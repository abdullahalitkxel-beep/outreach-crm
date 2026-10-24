"""Automatic updates from GitHub Releases, and the BlacSwanken publisher certificate.

- Checks the latest release every 6 hours (and at start). Only a release that carries a valid signature made by
  BlacSwanken's build system (BSO-Setup.exe.sig, RSA/SHA-256) is ever installed.
- Before installing: the database is backed up. The installer runs silently for this Windows user only, BSO
  restarts on its own, and if the new version hasn't started properly within 5 minutes the previous version is
  put back.
- Staged roll-out: the owner's and managers' BSOs first, supervisors 6 hours later, the BSO Bot 24 hours later and
  only when no email is due in the next 15 minutes. The Master Console can hold a release, release it to everyone
  now, or set a minimum version (urgent fixes).
- Supervisors see a banner (Update now / tonight); after 3 days BSO updates by itself at a quiet moment.
- On joining a team, BSO trusts the BlacSwanken publisher certificate for this Windows user, so its own signed
  installers show a verified publisher.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import bot
from bot import DATA, ROOT, iso, now_utc

# Releases are published to a public repository that holds only the installers, so the source code can stay private.
# The code repository is still asked as a fallback (laptops that updated before the releases repository existed).
REPOS = [r for r in (os.getenv("BSO_UPDATE_REPO"), "abdullahalitkxel-beep/bso-releases", "abdullahalitkxel-beep/outreach-crm") if r]
REPO = REPOS[0]
API = f"https://api.github.com/repos/{REPO}/releases/latest"
STATE = Path(DATA) / "update.json"
DIR = Path(DATA) / "updates"
CHECK_EVERY = 6 * 3600
DELAY_HOURS = {"owner": 0, "manager": 0, "supervisor": 6, "member": 6, "bot": 24}
SILENT = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER"


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(s):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), encoding="utf-8")
    os.replace(tmp, STATE)


def current():
    try:
        import legal
        return legal.app_version()
    except Exception:
        return "0"


def ver(t):
    out = []
    for p in str(t or "").lstrip("vV").split("."):
        try:
            out.append(int("".join(ch for ch in p if ch.isdigit()) or 0))
        except ValueError:
            out.append(0)
    return tuple(out + [0] * (3 - len(out)))


# ---------------------------------------------------------------- signature check (no extra libraries)

def _key():
    try:
        k = json.loads((ROOT / "update_key.json").read_text(encoding="utf-8"))
        return int(k["n"], 16), int(k.get("e", 65537))
    except Exception:
        return None


SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def verify(data, sig, key=None):
    """RSA PKCS#1 v1.5 with SHA-256, as made by `openssl dgst -sha256 -sign`."""
    key = key or _key()
    if not key or not sig:
        return False
    n, e = key
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    m = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(SHA256_PREFIX) - 32) + b"\x00" + SHA256_PREFIX + hashlib.sha256(data).digest()
    return m == want


# ---------------------------------------------------------------- what's out there

MANIFEST = "BSO-release.json"          # {"version": ..., "sha256": <of BSO-Setup.exe>}, signed as BSO-release.json.sig


def _small(url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "BSO-updater"})
    r.raise_for_status()
    return r.content


def _signed_manifest(rel):
    """The release's signed manifest if it checks out: the signature is BlacSwanken's and it names this release's
    version, so an old installer can't be republished under a newer number. None otherwise."""
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
    if not (assets.get("BSO-Setup.exe") and assets.get(MANIFEST) and assets.get(MANIFEST + ".sig")):
        return None
    try:
        body, sig = _small(assets[MANIFEST]), _small(assets[MANIFEST + ".sig"])
        if not verify(body, sig):
            return None
        m = json.loads(body)
        if str(m.get("version")) != str(rel.get("tag_name", "")).lstrip("v") or len(str(m.get("sha256", ""))) != 64:
            return None
        return {"manifest": body.decode(), "manifest_sig": sig.hex(), "sha256": m["sha256"], "setup_url": assets["BSO-Setup.exe"]}
    except Exception:
        return None


