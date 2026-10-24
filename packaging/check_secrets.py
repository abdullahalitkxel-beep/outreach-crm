"""Checks the GitHub secrets a release needs, in seconds, before the long build starts.

    python packaging/check_secrets.py            (reads the secrets from environment variables)

Each problem is printed as a GitHub error naming the secret and what to do. Exit code 1 if any secret is wrong.
Needs the 'cryptography' package (pip install cryptography).
"""
import base64
import binascii
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("BSO_ROOT") or Path(__file__).resolve().parent.parent)
RELEASES_REPO = "abdullahalitkxel-beep/bso-releases"
problems, notes = [], []


def bad(secret, why):
    problems.append(f"{secret}: {why}")


def check_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    b64 = (os.getenv("CODESIGN_PFX_BASE64") or "").strip()
    pw = os.getenv("CODESIGN_PFX_PASSWORD") or ""
    if not b64:
        notes.append("CODESIGN_PFX_BASE64 is not set: the program and installer will be unsigned")
        return
    try:
        raw = base64.b64decode("".join(b64.split()), validate=True)
    except (binascii.Error, ValueError):
        return bad("CODESIGN_PFX_BASE64", "isn't valid base64 text. Paste the whole content of 2_CODESIGN_PFX_BASE64.txt again (Ctrl+A, Ctrl+C)")
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(raw, pw.encode() if pw else None)
    except ValueError as e:
        msg = str(e).lower()
        if "password" in msg or "mac" in msg or "decrypt" in msg:
            return bad("CODESIGN_PFX_PASSWORD", "doesn't open the certificate. Paste the content of 3_CODESIGN_PFX_PASSWORD.txt again")
        return bad("CODESIGN_PFX_BASE64", f"isn't a certificate file ({e}). Paste 2_CODESIGN_PFX_BASE64.txt again")
    if not key or not cert:
        return bad("CODESIGN_PFX_BASE64", "the file has no private key or no certificate")
    now = datetime.now(timezone.utc)
    if cert.not_valid_after_utc < now:
        bad("CODESIGN_PFX_BASE64", f"the certificate expired on {cert.not_valid_after_utc:%Y-%m-%d}")
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if x509.oid.ExtendedKeyUsageOID.CODE_SIGNING not in eku:
            bad("CODESIGN_PFX_BASE64", "the certificate isn't for code signing")
    except x509.ExtensionNotFound:
        bad("CODESIGN_PFX_BASE64", "the certificate isn't marked for code signing")
    bundled = ROOT / "assets" / "bso-publisher.cer"
    if bundled.exists() and bundled.read_bytes() != cert.public_bytes(serialization.Encoding.DER):
        bad("CODESIGN_PFX_BASE64", "is a different certificate from the one BSO trusts (assets/bso-publisher.cer). "
                                   "Use the certificate made together with this version, or update the bundled one")
    notes.append(f"certificate OK: {cert.subject.rfc4514_string()}, valid until {cert.not_valid_after_utc:%Y-%m-%d}")


def check_update_key():
    from cryptography.hazmat.primitives import serialization
    pem = (os.getenv("UPDATE_SIGNING_KEY") or "").strip()
    if not pem:
        notes.append("UPDATE_SIGNING_KEY is not set: BSOs won't install this release automatically")
        return
    if "BEGIN" not in pem:
        return bad("UPDATE_SIGNING_KEY", "isn't a private key (no BEGIN line). Paste the whole content of 1_UPDATE_SIGNING_KEY.txt again")
    pw = os.getenv("UPDATE_SIGNING_KEY_PASSWORD") or None
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=pw.encode() if pw else None)
    except TypeError:
        return bad("UPDATE_SIGNING_KEY", "is protected by a password: paste the key without a password (1_UPDATE_SIGNING_KEY.txt), "
                                         "or set UPDATE_SIGNING_KEY_PASSWORD")
    except ValueError as e:
        if pw:
            return bad("UPDATE_SIGNING_KEY_PASSWORD", f"doesn't open the key, or the key is damaged ({e})")
        return bad("UPDATE_SIGNING_KEY", f"can't be read ({e}). Paste the whole content of 1_UPDATE_SIGNING_KEY.txt again")
    want = int(json.loads((ROOT / "update_key.json").read_text(encoding="utf-8"))["n"], 16)
    got = getattr(key.public_key().public_numbers(), "n", None)
    if got != want:
        return bad("UPDATE_SIGNING_KEY", "is a valid key but not the one BSO trusts (update_key.json). Use the key made together with this version")
    notes.append(f"update key OK ({want.bit_length()}-bit, matches update_key.json)")


def check_releases_token():
    tok = (os.getenv("RELEASES_TOKEN") or "").strip()
    if not tok:
        notes.append(f"RELEASES_TOKEN is not set: releases go only to this repository (keep it public until {RELEASES_REPO} is set up)")
        return
    req = urllib.request.Request(f"https://api.github.com/repos/{RELEASES_REPO}",
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "BSO-check"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return bad("RELEASES_TOKEN", "GitHub doesn't accept this token (wrong, expired or revoked). Make a new one")
        if e.code == 404:
            return bad("RELEASES_TOKEN", f"can't see {RELEASES_REPO}: the repository doesn't exist yet, or the token wasn't given access to it")
        return bad("RELEASES_TOKEN", f"GitHub answered {e.code}")
    except (urllib.error.URLError, OSError) as e:
        notes.append(f"couldn't reach GitHub to check RELEASES_TOKEN ({e}); the publish step will tell")
        return
    if j.get("private"):
        bad("RELEASES_TOKEN", f"{RELEASES_REPO} is private: laptops can't download updates from it. Make that repository public")
    perms = j.get("permissions") or {}
    if perms and not perms.get("push"):
        bad("RELEASES_TOKEN", f"can read {RELEASES_REPO} but not publish to it: give the token Contents: Read and write")
    if not [p for p in problems if p.startswith("RELEASES_TOKEN")]:
        notes.append(f"releases token OK: can publish to {RELEASES_REPO}")


def main():
    for fn in (check_certificate, check_update_key, check_releases_token):
        try:
            fn()
        except Exception as e:                       # a bug here must not hide the other checks
            problems.append(f"{fn.__name__}: the check itself failed ({e})")
    for n in notes:
        print(n)
    gh = os.getenv("GITHUB_ACTIONS") == "true"
    for p in problems:
        print(f"::error title=Secret check::{p}" if gh else f"PROBLEM {p}")
    if not problems:
        print(("::notice title=Secret check::" if gh else "") + "All release secrets are correct")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
