"""Talking to the BSO team relay: a Google Apps Script web app (relay/Code.gs) that the master deploys
once under their own Google account. Used by the master console (hub.py) and by team laptops (team.py)."""
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path

ITERATIONS = 1500
SCRIPT_TEMPLATE = Path(__file__).resolve().parent / "relay" / "Code.gs"


class RelayError(Exception):
    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


def is_relay(url):
    url = (url or "").lower()
    return "script.google.com/macros/" in url or url.rstrip("/").endswith("/exec")


def password_hash(password, salt_hex, iterations=ITERATIONS):
    """Same calculation as passwordHash_ in Code.gs (iterated HMAC-SHA256)."""
    key = bytes.fromhex(salt_hex)
    h = hmac.new(key, password.encode("utf-8"), hashlib.sha256).digest()
    for _ in range(iterations - 1):
        h = hmac.new(key, h, hashlib.sha256).digest()
    return h.hex()


def new_password_record(password):
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": password_hash(password, salt), "iter": ITERATIONS}


def script_text(admin_key):
    """Code.gs with this master console's key fingerprint filled in."""
    base = SCRIPT_TEMPLATE
    if not base.exists():                       # packaged app: data files sit next to the modules
        import sys
        base = Path(getattr(sys, "_MEIPASS", ".")) / "relay" / "Code.gs"
    return base.read_text(encoding="utf-8").replace("__ADMIN_KEY_HASH__", hashlib.sha256(admin_key.encode()).hexdigest())


def call(url, payload, timeout=30):
    """POST one action to the relay and return its JSON. Raises RelayError (code 0 = can't reach it)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "BSO/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # follows Google's redirect to the result
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RelayError(f"The relay answered with an error ({e.code}).", e.code)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise RelayError(f"Can't reach the team relay ({getattr(e, 'reason', e)}).", 0)
    try:
        j = json.loads(raw or b"{}")
    except ValueError:
        # Google shows an HTML page when the deployment isn't public or the script has an error
        raise RelayError("The relay address doesn't answer like a BSO relay. Check the deployment is a Web app with access for \"Anyone\".", 502)
    if isinstance(j, dict) and j.get("error"):
        raise RelayError(j["error"], int(j.get("code") or 500))
    return j