def check(force=False):
    """Ask GitHub for the latest release (at most every 6 hours unless forced). Returns banner().
    Every repository in REPOS is asked; the newest release whose signed manifest checks out wins. A release without
    one (or with a bad one) is skipped, so a broken or planted release can't block updates or downgrade anyone."""
    s = _state()
    if not force and s.get("checked_at") and time.time() - s["checked_at"] < CHECK_EVERY:
        return banner()
    try:
        found, errs = [], []
        for repo in REPOS:
            try:
                r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "BSO-updater"}, timeout=20)
                r.raise_for_status()
                got = r.json()
                if got.get("tag_name"):
                    found.append(got)
            except Exception as e:
                errs.append(str(e)[:80])
        if not found:
            raise RuntimeError("; ".join(errs) or "no release found")
        found.sort(key=lambda x: ver(x["tag_name"].lstrip("v")), reverse=True)
        pick, sm = None, None
        for rel in found:
            sm = _signed_manifest(rel)
            if sm:
                pick = rel
                break
        if not pick:
            s.update(checked_at=time.time(), latest=None, error="")
            _save(s)
            return banner()
        s.update(latest=pick.get("tag_name", "").lstrip("v"), published_at=pick.get("published_at"), notes=(pick.get("body") or "")[:4000],
                 setup_url=sm["setup_url"], manifest=sm["manifest"], manifest_sig=sm["manifest_sig"], sha256=sm["sha256"],
                 sig_url=None, checked_at=time.time(), error="")
        if ver(s["latest"]) > ver(current()) and not s.get("first_seen", {}).get(s["latest"]):
            s.setdefault("first_seen", {})[s["latest"]] = iso(now_utc())
    except Exception as e:
        s.update(checked_at=time.time(), error=f"Couldn't check for updates ({str(e)[:120]}).")
    _save(s)
    return banner()


def _role():
    try:
        import team
        if team.joined():
            return (team.policy() or {}).get("role") or "member"
    except Exception:
        pass
    return "owner"


def _control():
    try:
        import team
        return team.state().get("updates") or {}
    except Exception:
        return {}


def decision(s=None, now=None):
    """(allowed now?, forced?, why)."""
    s = s if s is not None else _state()
    now = now or now_utc()
    latest = s.get("latest")
    if not latest or ver(latest) <= ver(current()):
        return False, False, "You have the latest version."
    if not _manifest_ok(s):
        return False, False, f"Version {latest} isn't signed by BlacSwanken's build system, so it won't be installed."
    ctl = _control()
    forced = bool(ctl.get("min_version")) and ver(current()) < ver(ctl["min_version"])
    if forced:
        return True, True, f"Your manager requires version {ctl['min_version']} or newer."
    if ctl.get("hold"):
        return False, False, "Your manager is holding this release while it's tested."
    try:
        published = datetime.fromisoformat(str(s.get("published_at")).replace("Z", "+00:00"))
    except Exception:
        published = now
    if ctl.get("release_now"):
        try:
            if datetime.fromisoformat(str(ctl["release_now"]).replace("Z", "+00:00")) >= published:
                return True, False, "Released to everyone by your manager."
        except Exception:
            pass
    wait = DELAY_HOURS.get(_role(), 6)
    if now - published < timedelta(hours=wait):
        return False, False, f"Staged roll-out: this BSO gets version {latest} {wait} hours after its release."
    return True, False, ""


def banner():
    s = _state()
    latest = s.get("latest")
    avail = bool(latest) and ver(latest) > ver(current())
    ok, forced, why = decision(s)
    return {"available": avail, "latest": latest, "notes": s.get("notes", ""), "allowed": ok, "forced": forced,
            "message": s.get("error") or why or ("" if avail else "You have the latest version."), "rolled_back": s.get("rolled_back")}


# ---------------------------------------------------------------- installing

