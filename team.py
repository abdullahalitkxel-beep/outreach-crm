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
import re
import secrets
import socket
import sys
import time
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import relay
from bot import add_task, DATA, SECRETS, env, log_event, now_utc, iso, set_stage

TOKEN_KEY = "BSO_TEAM_TOKEN"
STATE = Path(DATA) / "team.json"
CONSENT_VERSION = "2"
SHARED_TYPES = {"sent", "reply", "drafted", "approved", "rejected", "bounce", "error", "handover", "handover_in", "prospect_stage", "deal", "incident", "temperature"}
PROSPECT_STAGES = ["new", "contacted", "meeting", "met", "proposal", "negotiation", "won", "lost"]
DISCLOSURE = [
    "All leads and all conversations with clients are company property. Your company sees everything BSO handles, in full.",
    "That includes every email sent and received in a lead's conversation (text, To, CC and BCC), lead lists by campaign, notes, tasks, "
    "reply temperatures and run summaries. What was already on this computer is copied once into the company archive.",
    "If you are a lead gen supervisor: your work mailbox is connected so that every conversation with a prospect (in any thread, with any of "
    "their addresses or colleagues) is tracked, including emails sent from other apps. Copying a client email to an address outside the "
    "company and the client is blocked in BSO and reported; deleting a client email is reported (the company keeps its copy).",
    "Your manager can approve, edit or reject drafts, pause your account, set limits, and see when this computer last connected, "
    "when you export or view large lists, and whether its drive is encrypted.",
    "If you leave the team, BSO hands its leads over to your colleagues and then erases its data from this computer.",
    "Never shared: your email passwords, API keys and inbox logins. Everything sent to the company is encrypted on the way.",
]


# ---------------------------------------------------------------- state

def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_state_lock = threading.RLock()
_sync_lock = threading.Lock()


def save_state(s):
    with _state_lock:                 # background check-ins and the window can save at the same time
        tmp = STATE.with_name(f"team.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(s), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, STATE)
                return
            except PermissionError:    # Windows: another process is reading it this instant
                time.sleep(0.1 * (attempt + 1))
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
_PW = {}                # the password just typed, kept in memory a few minutes so a setup sent by the master can be unlocked


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
    if SIGNED_IN.get("bot"):
        st = state()
        st["no_resume"] = True          # someone signed the bot out on purpose: ask for the password next time
        save_state(st)
    SIGNED_IN.clear()


def is_bot():
    return (policy() or {}).get("role") == "bot"


def resume():
    """The BSO Bot account stays signed in on its computer, so campaigns carry on after a restart or power cut.
    Human accounts always sign in with their password."""
    st = state()
    if not (joined() and is_bot() and st.get("username")) or st.get("revoked") or st.get("no_resume"):
        return False
    SIGNED_IN.clear()
    SIGNED_IN.update(user=st["username"], name=(st.get("policy") or {}).get("name", st["username"]), bot=True)
    return True


def check_hub_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("Enter the team address your admin gave you.")
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
    if relay.is_relay(url):         # the Google Drive team relay: same requests, carried by Google
        body = dict(payload, action=path.rsplit("/", 1)[-1])
        if token:
            body["token"] = token
        try:
            return 200, relay.call(url, body, timeout=max(timeout, 30))
        except relay.RelayError as e:
            if e.code == 0:
                raise urllib.error.URLError(str(e))
            return e.code, {"error": str(e)}
    return _post_http(url, path, payload, token, timeout)


def _post_http(url, path, payload, token=None, timeout=20):
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
    if (first_time or str(s.get("consent") or "1") != CONSENT_VERSION) and not consent:
        raise ValueError("Tick the box to confirm you've read what your manager will be able to see.")
    target = hub or hub_url()
    if not target and _local_hub_running():
        target = LOCAL_HUB          # the computer that runs the Master Console needs no address
    url = check_hub_url(target)
    device = os.getenv("COMPUTERNAME") or socket.gethostname()
    try:
        try:
            status, j = _post(url, "/api/v1/login", {"username": username, "password": password, "device": device})
        except (urllib.error.URLError, OSError, TimeoutError):
            # on the computer that runs the master console itself, use it directly while the public address isn't live
            if url == LOCAL_HUB or relay.is_relay(url) or not _local_hub_running():
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
        if _verify_offline(s, username, password) and recent and not s.get("revoked") and needs_reauth():
            raise ValueError(f"It's been more than {int(security().get('reauth_days') or 7)} days since BSO could check your password with the team. "
                             "Connect to the internet and sign in again.")
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
             verifier=_verifier(username, password), required=True, last_online_signin=iso(now_utc()))
    s.pop("revoked", None)
    s.pop("no_resume", None)
    save_state(s)
    SIGNED_IN.clear()
    SIGNED_IN.update(user=username, name=pol.get("name", username), bot=pol.get("role") == "bot")
    _PW.update(user=username, pw=password, at=time.time())
    if first_time or not SECRETS.load().get(SETUP_KEY):
        if first_time:
            restore(con)
        sync(con)          # pick up anything waiting in this person's queue (and their setup) straight away
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
            "LEFT JOIN leads l ON l.id=e.lead_id WHERE e.id>? AND e.type IN (%s) "
            "AND CAST(e.id AS TEXT) NOT IN (SELECT rid FROM test_rows WHERE tbl='events') ORDER BY e.id LIMIT 500"
            % ",".join("?" * len(SHARED_TYPES)), (after, *sorted(SHARED_TYPES))):
        detail = e["detail"] or ""
        if e["type"] == "error":
            detail = "send failed"          # raw error text can contain server details; keep it generic
        elif e["type"] == "reply":
            detail = detail if detail in ("interested", "meeting_request", "question", "not_interested", "unsubscribe", "out_of_office", "referral", "other") else "other"
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


