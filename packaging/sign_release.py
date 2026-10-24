"""Signs a release for automatic updates (run by the GitHub build after the installer is built and code-signed).

    python packaging/sign_release.py dist/BSO-Setup.exe <version>

Reads the private key from UPDATE_SIGNING_KEY (and UPDATE_SIGNING_KEY_PASSWORD if it has one) and writes, next to
the installer:
  BSO-Setup.exe.sig        signature of the installer (what BSO 1.0.x checks)
  BSO-release.json         {"version": ..., "sha256": ...}: the version and the installer's fingerprint
  BSO-release.json.sig     signature of that manifest (what BSO 1.1+ checks: an old installer can't be republished
                           under a newer version number)
Then checks both with the app's own verify() against update_key.json. Never prints the key.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(installer, version):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    pem = (os.getenv("UPDATE_SIGNING_KEY") or "").strip()
    if not pem:
        print("::warning title=Automatic updates::UPDATE_SIGNING_KEY secret not set: this release isn't signed, so BSOs won't install it automatically")
        return 0
    pw = os.getenv("UPDATE_SIGNING_KEY_PASSWORD") or None
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=pw.encode() if pw else None)
    except Exception as e:
        print(f"::error title=Release signature::UPDATE_SIGNING_KEY can't be used ({type(e).__name__}). Run the 'Check release secrets' workflow for details.")
        return 1
    exe = Path(installer)
    data = exe.read_bytes()
    sign = lambda b: key.sign(b, padding.PKCS1v15(), hashes.SHA256())    # noqa: E731
    exe.with_name(exe.name + ".sig").write_bytes(sign(data))
    manifest = json.dumps({"version": version, "sha256": hashlib.sha256(data).hexdigest()}).encode()
    m = exe.with_name("BSO-release.json")
    m.write_bytes(manifest)
    m.with_name(m.name + ".sig").write_bytes(sign(manifest))
    import updater
    ok = updater.verify(data, exe.with_name(exe.name + ".sig").read_bytes()) and updater.verify(manifest, m.with_name(m.name + ".sig").read_bytes())
    if not ok:
        print("::error title=Release signature::UPDATE_SIGNING_KEY is a valid key but not the one in update_key.json")
        return 1
    print(f"release {version} signed: installer and manifest verify against update_key.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