def _download(url, dest):
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "BSO-updater"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def _manifest_ok(s):
    try:
        m = json.loads(s.get("manifest") or "{}")
        return (verify((s.get("manifest") or "").encode(), bytes.fromhex(s.get("manifest_sig") or "")) and
                str(m.get("version")) == str(s.get("latest")) and m.get("sha256") == s.get("sha256"))
    except Exception:
        return False


def fetch(s=None):
    """Download the latest installer and check it is exactly the one BlacSwanken signed. Returns its path. Raises RuntimeError."""
    s = s or _state()
    if not _manifest_ok(s):
        raise RuntimeError("This release isn't signed by BlacSwanken's build system; it won't be installed.")
    DIR.mkdir(parents=True, exist_ok=True)
    exe = DIR / f"BSO-Setup-{s['latest']}.exe"
    if not exe.exists():
        _download(s["setup_url"], exe)
    if hashlib.sha256(exe.read_bytes()).hexdigest() != s["sha256"]:
        exe.unlink(missing_ok=True)
        raise RuntimeError("The download's signature doesn't match BlacSwanken's build key; it was deleted and not installed.")
    return exe


def backup_db():
    src = Path(DATA) / "outreach.db"
    if not src.exists():
        return None
    out = Path(DATA) / "backups"
    out.mkdir(exist_ok=True)
    dest = out / f"pre-update-{current()}-{datetime.now().strftime('%Y%m%d-%H%M')}.db"
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dest))
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    return dest


def _previous_installer():
    """The installer of the version running now, for going back if the new one fails (checked against its signed manifest)."""
    old = DIR / f"BSO-Setup-{current()}.exe"
    if old.exists():
        return old
    for repo in REPOS:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/v{current()}", timeout=20, headers={"User-Agent": "BSO-updater"})
            sm = _signed_manifest(r.json())
            if not sm:
                continue
            DIR.mkdir(parents=True, exist_ok=True)
            _download(sm["setup_url"], old)
            if hashlib.sha256(old.read_bytes()).hexdigest() == sm["sha256"]:
                return old
            old.unlink(missing_ok=True)
        except Exception:
            pass
    return None


def helper_script(new_exe, old_exe, app_exe, new_ver, background):
    flag = DIR / f"healthy-{new_ver}.flag"
    args = "--background" if background else ""
    return f"""$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 3
Start-Process -FilePath '{new_exe}' -ArgumentList '{SILENT}' -Wait
Start-Process -FilePath '{app_exe}' {f"-ArgumentList '{args}'" if args else ''}
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {{ if (Test-Path '{flag}') {{ exit 0 }}; Start-Sleep -Seconds 10 }}
{f'''Get-Process BSO -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath '{old_exe}' -ArgumentList '{SILENT}' -Wait
Set-Content -Path '{DIR / "rolled-back.flag"}' -Value '{new_ver}'
Start-Process -FilePath '{app_exe}' {f"-ArgumentList '{args}'" if args else ''}''' if old_exe else ''}
"""