def relay_key(ident):
    return str(ident or "").lower().replace(".", "~")


def me():
    return relay_key(state().get("username"))


def people():
    """Everyone on the team who can be @mentioned: [{key, name, role}] (published by the master console)."""
    return [p for p in (state().get("people") or []) if p.get("key")]


def mentions(text):
    """Team members @mentioned in a comment, matched by first name, full name or ID."""
    low = " " + (text or "").lower()
    out = []
    for p in people():
        names = {p["name"].lower(), p["name"].split()[0].lower() if p.get("name") else "", p["key"].replace("~", ".")}
        if any(n and re.search(r"@" + re.escape(n) + r"(?![\w.])", low) for n in names):
            out.append(p["key"])
    return out


def add_comment(con, lead_email, text, lead_name="", company=""):
    """Write a comment on a prospect. It goes to everyone @mentioned, everyone already in this prospect's
    comment thread, and the master console."""
    text = (text or "").strip()[:4000]
    if not text:
        return None
    mine = me()
    to = set(mentions(text))
    for r in con.execute("SELECT DISTINCT author FROM comments WHERE lead_email=?", (lead_email.lower(),)):
        if r["author"]:
            to.add(r["author"])
    to.discard(mine)
    uid = f"{mine}-{secrets.token_hex(8)}"
    con.execute("INSERT INTO comments(uid, lead_email, lead_name, company, author, author_name, text, at, to_, mine, synced, seen) VALUES (?,?,?,?,?,?,?,?,?,1,0,1)",
                (uid, lead_email.lower(), lead_name, company, mine, (policy() or {}).get("name") or mine, text, iso(now_utc()), json.dumps(sorted(to))))
    con.commit()
    return uid


def set_away(until):
    s = state()
    s["away_until"] = until or ""
    s["away_pending"] = True
    save_state(s)


def supervisors():
    """Lead gen supervisors published by the master console: [{id, name, email, count}]."""
    return [sv for sv in (state().get("supervisors") or []) if sv.get("email")]


def _receive_handover(con, d, restoring=False):
    """A prospect handed to me (I'm a supervisor): add it to My prospects (once, however often it arrives)."""
    L = d.get("lead") or {}
    email = str(L.get("email") or "").strip().lower()
    if not email:
        return
    have = con.execute("SELECT id FROM leads WHERE email=? AND handed_from IS NOT NULL", (email,)).fetchone()
    if have:
        if restoring and d.get("stage"):
            con.execute("UPDATE leads SET prospect_stage=? WHERE id=?", (d["stage"], have["id"]))
        return
    at = iso(now_utc())
    who_from = d.get("from") or d.get("from_name") or "a colleague"
    extra = {k: L.get(k) for k in ("last_name", "title", "linkedin", "city", "country") if L.get(k)}
    extra["handover_label"] = d.get("label", "")
    row = con.execute("SELECT id, extra FROM leads WHERE email=?", (email,)).fetchone()
    if row:
        lid = row["id"]
        merged = dict(json.loads(row["extra"] or "{}"), **extra)
        con.execute("UPDATE leads SET handed_from=?, prospect_stage=?, status='replied', extra=? WHERE id=?",
                    (who_from, d.get("stage") or "new", json.dumps(merged), lid))
    else:
        con.execute("INSERT INTO leads(email, first_name, company, website, extra, status, step, created_at, stage, handed_from, prospect_stage) "
                    "VALUES (?,?,?,?,?,'replied',0,?,'qualified',?,?)",
                    (email, L.get("first_name") or "", L.get("company") or "", L.get("website") or "", json.dumps(extra), at, who_from,
                     d.get("stage") or "new"))
        lid = con.execute("SELECT id FROM leads WHERE email=?", (email,)).fetchone()["id"]
    text = ([f"Reassigned to you from {d['reassigned_from']}."] if d.get("reassigned_from") else []) + \
           [f"Handed over by {who_from} ({str(d.get('label', '')).replace('_', ' ')} reply) on {str(d.get('at', at))[:16].replace('T', ' ')}."
            "", "--- Their reply ---", d.get("reply") or "(not available)"]
    if d.get("intro"):
        text += ["", "--- Introduction sent (you were in CC) ---", d["intro"]]
    for i, t in enumerate(d.get("thread") or [], 1):
        text += ["", f"--- Our email {i} ({str(t.get('at', ''))[:10]}): {t.get('subject', '')} ---", t.get("body", "")]
    con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'email')", (lid, "\n".join(text), at))
    name = " ".join(p for p in (L.get("first_name"), L.get("last_name")) if p) or email
    temp = d.get("temperature") or ("hot" if d.get("label") == "meeting_request" else "warm")
    if temp in ("hot", "warm", "cold"):
        con.execute("UPDATE leads SET temperature=?, temp_reason=? WHERE id=?", (temp, d.get("reason") or "", lid))
    if d.get("first_touch") and (d["first_touch"] or {}).get("campaign"):
        ft = d["first_touch"]
        con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                    (lid, f"First reached {str(ft.get('at', ''))[:10]} by campaign \"{ft.get('campaign')}\" (subject \"{ft.get('subject', '')}\") from {ft.get('sender', '')}.", at))
    if d.get("suggested_reply"):
        con.execute("UPDATE leads SET extra=json_set(COALESCE(extra,'{}'), '$.suggested_reply', ?) WHERE id=?", (str(d["suggested_reply"])[:3000], lid))
    for r in d.get("related") or []:
        if r.get("email"):
            con.execute("INSERT OR IGNORE INTO related_contacts(email, lead_id, name, role, at) VALUES (?,?,?,?,?)",
                        (str(r["email"]).lower(), lid, r.get("name") or "", r.get("role") or "cc", at))
    if (d.get("stage") or "new") == "new":
        import convo
        hours = int(d.get("respond_hours") or convo.PLAYBOOK.get(temp, {}).get("respond_hours", 9))
        due = convo.add_working_hours(datetime.now(), hours)
        icon = convo.TEMP_ICON.get(temp, "")
        add_task(con, f"{icon} Contact {name}{' at ' + L['company'] if L.get('company') else ''} within {hours} working hour(s): {temp}, handed over by {who_from}",
                 due_date=due.date().isoformat(), lead_id=lid)
        con.execute("UPDATE tasks SET priority=?, due_at=?, kind='respond' WHERE id=last_insert_rowid()",
                    ("high" if temp == "hot" else "normal", due.isoformat(timespec="minutes")))
    log_event(con, lid, "handover_in", who_from)


