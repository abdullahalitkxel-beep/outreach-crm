"""Team mode, the laptop side: connect this BSO to a team hub run by your manager.

Nothing here runs unless the person signs in to a team (the admin gives them an ID and password) and agrees to the disclosure.
What is shared with the hub: send / reply-label / bounce / draft events (lead email, company, step, which of
your inboxes sent it), and, only when your manager requires approval, the text of drafts waiting for approval.
Never shared: passwords, API keys, inbox credentials, the text of replies, your lead list, notes.
The hub's policy (paused, daily cap, approval mode) is enforced here before BSO sends.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from bot import DATA, SECRETS, env, log_event, now_utc, iso, set_stage

TOKEN_KEY = "BSO_TEAM_TOKEN"
STATE = Path(DATA) / "team.json"
CONSENT_VERSION = "1"
SHARED_TYPES = {"sent", "reply", "drafted", "approved", "rejected", "bounce", "error"}
DISCLOSURE = [
    "Your manager can see how many emails you send, which leads they go to (email address and company), which step, and from which of your inboxes.",
    "Your manager can see the label of each reply (for example “interested”), but not what the reply says.",
    "If your manager requires approval, they can read, edit and approve or reject your drafts before they send.",
    "Your manager can pause your account, set a daily send cap and a weekly target, and see when this laptop last connected.",
    "Never shared: your passwords, API keys, inbox logins, your lead list, your notes, or the text of replies.",
    "Your data stays on this laptop; your manager keeps the activity log already sent. If you stop working with the team, your admin removes your account.",
]


# ---------------------------------------------------------------- state

def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(s):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), encoding="utf-8")
    os.replace(tmp, STATE)


def joined():
    return bool(state().get("hub") and env(TOKEN_KEY))


def policy():
    return state().get("policy") or {}


def holds_approval():
    """True when the manager (not this person) approves drafts."""
    return joined() and policy().get("approval") == "master"


# ---------------------------------------------------------------- signing in

SIGNED_IN = {}          # lives only in this running app: who is signed in right now


def app_dir():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def built_in_hub():
    """The hub address built into this copy of BSO (hub_address.txt, bundled at build time)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    try:
        for line in (base / "hub_address.txt").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.rstrip("/")
    except OSError:
        pass
    return ""


def defaults():
    """Hub settings: a team-defaults.json next to BSO.exe (or in the data folder) wins; otherwise the built-in hub address."""
    for d in (app_dir(), Path(DATA)):
        try:
            return json.loads((d / "team-defaults.json").read_text(encoding="utf-8-sig"))
        except Exception:
            continue
    hub = built_in_hub()
    return {"hub": hub} if hub else {}


def hub_url():
    return (defaults().get("hub") or state().get("hub") or "").rstrip("/")


def required():
    """Every install opens on the sign-in screen and needs an ID and password from the admin.
    (BSO_NO_SIGNIN=1 is only for the automated build tests, which have no hub to sign in to.)"""
    if os.getenv("BSO_NO_SIGNIN") == "1":
        return False
    d = defaults()
    return d.get("required", True) is not False


def locked_in():
    return required()


def signed_in():
    return SIGNED_IN.get("user")


def sign_out():
    SIGNED_IN.clear()


def check_hub_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("Enter the hub address your admin gave you.")
    if "://" not in url:
        url = "https://" + url
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("That hub address isn't valid.")
    if u.scheme == "http" and not _private_host(u.hostname):
        raise ValueError("This hub is not using HTTPS. BSO only connects to a hub over the internet when it is encrypted (https://). Ask your admin to set up HTTPS.")
    return url


def _private_host(host):
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        try:
            return ipaddress.ip_address(socket.gethostbyname(host)).is_private
        except Exception:
            return False


def _post(url, path, payload, token=None, timeout=20):
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "BSO-team/1"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


LOCAL_HUB = "http://127.0.0.1:8780"


def _local_hub_running():
    try:
        with socket.create_connection(("127.0.0.1", 8780), timeout=0.5):
            return True
    except OSError:
        return False