def install_now(reason=""):
    """Download, verify, back up and hand over to the installer. Returns (started?, message)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False, "Updates install on the Windows app only (this copy runs from source)."
    s = _state()
    ok, forced, why = decision(s)
    if not ok and not (reason == "started by hand" and why.startswith("Staged roll-out")):
        return False, why                      # a hold, or an unsigned release, also stops a manual update
    if not s.get("latest") or ver(s["latest"]) <= ver(current()):
        return False, "You have the latest version."
    try:
        exe = fetch(s)
    except Exception as e:
        return False, str(e)
    backup_db()
    old = _previous_installer()
    background = _role() == "bot" or "--background" in sys.argv
    ps1 = DIR / "update.ps1"
    ps1.write_text(helper_script(exe, old, sys.executable, s["latest"], background), encoding="utf-8")
    s.update(installing=s["latest"], installing_from=current(), installing_at=iso(now_utc()), reason=reason)
    _save(s)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)],
                     creationflags=flags, close_fds=True)
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return True, f"Updating to {s['latest']}: BSO closes for about a minute and opens again."


def mark_healthy():
    """Called a minute after the app starts: tells the update helper the new version runs."""
    s = _state()
    rb = DIR / "rolled-back.flag"
    if rb.exists():
        s["rolled_back"] = rb.read_text(encoding="utf-8").strip()
        rb.unlink(missing_ok=True)
        s.pop("installing", None)
        _save(s)
        return "rolled back"
    if s.get("installing") and ver(current()) >= ver(s["installing"]):
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / f"healthy-{current()}.flag").write_text(iso(now_utc()), encoding="utf-8")
        s["updated_at"] = iso(now_utc())
        s.pop("installing", None)
        s.pop("tonight", None)
        _save(s)
        return "healthy"
    return None


def set_tonight(on=True):
    s = _state()
    s["tonight"] = bool(on)
    _save(s)


def _bot_quiet(con):
    """No email due on the bot in the next 15 minutes."""
    soon = iso(now_utc() + timedelta(minutes=15))
    n = con.execute("SELECT COUNT(*) FROM leads WHERE status='active' AND next_send_at<=?", (soon,)).fetchone()[0]
    running = con.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone()
    return n == 0 and not running


def tick(idle_minutes=None, con=None, now=None):
    """One decision: install now or not. Returns a reason string when it starts an install."""
    s = _state()
    ok, forced, why = decision(s, now)
    if not ok:
        return None
    role = _role()
    local = (now or now_utc()).astimezone()
    if role == "bot":
        con = con or bot.db_connect("outreach.db")
        if forced or _bot_quiet(con):
            return install_now("automatic (bot)")[1]
        return None
    quiet = idle_minutes is None or idle_minutes >= 30
    if s.get("tonight") and 1 <= local.hour < 5:
        return install_now("tonight, as asked")[1]
    seen = (s.get("first_seen") or {}).get(s.get("latest"))
    try:
        old = seen and (now or now_utc()) - datetime.fromisoformat(seen) > timedelta(days=3)
    except Exception:
        old = False
    if (forced or old) and quiet:
        return install_now("automatic after 3 days" if not forced else "required by your manager")[1]
    return None


def loop(idle_fn=lambda: None):
    """Background: check every 6 hours, decide every 10 minutes."""
    def run():
        time.sleep(60)
        try:
            mark_healthy()
        except Exception as e:
            print("update health:", e)
        while True:
            try:
                check()
                tick(idle_fn())
            except Exception as e:
                print("updater:", e)
            time.sleep(600)
    threading.Thread(target=run, daemon=True, name="updater").start()


# ---------------------------------------------------------------- the publisher certificate

CERT = "bso-publisher.cer"


def cert_path():
    p = ROOT / "assets" / CERT
    return p if p.exists() else None


def trust_certificate():
    """Trust BlacSwanken's code-signing certificate for this Windows user (Trusted Publishers and Root).
    Windows asks once to confirm the root certificate. Returns (ok, message)."""
    p = cert_path()
    if sys.platform != "win32" or not p:
        return False, "Not on Windows, or no certificate bundled."
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = []
    for store in ("TrustedPublisher", "Root"):
        r = subprocess.run(["certutil", "-user", "-f", "-addstore", store, str(p)], capture_output=True, text=True, creationflags=flags)
        res.append(r.returncode == 0)
    ok = all(res)
    try:
        import team
        st = team.state()
        st["cert_trusted"] = ok
        team.save_state(st)
    except Exception:
        pass
    return ok, "Publisher certificate trusted." if ok else "Windows didn't add the certificate (the confirmation may have been declined)."


def untrust_certificate():
    p = cert_path()
    if sys.platform != "win32" or not p:
        return
    try:
        thumb = hashlib.sha1(p.read_bytes()).hexdigest()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for store in ("TrustedPublisher", "Root"):
            subprocess.run(["certutil", "-user", "-delstore", store, thumb], capture_output=True, creationflags=flags)
    except Exception:
        pass