def assign_supervisor(lead_email):
    """Ask the always-on Google relay whose turn it is (fair across the whole team). None if it can't answer."""
    s = state()
    if not (joined() and relay.is_relay(s.get("hub", "")) and int(s.get("relay_version") or 1) >= 2):
        return None
    try:
        r = relay.call(s["hub"], {"action": "assign", "token": env(TOKEN_KEY), "lead_email": lead_email}, timeout=30)
    except relay.RelayError as e:
        print("assign:", e)
        return None
    sv = r.get("supervisor")
    return {"id": sv["id"], "name": sv["name"], "email": sv["email"], "count": 0} if sv else None


def restore(con):
    """First sign-in on this computer: bring back everything that belongs to this person (handed-over prospects and their stages)."""
    s = state()
    if not (relay.is_relay(s.get("hub", "")) and env(TOKEN_KEY)):
        return 0
    try:
        r = relay.call(s["hub"], {"action": "restore", "token": env(TOKEN_KEY)}, timeout=60)
    except relay.RelayError as e:
        print("restore:", e)
        return 0
    n = 0
    for item in r.get("items") or []:
        try:
            _receive_handover(con, unseal_item(item), restoring=True)
            n += 1
        except Exception as e:
            print("restore item:", e)
    con.commit()
    s = state()
    s["relay_version"] = int(r.get("relay_version") or s.get("relay_version") or 1)
    s["restored"] = True
    save_state(s)
    return n


# ---------------------------------------------------------------- sealing team items (Google only carries ciphertext)

SEAL_PREFIX = "BSOSEAL:"
TEAM_KEY = "BSO_TEAM_SEAL"


def _team_key():
    k = SECRETS.load().get(TEAM_KEY)
    return bytes.fromhex(k) if k else None


def seal_team(obj):
    import sealbox
    k = _team_key()
    return SEAL_PREFIX + sealbox.seal(k, json.dumps(obj, default=str)) if k else None


def open_team(v):
    import sealbox
    if not (isinstance(v, str) and v.startswith(SEAL_PREFIX)):
        return None
    k = _team_key()
    if not k:
        return None
    try:
        return json.loads(sealbox.open_(k, v[len(SEAL_PREFIX):]))
    except Exception:
        return None


def unseal_item(d):
    """Team items arrive sealed: as {'sealed': ...} from the console, or in 'reply' / 'text' when the Google script built them."""
    if isinstance(d.get("sealed"), str):
        inner = open_team(d["sealed"])
        if inner is not None:
            d = dict(inner, **{k: v for k, v in d.items() if k in ("handover_id", "local_id", "action")})
    for f in ("reply", "text"):
        inner = open_team(d.get(f))
        if isinstance(inner, dict):
            keep = {k: d[k] for k in ("handover_id", "local_id", "action", "uid", "author", "author_name", "at", "from", "stage") if k in d}
            d = dict(d, **inner, **keep)
    return d


def _seal_handover(h):
    k = _team_key()
    if not k:
        return h
    lead = h.get("lead") or {}
    outer = {f: h.get(f) for f in ("lead_local_id", "supervisor", "label", "at", "out_id", "from_name", "campaign") if f in h}
    outer["lead"] = {"email": lead.get("email")}
    outer["reply"] = seal_team(h)
    outer["intro"] = ""
    outer["thread"] = []
    return outer


def _receive_comment(con, d):
    con.execute("INSERT OR IGNORE INTO comments(uid, lead_email, lead_name, company, author, author_name, text, at, to_, mine, synced, seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,0,1,0)",
                (d.get("uid"), str(d.get("lead_email") or "").lower(), d.get("lead_name") or "", d.get("company") or "", d.get("author") or "",
                 d.get("author_name") or "", d.get("text") or "", d.get("at") or iso(now_utc()), "[]"))


