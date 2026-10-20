"""Small authenticated encryption with the Python standard library only (no extra packages to ship).

Used to send a person's BSO setup (inbox passwords, API keys) from the master console to their laptop
through the Google relay without Google, or anyone else holding the files, being able to read it.

    seal(key, bytes) -> str          encrypt-then-MAC: HMAC-SHA256 counter-mode keystream + HMAC-SHA256 tag
    open_(key, str) -> bytes         raises ValueError if the key is wrong or the data was changed
    wrap(secret, username, password) / unwrap(...)   protect a key with a sign-in password (PBKDF2-SHA256)
"""
import base64
import hashlib
import hmac
import secrets

ITERATIONS = 300_000


def _sub(key, label):
    return hmac.new(key, label, hashlib.sha256).digest()


def _stream(key, nonce, n):
    out, i = bytearray(), 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def seal(key, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(_sub(key, b"enc"), nonce, len(data))))
    tag = hmac.new(_sub(key, b"mac"), b"v1" + nonce + ct, hashlib.sha256).digest()
    return "v1." + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def open_(key, box):
    if not str(box).startswith("v1."):
        raise ValueError("unknown format")
    raw = base64.urlsafe_b64decode(str(box)[3:].encode())
    if len(raw) < 48:
        raise ValueError("too short")
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    if not hmac.compare_digest(tag, hmac.new(_sub(key, b"mac"), b"v1" + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("wrong key or damaged")
    return bytes(a ^ b for a, b in zip(ct, _stream(_sub(key, b"enc"), nonce, len(ct))))


def _pw_key(username, password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", f"bso-setup\0{username.strip().lower()}\0{password}".encode(), salt, iterations)


def wrap(secret, username, password):
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "iter": ITERATIONS, "box": seal(_pw_key(username, password, salt, ITERATIONS), secret)}


def unwrap(w, username, password):
    return open_(_pw_key(username, password, bytes.fromhex(w["salt"]), int(w.get("iter") or ITERATIONS)), w["box"])