def _verifier(username, password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", f"{username}\0{password}".encode(), bytes.fromhex(salt), 200_000).hex()
    return {"salt": salt, "hash": h}


def _verify_offline(s, username, password):
    v = s.get("verifier") or {}
    if not v or s.get("username") != username:
        return False
    return hmac.compare_digest(_verifier(username, password, v["salt"])["hash"], v["hash"])


def sign_in(con, username, password, consent=False, hub=None):
    """Sign in with the ID and password the admin created. Returns the policy. Raises ValueError with a friendly message."""
    username = (username or "").strip().lower()
    if not username or not password:
        raise ValueError("Enter your ID and password.")
    s = state()
    first_time = s.get("username") != username
    if first_time and not consent:
        raise ValueError("Tick the box to confirm you've read what your manager will be able to see.")
    url = check_hub_url(hub or hub_url())
    device = os.getenv("COMPUTERNAME") or socket.gethostname()
    try:
        try:
            status, j = _post(url, "/api/v1/login", {"username": username, "password": password, "device": device})
        except (urllib.error.URLError, OSError, TimeoutError):
            # on the computer that runs the master console itself, use it directly while the public address isn't live
            if url == LOCAL_HUB or not _local_hub_running():
                raise
            url = LOCAL_HUB
            status, j = _post(url, "/api/v1/login", {"username": username, "password": password, "device": device})
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # hub unreachable: allow the same person back in on this laptop for a limited time
        grace = int(((s.get("policy") or {}).get("grace_hours", 72)) or 0)
        try:
            recent = grace == 0 or now_utc() - datetime.fromisoformat(s.get("last_ok")).astimezone(timezone.utc) <= timedelta(hours=grace)
        except Exception:
            recent = False
        if _verify_offline(s, username, password) and recent and not s.get("revoked"):
            SIGNED_IN.update(user=username, name=(s.get("policy") or {}).get("name", username), offline=True)
            return s.get("policy") or {}
        raise ValueError(f"Can't reach the hub at {url}. Check your internet connection. ({getattr(e, 'reason', e)})")
    if status != 200 or "token" not in j:
        raise ValueError(j.get("error") or f"Sign-in failed (code {status}).")
    SECRETS.set(TOKEN_KEY, j["token"])
    pol = j.get("policy") or {}
    if first_time:
        last = con.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        s = {"joined_at": iso(now_utc()), "last_event_id": last, "drafts_sent": [], "ack": []}
    s.update(hub=url, username=username, policy=pol, consent=CONSENT_VERSION, last_ok=iso(now_utc()),
             verifier=_verifier(username, password), required=True)
    s.pop("revoked", None)
    save_state(s)
    SIGNED_IN.clear()
    SIGNED_IN.update(user=username, name=pol.get("name", username))
    return pol


def leave():
    """Only for installs the admin has not locked to a team."""
    sign_out()
    SECRETS.set(TOKEN_KEY, "")
    try:
        STATE.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- sync

def _step(detail):
    d = (detail or "").lower()
    if d.startswith("step "):
        try:
            return int(d.split()[1])
        except (IndexError, ValueError):
            return None
    return None


def _z(ts):
    """Local ISO timestamp -> the hub's 2026-01-31T09:05:00Z form."""
    try:
        return datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_events(con, after):
    out = []
    for e in con.execute(
            "SELECT e.id, e.type, e.detail, e.sender, e.at, e.variant, l.email, l.company FROM events e "
            "LEFT JOIN leads l ON l.id=e.lead_id WHERE e.id>? AND e.type IN (%s) ORDER BY e.id LIMIT 500"
            % ",".join("?" * len(SHARED_TYPES)), (after, *sorted(SHARED_TYPES))):
        detail = e["detail"] or ""
        if e["type"] == "error":
            detail = "send failed"          # raw error text can contain server details; keep it generic
        elif e["type"] == "reply":
            detail = detail if detail in ("interested", "not_interested", "unsubscribe", "out_of_office", "referral", "other") else "other"
        out.append({"id": e["id"], "type": e["type"], "detail": detail, "sender": e["sender"], "at": _z(e["at"]),
                    "lead_email": e["email"], "company": e["company"], "step": _step(e["detail"]), "variant": e["variant"]})
    return out


def _collect_drafts(con, sent_ids):
    if not holds_approval():
        return []
    q = ("SELECT o.id, o.step, o.sender, o.subject, o.body, l.email, l.company FROM outbox o JOIN leads l ON l.id=o.lead_id "
         "WHERE o.status='pending'")
    return [{"id": r["id"], "lead_email": r["email"], "company": r["company"], "step": r["step"] + 1, "sender": r["sender"],
             "subject": r["subject"], "body": r["body"]} for r in con.execute(q) if r["id"] not in sent_ids]


def _apply_decisions(con, decisions):
    done = []
    for d in decisions:
        o = con.execute("SELECT * FROM outbox WHERE id=?", (d["local_id"],)).fetchone()
        if o and o["status"] == "pending":
            when = iso(now_utc())
            if d["action"] == "approve":
                subject = d["subject"] if o["step"] == 0 and d.get("subject") else o["subject"]
                con.execute("UPDATE outbox SET status='approved', subject=?, body=?, decided_at=? WHERE id=?",
                            (subject, d.get("body") or o["body"], when, o["id"]))
                log_event(con, o["lead_id"], "approved", f"step {o['step'] + 1} (manager)")
            else:
                con.execute("UPDATE outbox SET status='rejected', decided_at=? WHERE id=?", (when, o["id"]))
                con.execute("UPDATE leads SET status='paused' WHERE id=?", (o["lead_id"],))
                log_event(con, o["lead_id"], "rejected", f"step {o['step'] + 1} (manager)")
        done.append(d["local_id"])
    con.commit()
    return done


def sync(con, version=""):
    """One round trip. Returns (ok, message). Never raises."""
    s = state()
    if not joined():
        return False, "Not in a team."
    try:
        events = _collect_events(con, s.get("last_event_id", 0))
        drafts = _collect_drafts(con, set(s.get("drafts_sent", [])))
        status, j = _post(s["hub"], "/api/v1/sync", {"events": events, "drafts": drafts, "acked": s.get("ack", []), "version": version},
                          token=env(TOKEN_KEY))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, f"Can't reach the hub ({getattr(e, 'reason', e)})."
    if status == 401:
        s["revoked"] = True
        save_state(s)
        return False, "Your manager disconnected this laptop. Ask for a new invite."
    if status != 200:
        return False, f"The hub returned an error ({status})."
    s = state() or s
    s.pop("revoked", None)
    if events:
        s["last_event_id"] = events[-1]["id"]
    s["drafts_sent"] = sorted(set(s.get("drafts_sent", [])) | {d["id"] for d in drafts})[-2000:]
    s["policy"] = j.get("policy") or s.get("policy", {})
    s["last_ok"] = iso(now_utc())
    s["ack"] = _apply_decisions(con, j.get("decisions") or [])
    save_state(s)
    return True, "Synced."


# ---------------------------------------------------------------- enforcement used by bot.py

def gate(con, version=""):
    """Called before BSO sends. Returns (allowed, reason, daily_cap or 0)."""
    if not joined():
        if required():
            return False, "Sign in to BSO with your ID and password first.", 0
        return True, "", 0
    sync(con, version)
    s, p = state(), policy()
    if s.get("revoked"):
        return False, "Your manager disconnected this laptop from the team. Open Settings → Team.", 0
    if not p.get("enabled", True):
        return False, p.get("message") or "Your account is paused by your manager.", 0
    grace = int(p.get("grace_hours", 72) or 0)
    if grace:
        try:
            last = datetime.fromisoformat(s.get("last_ok")).astimezone(timezone.utc)
            if now_utc() - last > timedelta(hours=grace):
                return False, f"BSO hasn't been able to reach your manager's hub for over {grace} hours, so sending is paused. Check your internet connection.", 0
        except Exception:
            pass
    return True, "", int(p.get("daily_cap") or 0)


def sent_today_total(con):
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    return con.execute("SELECT COUNT(*) FROM events WHERE type='sent' AND at>=? AND detail NOT LIKE '%(test)%'", (iso(start),)).fetchone()[0]