def _receive_removed(con, d):
    """The master moved one of my prospects to another supervisor."""
    email = str(d.get("lead_email") or "").lower()
    row = con.execute("SELECT id FROM leads WHERE email=? AND handed_from IS NOT NULL", (email,)).fetchone()
    if row:
        con.execute("UPDATE leads SET handed_from=NULL WHERE id=?", (row["id"],))
        con.execute("UPDATE tasks SET status='done', done_at=? WHERE lead_id=? AND status='open'", (iso(now_utc())[:10], row["id"]))
        con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                    (row["id"], f"Your manager moved this prospect to {d.get('to_name') or 'another supervisor'}.", iso(now_utc())))
        log_event(con, row["id"], "reassigned", d.get("to_name") or "")


# ---------------------------------------------------------------- security: trap contacts, access log, erase

def _h(email):
    return hashlib.sha256(("bso-trap:" + str(email or "").strip().lower()).encode()).hexdigest()[:24]


def is_trap(addr):
    """Trap contacts are planted by the master console to detect copied lists. BSO never emails them."""
    traps = set(state().get("traps") or [])
    if not traps:
        return False
    for e in re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", str(addr or "")):
        if _h(e) in traps:
            return True
    return False


def _plant(con, d):
    import bot as _bot
    s = state()
    traps = set(s.get("traps") or [])
    for r in d.get("leads") or []:
        email = str(r.get("email") or "").lower()
        if not email:
            continue
        traps.add(_h(email))
        lid = _bot.add_lead(con, dict(r, email=email), "done")       # looks like an old, finished lead; never sent to
        if lid:
            con.execute("UPDATE leads SET step=3, created_at=? WHERE id=?", (iso(now_utc() - timedelta(days=20 + secrets.randbelow(40))), lid))
    s = state()
    s["traps"] = sorted(traps)
    save_state(s)
    con.commit()


def _access_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS access_log (id INTEGER PRIMARY KEY, kind TEXT, detail TEXT, n INTEGER, at TEXT, synced INTEGER DEFAULT 0)")


def record_access(con, kind, detail="", n=0):
    """Exports, opening the data folder, big lists and blocked attempts are reported to the master console."""
    try:
        _access_table(con)
        con.execute("INSERT INTO access_log(kind, detail, n, at) VALUES (?,?,?,?)", (kind, str(detail)[:300], int(n or 0), iso(now_utc())))
        con.commit()
    except Exception as e:
        print("access log:", e)


def security():
    return (state().get("security") or {})


def may_export():
    """Owners and managers may export; members only if the master allows it. Without a team, anyone may."""
    if not joined():
        return True
    role = (policy() or {}).get("role") or "member"
    roles = str(security().get("export_roles") or "owner,manager").split(",")
    return role in roles


def big_list():
    return int(security().get("big_list") or 500)


def idle_minutes():
    """Minutes without use before BSO signs itself out. Each person chooses their own (Settings → Sign-out);
    until they do, the default from the Master Console applies. The BSO Bot account is never signed out."""
    if is_bot() or not joined():
        return 0
    mine = state().get("my_idle_minutes")
    if mine is not None:
        return int(mine or 0)
    return int(security().get("idle_minutes", 15) or 0)


def set_idle_minutes(minutes):
    s = state()
    s["my_idle_minutes"] = None if minutes is None else max(0, min(int(minutes), 24 * 60))
    save_state(s)


def needs_reauth():
    """Offline sign-in is allowed for a while; after `reauth_days` the password must be checked by the team again."""
    days = int(security().get("reauth_days") or 7)
    s = state()
    try:
        last = datetime.fromisoformat(s.get("last_online_signin")).astimezone(timezone.utc)
    except Exception:
        return False
    return now_utc() - last > timedelta(days=days)


