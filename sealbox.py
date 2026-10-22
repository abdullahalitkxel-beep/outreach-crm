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


# ---------------------------------------------------------------- RSA-OAEP (SHA-256), standard library only
# The Master Console has a key pair; its public key goes to every BSO. Each BSO picks its own random key, sends it to
# the console sealed with the public key (only the console can open it), and from then on seals its reports with it.

def _is_probable_prime(n, rounds=40):
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(c):
            return c


def rsa_keypair(bits=2048):
    e = 65537
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        try:
            d = pow(e, -1, phi)
        except ValueError:
            continue
        n = p * q
        if n.bit_length() == bits:
            return {"n": format(n, "x"), "e": e}, {"n": format(n, "x"), "d": format(d, "x")}


def _mgf1(seed, length):
    out, c = b"", 0
    while len(out) < length:
        out += hashlib.sha256(seed + c.to_bytes(4, "big")).digest()
        c += 1
    return out[:length]


def rsa_encrypt(pub, message, label=b""):
    n, e = int(pub["n"], 16), int(pub["e"])
    k = (n.bit_length() + 7) // 8
    hlen = 32
    if len(message) > k - 2 * hlen - 2:
        raise ValueError("message too long")
    lhash = hashlib.sha256(label).digest()
    db = lhash + b"\x00" * (k - len(message) - 2 * hlen - 2) + b"\x01" + message
    seed = secrets.token_bytes(hlen)
    masked_db = bytes(a ^ b for a, b in zip(db, _mgf1(seed, k - hlen - 1)))
    masked_seed = bytes(a ^ b for a, b in zip(seed, _mgf1(masked_db, hlen)))
    em = b"\x00" + masked_seed + masked_db
    return base64.urlsafe_b64encode(pow(int.from_bytes(em, "big"), e, n).to_bytes(k, "big")).decode()


def rsa_decrypt(priv, ciphertext, label=b""):
    n, d = int(priv["n"], 16), int(priv["d"], 16)
    k = (n.bit_length() + 7) // 8
    hlen = 32
    c = int.from_bytes(base64.urlsafe_b64decode(ciphertext.encode()), "big")
    em = pow(c, d, n).to_bytes(k, "big")
    masked_seed, masked_db = em[1:1 + hlen], em[1 + hlen:]
    seed = bytes(a ^ b for a, b in zip(masked_seed, _mgf1(masked_db, hlen)))
    db = bytes(a ^ b for a, b in zip(masked_db, _mgf1(seed, k - hlen - 1)))
    if em[0] != 0 or not hmac.compare_digest(db[:hlen], hashlib.sha256(label).digest()):
        raise ValueError("decryption failed")
    rest = db[hlen:].lstrip(b"\x00")
    if not rest.startswith(b"\x01"):
        raise ValueError("decryption failed")
    return rest[1:]
