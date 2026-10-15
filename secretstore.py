"""
Encrypted storage for passwords and API keys.

On Windows, secrets are encrypted with DPAPI (CryptProtectData): only the same Windows user
on the same computer can decrypt them. They are kept in <data folder>/secrets.dat, never in
plain text. Older plain-text .env files are migrated automatically and then blanked.

On other systems (development only) secrets are stored with owner-only file permissions.
"""
import base64
import json
import os
import sys
from pathlib import Path

ENTROPY = b"BlacSwanken.OutreachCRM.v1"
_PLAIN_MARK = b"PLAIN1:"


# ---------------------------------------------------------------- DPAPI (Windows)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data):
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _protect(data):
        blob_in, _b1 = _blob(data)
        ent, _b2 = _blob(ENTROPY)
        out = _BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "OutreachCRM", ctypes.byref(ent),
                                                      None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("Windows could not encrypt the secret (CryptProtectData failed)")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def _unprotect(data):
        blob_in, _b1 = _blob(data)
        ent, _b2 = _blob(ENTROPY)
        out = _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(ent),
                                                        None, None, 0x1, ctypes.byref(out)):
            raise OSError("Windows could not decrypt the saved secrets (were they created by another Windows user?)")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    ENCRYPTED = True
else:
    def _protect(data):
        return _PLAIN_MARK + data

    def _unprotect(data):
        return data[len(_PLAIN_MARK):] if data.startswith(_PLAIN_MARK) else data

    ENCRYPTED = False


# ---------------------------------------------------------------- store

class SecretStore:
    def __init__(self, folder):
        self.path = Path(folder) / "secrets.dat"

    def load(self):
        if not self.path.exists():
            return {}
        raw = base64.b64decode(self.path.read_bytes())
        return json.loads(_unprotect(raw).decode("utf-8"))

    def save(self, data):
        blob = base64.b64encode(_protect(json.dumps(data).encode("utf-8")))
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        if not ENCRYPTED:
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def set(self, key, value):
        data = self.load()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        self.save(data)
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

    def apply_to_environ(self):
        """Make saved secrets available to the app (without overriding real environment variables)."""
        try:
            for k, v in self.load().items():
                os.environ.setdefault(k, v)
        except Exception as e:  # unreadable store must never crash the app
            print("secrets:", e, file=sys.stderr)

    def migrate_env_file(self, env_path):
        """Move secrets out of a plain-text .env file into the encrypted store, then blank them."""
        env_path = Path(env_path)
        if not env_path.exists():
            return 0
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
        data, kept, moved = self.load(), [], 0
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if v and "..." not in v and not v.lower().startswith("your"):
                    data[k] = v
                    moved += 1
                    continue
            kept.append(line)
        if moved:
            self.save(data)
            kept.insert(0, "# Secrets are now stored encrypted (secrets.dat). Edit them in the app: Settings.")
            env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return moved