def disk_encrypted():
    """True / False when Windows can tell whether the BSO data drive is protected by BitLocker or Device encryption; None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import subprocess
        drive = str(Path(DATA).resolve().drive or "C:")
        ps = ("$v=(New-Object -ComObject Shell.Application).NameSpace('%s\\').Self.ExtendedProperty('System.Volume.BitLockerProtection');"
              "Write-Output $v") % drive
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if out in ("1", "3", "5"):      # 1 = on, 3 = on (encrypting), 5 = on (suspended counts as protected at rest)
            return True
        if out in ("0", "2", "4", "6"):
            return False
    except Exception:
        pass
    return None


_enc_cache = {}


def disk_encrypted_cached():
    if not _enc_cache or time.time() - _enc_cache.get("t", 0) > 6 * 3600:
        _enc_cache.update(v=disk_encrypted(), t=time.time())
    return _enc_cache["v"]


def erase_everything(con):
    """The master removed or disconnected this person: erase leads, prospects, emails, notes and saved passwords."""
    keep = {"sqlite_sequence"}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if t not in keep:
            con.execute(f'DELETE FROM "{t}"')
    con.commit()
    try:
        con.execute("VACUUM")
    except Exception:
        pass
    try:
        from secretstore import SecretStore
        st = SecretStore(DATA)
        for k in list(st.load()):
            st.set(k, "")
    except Exception as e:
        print("erase secrets:", e)
    import shutil
    for sub in ("mail", "mailbox", "attachments", "imports", "exports", "backups", "logs"):
        shutil.rmtree(Path(DATA) / sub, ignore_errors=True)
    for f in Path(DATA).glob("*.csv"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        import yaml
        cp = Path(DATA) / "config.yaml"
        cfg = yaml.safe_load(cp.read_text(encoding="utf-8")) or {}
        for sv in cfg.get("senders") or []:
            for k in ("password", "smtp_password", "imap_password"):
                sv.pop(k, None)
        cp.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    except Exception:
        pass
    try:
        import yaml
        sp = Path(DATA) / "settings.yaml"
        if sp.exists():
            cur = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            sp.write_text(yaml.safe_dump({k: v for k, v in cur.items() if k == "legal_acceptance"}), encoding="utf-8")
    except Exception:
        pass
    hub = state().get("hub", "")
    save_state({"hub": hub, "revoked": True, "wiped": iso(now_utc())})
    _PW.clear()
    SIGNED_IN.clear()


# ---------------------------------------------------------------- setup sent by the master

SETUP_KEY = "BSO_SETUP_KEY"


def _take_setup_key(d):
    """Unlock the per-person key with the password typed at sign-in. True when done (or no longer possible)."""
    import sealbox
    if not (_PW.get("pw") and time.time() - _PW.get("at", 0) < 900):
        return False                      # wait until someone signs in with the password
    try:
        k = sealbox.unwrap(d.get("wrap") or {}, _PW["user"], _PW["pw"])
    except Exception:
        return True                       # made for an older password: the master will send a new one
    SECRETS.set(SETUP_KEY, k.hex())
    return True


def apply_setup(con, d):
    """Apply inboxes, mail server, keys and schedule the master sent. True when applied (or unusable)."""
    import sealbox
    import yaml
    from datetime import date
    k = SECRETS.load().get(SETUP_KEY)
    if not k:
        return False
    try:
        data = json.loads(sealbox.open_(bytes.fromhex(k), d.get("box") or ""))
    except Exception:
        return True
    from bot import settings_path
    sp = settings_path(str(Path(DATA) / "config.yaml"))
    try:
        cur = yaml.safe_load(sp.read_text(encoding="utf-8")) or {} if sp.exists() else {}
    except Exception:
        cur = {}
    senders = [x for x in (cur.get("senders") or []) if x.get("email") and "example" not in x["email"]]
    by = {x["email"].lower(): x for x in senders}
    used = {int(re.sub(r"\D", "", x.get("password_env", "")) or 0) for x in senders}
    for sv in data.get("senders") or []:
        e = str(sv.get("email") or "").lower()
        if not e:
            continue
        x = by.get(e)
        if not x:
            n = next(i for i in range(1, 200) if i not in used)
            used.add(n)
            x = {"email": e, "password_env": f"SENDER{n}_PASSWORD", "ramp_from": date.today().isoformat() if sv.get("ramp") else None}
            senders.append(x)
            by[e] = x
        x.update(name=sv.get("name") or x.get("name") or e, smtp_host=sv.get("smtp_host") or x.get("smtp_host", ""),
                 smtp_port=int(sv.get("smtp_port") or x.get("smtp_port") or 465), imap_host=sv.get("imap_host") or x.get("imap_host", ""),
                 daily_limit=int(sv.get("daily_limit") or x.get("daily_limit") or 10))
        if sv.get("password"):
            SECRETS.set(x["password_env"], sv["password"])
    if senders:
        cur["senders"] = senders
    for name, val in (data.get("keys") or {}).items():
        if name in ("ANTHROPIC_API_KEY", "INSTANTLY_API_KEY") and val:
            SECRETS.set(name, val)
    opt = data.get("options") or {}
    if "autorun_minutes" in opt:
        m = int(opt.get("autorun_minutes") or 0)
        cur["autorun"] = dict(cur.get("autorun") or {}, enabled=m > 0, every_minutes=m or int((cur.get("autorun") or {}).get("every_minutes") or 30))
    if "live" in opt:
        cur["test_mode"] = dict(cur.get("test_mode") or {}, enabled=not opt["live"])
    if (data.get("keys") or {}).get("INSTANTLY_API_KEY"):
        cur["instantly"] = dict(cur.get("instantly") or {}, enabled=True)
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(cur, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, sp)
    s = state()
    s["setup_v"] = data.get("v")
    s["setup_ack"] = data.get("v")
    save_state(s)
    record_access(con, "setup", f"{len(data.get('senders') or [])} inbox(es) set up by the master")
    return True


def _transfer_out(con):
    """I'm being removed from the team: pack up my leads so the console can share them with the others."""
    rows = []
    for l in con.execute("SELECT l.*, c.name cname, (SELECT MAX(at) FROM events e WHERE e.lead_id=l.id AND e.type='sent') last_sent "
                         "FROM leads l LEFT JOIN campaigns c ON c.id=COALESCE(l.campaign_id,1) "
                         "WHERE l.status NOT IN ('unsubscribed','bounced') AND l.handed_from IS NULL ORDER BY l.id"):
        if is_trap(l["email"]):
            continue
        x = json.loads(l["extra"] or "{}")
        notes = [r[0][:500] for r in con.execute("SELECT body FROM notes WHERE lead_id=? ORDER BY at DESC LIMIT 3", (l["id"],))]
        rows.append({"email": l["email"], "first_name": l["first_name"], "company": l["company"], "website": l["website"], "extra": x,
                     "status": l["status"], "step": l["step"], "first_subject": l["first_subject"], "campaign": l["cname"],
                     "last_sent": l["last_sent"], "stage": l["stage"], "notes": notes})
    supp = [r[0] for r in con.execute("SELECT email FROM suppression")]
    parts = [rows[i:i + 300] for i in range(0, len(rows), 300)] or [[]]
    s = state()
    s["transfer_queue"] = [{"part": i, "leads": p, "last": i == len(parts) - 1, "suppress": supp if i == 0 else []} for i, p in enumerate(parts)]
    save_state(s)
    con.execute("UPDATE leads SET status='paused' WHERE status='active'")      # nothing more goes out from this BSO
    con.execute("UPDATE outbox SET status='cancelled' WHERE status IN ('pending','approved')")
    con.commit()


def _leads_in(con, d):
    """Leads shared with me from someone who left the team."""
    import bot as _bot
    who = d.get("from_name") or "a colleague"
    for r in d.get("leads") or []:
        email = str(r.get("email") or "").lower()
        if not email or con.execute("SELECT 1 FROM leads WHERE email=?", (email,)).fetchone():
            continue
        cid = 1
        if r.get("campaign"):
            hit = con.execute("SELECT id FROM campaigns WHERE name=?", (r["campaign"],)).fetchone()
            cid = hit["id"] if hit else 1
        row = dict(r.get("extra") or {}, email=email, first_name=r.get("first_name") or "", company=r.get("company") or "",
                   website=r.get("website") or "", campaign_id=cid, moved_from=who)
        st = r.get("status") or "active"
        step = int(r.get("step") or 0)
        mid = st == "active" and step > 0
        lid = _bot.add_lead(con, row, "paused" if mid else st)
        if not lid:
            continue
        con.execute("UPDATE leads SET step=?, first_subject=?, stage=COALESCE(?, stage) WHERE id=?", (step, r.get("first_subject"), r.get("stage"), lid))
        con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                    (lid, f"Moved to you from {who}" + (f" (last emailed {str(r['last_sent'])[:10]})." if r.get("last_sent") else ".") +
                     ("\n" + "\n---\n".join(r["notes"]) if r.get("notes") else ""), iso(now_utc())))
        if mid:
            add_task(con, f"Continue or restart the sequence for {r.get('first_name') or email} (moved from {who}, {step} email(s) already sent)", lead_id=lid)
    for e in d.get("suppress") or []:
        con.execute("INSERT OR IGNORE INTO suppression(email, reason, at) VALUES (?,?,?)", (e, f"do-not-contact from {who}", iso(now_utc())))
    con.commit()


def _apply_decisions(con, decisions):
    """Apply what the master console sent. Returns (draft ids done, handover ids received)."""
    done, ho_done = [], []
    decisions = sorted(decisions, key=lambda d: 0 if d.get("action") in ("setup_key", "vis_state") else 1)
    for d in decisions:
        if isinstance(d, dict) and (d.get("sealed") or str(d.get("reply") or "").startswith(SEAL_PREFIX) or str(d.get("text") or "").startswith(SEAL_PREFIX)):
            d = unseal_item(d)
        action = d.get("action")
        if action == "config":
            s = state()
            s["supervisors"] = d.get("supervisors") or []
            s["people"] = d.get("people") or s.get("people") or []
            s["sup_as_of"] = d.get("as_of", "")
            if isinstance(d.get("security"), dict):
                s["security"] = d["security"]
            save_state(s)
            continue
        if action == "strategy":
            if isinstance(d.get("arms"), dict) or d.get("version"):
                s2 = state()
                if s2.get("strategy", {}).get("version") != d.get("version") and (d.get("arms") is not None or d.get("lessons") is not None):
                    s2["strategy"] = {k: d.get(k) for k in ("version", "arms", "explore", "examples", "lessons", "scoring")}
                    save_state(s2)
                    con.execute("UPDATE leads SET fit_score=NULL WHERE status='active' AND step=0")      # re-scored at the next run
            continue
        if action == "vis_state":
            s2 = state()
            s2["vis_key_ok"] = bool(d.get("key_ok"))
            save_state(s2)
            if d.get("team_key"):
                try:
                    import report
                    import sealbox
                    tk = sealbox.open_(report.my_key(SECRETS), d["team_key"]).decode()
                    if SECRETS.load().get(TEAM_KEY) != tk:
                        SECRETS.set(TEAM_KEY, tk)
                except Exception as e:
                    print("team key:", e)
            continue
        if action in ("setup_key", "setup"):
            try:
                ok = _take_setup_key(d) if action == "setup_key" else apply_setup(con, d)
            except Exception as e:
                print(action + ":", e)
                ok = False
            if ok and d.get("handover_id"):
                ho_done.append(d["handover_id"])
            continue
        if action in ("wipe", "canary"):
            hid = d.get("handover_id")
            if hid not in state().get("ho_seen", []):
                s2 = state()
                if action == "wipe":
                    s2["wipe_then"] = d.get("then") or "remove"
                    save_state(s2)
                    if not s2.get("transfer_queue") and not s2.get("transfer_done"):
                        _transfer_out(con)          # hand the work over before erasing
                    con.execute("UPDATE leads SET status='paused' WHERE status='active'")
                    try:
                        con.execute("UPDATE outbox SET status='cancelled' WHERE status IN ('pending','approved')")
                    except Exception:
                        pass
                else:
                    _plant(con, d)
                s2 = state()
                s2["ho_seen"] = (s2.get("ho_seen", []) + [hid])[-1000:]
                save_state(s2)
            if hid:
                ho_done.append(hid)
            continue
        if action == "transfer_out":
            if not state().get("transfer_queue") and not state().get("transfer_done"):
                _transfer_out(con)
            if d.get("handover_id"):
                ho_done.append(d["handover_id"])
            continue
        if action == "leads_in":
            hid = d.get("handover_id")
            if hid not in state().get("ho_seen", []):
                _leads_in(con, d)
                s2 = state()
                s2["ho_seen"] = (s2.get("ho_seen", []) + [hid])[-1000:]
                save_state(s2)
            if hid:
                ho_done.append(hid)
            continue
        if action in ("comment", "removed"):
            try:
                (_receive_comment if action == "comment" else _receive_removed)(con, d)
            except Exception as e:
                print(action + ":", e)
            if d.get("handover_id"):
                ho_done.append(d["handover_id"])
            continue
        if action == "handover":
            s = state()
            seen = s.get("ho_seen", [])
            if d.get("handover_id") not in seen:      # the same handover can arrive again until the console sees our receipt
                try:
                    _receive_handover(con, d)
                except Exception as e:     # one bad item must not block the rest
                    print("handover:", e)
                s["ho_seen"] = (seen + [d.get("handover_id")])[-1000:]
                save_state(s)
            ho_done.append(d.get("handover_id"))
            continue
        if d.get("local_id") is None:
            continue
        o = con.execute("SELECT * FROM outbox WHERE id=?", (d["local_id"],)).fetchone()
        if o and o["status"] == "pending":
            when = iso(now_utc())
            if action == "approve":
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
    return done, ho_done


def _mailbox_on(cfg):
    try:
        import mailbox as _mb
        return bool(_mb.conf(cfg))
    except Exception:
        return False


def status(con):
    """How this BSO is doing (shown on the Master Console, mainly for the BSO Bot)."""
    import bot as _bot
    try:
        cfg = _bot.load_config(str(Path(DATA) / "config.yaml"))
    except Exception:
        cfg = {}
    day = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    run = con.execute("SELECT status, started_at, finished_at, output FROM jobs WHERE cmd='run' ORDER BY id DESC LIMIT 1").fetchone()
    paused = _bot.guard_paused(con)
    cap = sum(_bot.ramp_limit(cfg, sv) for sv in (cfg.get("senders") or [])
              if sv.get("email") and "example" not in sv.get("email", "") and sv["email"].lower() not in paused)
    fresh = q("SELECT COUNT(*) FROM leads WHERE status='active' AND step=0")
    last_line = ""
    if run and run["output"]:
        lines = [l for l in run["output"].strip().splitlines() if l.strip()]
        last_line = (lines[-1] if lines else "")[:200]
    ar = cfg.get("autorun") or {}
    return {"at": iso(now_utc()), "last_run": (run["finished_at"] or run["started_at"]) if run else None,
            "last_run_status": run["status"] if run else None, "last_run_line": last_line,
            "sent_today": sent_today_total(con), "replies_today": q("SELECT COUNT(*) FROM events WHERE type='reply' AND at>=?", iso(day)),
            "handovers_today": q("SELECT COUNT(*) FROM events WHERE type='handover' AND at>=?", iso(day)),
            "errors_today": q("SELECT COUNT(*) FROM events WHERE type='error' AND at>=?", iso(day)),
            "leads_active": q("SELECT COUNT(*) FROM leads WHERE status='active'"), "leads_not_started": fresh,
            "daily_capacity": cap, "days_of_leads": round(fresh / cap, 1) if cap else None,
            "autorun": bool(ar.get("enabled")), "every_minutes": int(ar.get("every_minutes") or 0),
            "test_mode": bool((cfg.get("test_mode") or {}).get("enabled")), "review": bool((cfg.get("review") or {}).get("enabled")),
            "paused_inboxes": sorted(paused), "inboxes": len([sv for sv in cfg.get("senders") or [] if sv.get("email") and "example" not in sv["email"]]),
            "campaigns": [dict(r) for r in con.execute("SELECT id, name, status FROM campaigns ORDER BY id")],
            "disk_encrypted": disk_encrypted_cached(), "idle_lock": idle_minutes(),
            "mailbox_connected": _mailbox_on(cfg), "mailbox_checked": state().get("mailbox_checked"),
            "prospects": q("SELECT COUNT(*) FROM leads WHERE handed_from IS NOT NULL"),
            "overdue": [dict(r) for r in con.execute(
                "SELECT l.email lead_email, l.company, t.subject, t.due_at, t.priority FROM tasks t JOIN leads l ON l.id=t.lead_id "
                "WHERE t.status='open' AND t.kind='respond' AND t.due_at IS NOT NULL AND t.due_at<? ORDER BY t.due_at LIMIT 50",
                (datetime.now().isoformat(timespec="minutes"),))]}


def sync(con, version=""):
    """One round trip. Returns (ok, message). Never raises. One at a time per app."""
    with _sync_lock:
        return _sync(con, version)


def _sync(con, version=""):
    s = state()
    if not joined():
        return False, "Not in a team."
    try:
        events = _collect_events(con, s.get("last_event_id", 0))
        drafts = _collect_drafts(con, set(s.get("drafts_sent", [])))
        hos = con.execute("SELECT id, payload FROM handover_out WHERE synced=0 ORDER BY id LIMIT 50").fetchall()
        handovers = [_seal_handover(dict(json.loads(h["payload"]), out_id=h["id"])) for h in hos]
        # handovers travel inside the existing fields, so an already-deployed Google relay passes them on unchanged
        packed = events + [{"id": f"h{h['out_id']}", "type": "__handover", "data": h} for h in handovers]
        st_now, st_extra = None, []
        try:
            st_now = status(con)
            key = json.dumps({k: v for k, v in st_now.items() if k != "at"}, sort_keys=True)
            due = not s.get("status_at") or now_utc() - datetime.fromisoformat(s["status_at"]).astimezone(timezone.utc) > timedelta(minutes=15)
            if key != s.get("status_key") or due:
                st_extra = [{"id": "s", "type": "__status", "data": st_now}]
                st_now = (key, iso(now_utc()))
            else:
                st_now = None
        except Exception as e:
            print("status:", e)
        packed += st_extra
        cms = con.execute("SELECT * FROM comments WHERE mine=1 AND synced=0 ORDER BY id LIMIT 50").fetchall()
        def _cm(c):
            d = {"uid": c["uid"], "lead_email": c["lead_email"], "lead_name": c["lead_name"], "company": c["company"], "text": c["text"], "at": c["at"],
                 "to": json.loads(c["to_"] or "[]")}
            sealed = seal_team({"text": (c["text"] or "")[:2500], "lead_name": c["lead_name"], "company": c["company"]}) if _team_key() else None
            if sealed and len(sealed) <= 4000:
                d.update(text=sealed, lead_name="", company="")
            return d
        packed += [{"id": f"c{c['id']}", "type": "__comment", "data": _cm(c)} for c in cms]
        tq = (s.get("transfer_queue") or [])[:2]
        def _tr(t):
            try:
                import report
                import sealbox
                if s.get("vis_key_ok"):
                    return {"part": t["part"], "last": t.get("last"), "box": sealbox.seal(report.my_key(SECRETS), json.dumps(t))}
            except Exception:
                pass
            return t
        packed += [{"id": f"t{t['part']}", "type": "__transfer", "data": _tr(t)} for t in tq]
        if s.get("away_pending"):
            packed.append({"id": "away", "type": "__away", "data": {"until": s.get("away_until", "")}})
        _access_table(con)
        acc = con.execute("SELECT * FROM access_log WHERE synced=0 ORDER BY id LIMIT 50").fetchall()
        dev = s.get("device_tag") or secrets.token_hex(4)
        if not s.get("device_tag"):
            s["device_tag"] = dev
            save_state(s)
        packed += [{"id": f"a{a['id']}", "type": "__audit", "data": {"uid": f"{dev}-{a['id']}", "kind": a["kind"], "detail": a["detail"], "n": a["n"], "at": a["at"]}}
                   for a in acc]
        if s.get("setup_ack"):
            packed.append({"id": "setup", "type": "__setup", "data": {"v": s["setup_ack"]}})
        vis_marks, vis_archive_done = [], False
        try:
            import report
            vis_evs, vis_marks, vis_archive_done = report.events(con, s, SECRETS)
            packed += vis_evs
        except Exception as e:
            print("visibility:", e)
        wiping = bool(s.get("wipe_then")) and not s.get("transfer_queue")
        if wiping:
            packed.append({"id": "w", "type": "__wiped", "data": {"at": iso(now_utc())}})
        acked = list(s.get("ack", [])) + [f"ho:{x}" for x in s.get("ho_ack", []) if x is not None]
        code, j = _post(s["hub"], "/api/v1/sync", {"events": packed, "drafts": drafts, "acked": acked, "version": version},
                        token=env(TOKEN_KEY))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, f"Can't reach the hub ({getattr(e, 'reason', e)})."
    if code == 401:
        s["revoked"] = True
        save_state(s)
        return False, "Your manager disconnected this laptop. Ask for a new invite."
    if code != 200:
        return False, f"The hub returned an error ({code})."
    s = state() or s
    s.pop("revoked", None)
    if events:
        s["last_event_id"] = events[-1]["id"]
    s["drafts_sent"] = sorted(set(s.get("drafts_sent", [])) | {d["id"] for d in drafts})[-2000:]
    s["policy"] = j.get("policy") or s.get("policy", {})
    s["relay_version"] = int(j.get("relay_version") or (1 if relay.is_relay(s["hub"]) else 0))
    s["last_ok"] = iso(now_utc())
    if st_now:
        s["status_key"], s["status_at"] = st_now
    if cms:
        con.execute(f"UPDATE comments SET synced=1 WHERE id IN ({','.join('?' * len(cms))})", [c["id"] for c in cms])
        con.commit()
    s.pop("away_pending", None)
    if s.get("setup_ack") and any(p.get("type") == "__setup" for p in packed):
        s.pop("setup_ack", None)
    if vis_marks or vis_archive_done:
        try:
            import report
            report.mark_done(con, s, vis_marks)
            if vis_archive_done:
                s["vis_archive_done"] = True
        except Exception as e:
            print("visibility marks:", e)
    if acc:
        con.execute(f"UPDATE access_log SET synced=1 WHERE id IN ({','.join('?' * len(acc))})", [a["id"] for a in acc])
        con.commit()
    if wiping:
        erase_everything(con)
        return True, "This BSO was erased by your manager."
    if tq:
        rest = (s.get("transfer_queue") or [])[len(tq):]
        s["transfer_queue"] = rest
        if not rest:
            s["transfer_done"] = True
    if handovers:
        con.execute(f"UPDATE handover_out SET synced=1 WHERE id IN ({','.join('?' * len(handovers))})", [h["out_id"] for h in handovers])
        con.commit()
    save_state(s)
    s["ack"], s["ho_ack"] = _apply_decisions(con, j.get("decisions") or [])
    s = dict(state(), ack=s["ack"], ho_ack=s["ho_ack"])   # keep the supervisor list _apply_decisions saved
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
