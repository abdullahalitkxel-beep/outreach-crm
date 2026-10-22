"""Master Console side of full visibility: the console's key pair, the sealed reports from every BSO,
the permanent incident log (each entry chained to the one before, so later tampering shows), retention,
the archive of old data, and the company archive mailbox.
"""
import email as _email
import hashlib
import imaplib
import json
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import sealbox

SCHEMA = """
CREATE TABLE IF NOT EXISTS vis_sent (
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, domain TEXT, campaign_id INTEGER, campaign TEXT, step INTEGER,
    variant TEXT, kind TEXT, sender TEXT, to_ TEXT, cc TEXT, bcc TEXT, subject TEXT, body TEXT, at TEXT, msg_id TEXT, angle TEXT,
    UNIQUE(user_id, local_id)
);
CREATE INDEX IF NOT EXISTS vis_sent_lead ON vis_sent(lead_email);
CREATE TABLE IF NOT EXISTS vis_received (
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, inbox TEXT, folder TEXT, frm TEXT, to_ TEXT, cc TEXT,
    subject TEXT, body TEXT, at TEXT, label TEXT, temperature TEXT, how TEXT, path TEXT, msg_id TEXT, UNIQUE(user_id, local_id)
);
CREATE INDEX IF NOT EXISTS vis_received_lead ON vis_received(lead_email);
CREATE TABLE IF NOT EXISTS vis_messages (                   -- supervisors' conversations with prospects (and the company archive mailbox)
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, msg_id TEXT, direction TEXT, at TEXT, frm TEXT, to_ TEXT,
    cc TEXT, bcc TEXT, subject TEXT, body TEXT, via TEXT, folder TEXT, deleted_at TEXT, deleted_how TEXT, UNIQUE(user_id, msg_id)
);
CREATE INDEX IF NOT EXISTS vis_messages_lead ON vis_messages(lead_email);
CREATE TABLE IF NOT EXISTS vis_leads (
    user_id INTEGER, email TEXT, local_id INTEGER, first_name TEXT, company TEXT, campaign TEXT, status TEXT, stage TEXT, temperature TEXT,
    temp_reason TEXT, check_back TEXT, prospect_stage TEXT, handed_to TEXT, handed_from TEXT, deal_status TEXT, deal_value REAL, lost_reason TEXT,
    score REAL, came_back TEXT, data TEXT, updated_at TEXT, PRIMARY KEY (user_id, email)
);
CREATE TABLE IF NOT EXISTS vis_notes (id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, body TEXT, at TEXT, kind TEXT, UNIQUE(user_id, local_id));
CREATE TABLE IF NOT EXISTS vis_tasks (
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, subject TEXT, due_date TEXT, due_at TEXT, status TEXT,
    priority TEXT, kind TEXT, done_at TEXT, UNIQUE(user_id, local_id)
);
CREATE TABLE IF NOT EXISTS vis_temps (id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, temperature TEXT, score INTEGER,
    reason TEXT, by TEXT, at TEXT, UNIQUE(user_id, local_id));
CREATE TABLE IF NOT EXISTS vis_inbox (id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, inbox TEXT, at TEXT, frm TEXT, subject TEXT,
    decision TEXT, folder TEXT, UNIQUE(user_id, local_id));
CREATE TABLE IF NOT EXISTS vis_runs (id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, cmd TEXT, status TEXT, started_at TEXT,
    finished_at TEXT, output TEXT, UNIQUE(user_id, local_id));
CREATE TABLE IF NOT EXISTS archive (                         -- everything each BSO held before full visibility, for backtracking
    id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, local_id INTEGER, lead_email TEXT, at TEXT, text TEXT, data TEXT, UNIQUE(user_id, kind, local_id)
);
CREATE INDEX IF NOT EXISTS archive_lead ON archive(lead_email);
CREATE TABLE IF NOT EXISTS incidents (                       -- permanent: never deleted, never edited (review notes live beside it)
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, at TEXT, kind TEXT, lead_email TEXT, detail TEXT, how TEXT, msg TEXT,
    prev_hash TEXT, hash TEXT, UNIQUE(user_id, local_id)
);
CREATE TABLE IF NOT EXISTS incident_reviews (incident_id INTEGER PRIMARY KEY, status TEXT, by TEXT, at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS archive_box_seen (msg_id TEXT PRIMARY KEY, at TEXT);
"""

INCIDENT_KINDS = {
    "bcc_outside": "BCC to an outside address", "cc_outside": "CC to an outside address", "to_outside": "sent to an outside address",
    "forward_outside": "lead conversation forwarded outside", "blocked": "blocked before sending", "deleted": "lead email deleted",
    "trashed": "lead email moved to Trash", "outside_bso": "sent and deleted outside BSO (seen only in the company archive)",
    "unlinked_sender": "lead conversation from an unlinked address",
}


def ensure(con):
    con.executescript(SCHEMA)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- the console's key pair

def keypair(store, meta_get, meta_set):
    pub = meta_get("vis_pub")
    if pub and store.load().get("HUB_VIS_PRIV"):
        return json.loads(pub), json.loads(store.load()["HUB_VIS_PRIV"])
    p, q = sealbox.rsa_keypair()
    store.set("HUB_VIS_PRIV", json.dumps(q))
    meta_set("vis_pub", json.dumps(p))
    return p, q


def take_key(con, store, uid, box, priv):
    try:
        k = sealbox.rsa_decrypt(priv, box)
    except Exception:
        return False
    store.set(f"VIS_KEY_{uid}", k.hex())
    return True


def has_key(store, uid):
    return bool(store.load().get(f"VIS_KEY_{uid}"))


# ---------------------------------------------------------------- incidents (hash-chained)

def _chain_hash(prev, row):
    canon = json.dumps({k: row.get(k) for k in ("user_id", "local_id", "at", "kind", "lead_email", "detail", "how", "msg")}, sort_keys=True)
    return hashlib.sha256(((prev or "") + canon).encode()).hexdigest()


def add_incident(con, user_id, local_id, at, kind, lead_email, detail, how, msg):
    if local_id is not None and con.execute("SELECT 1 FROM incidents WHERE user_id IS ? AND local_id=?", (user_id, local_id)).fetchone():
        return None
    prev = con.execute("SELECT hash FROM incidents ORDER BY id DESC LIMIT 1").fetchone()
    prev = prev[0] if prev else ""
    row = {"user_id": user_id, "local_id": local_id, "at": at or now_iso(), "kind": kind, "lead_email": (lead_email or "").lower(),
           "detail": (detail or "")[:500], "how": how or "", "msg": msg if isinstance(msg, str) or msg is None else json.dumps(msg)}
    h = _chain_hash(prev, row)
    cur = con.execute("INSERT INTO incidents(user_id, local_id, at, kind, lead_email, detail, how, msg, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (row["user_id"], row["local_id"], row["at"], kind, row["lead_email"], row["detail"], row["how"], row["msg"], prev, h))
    return cur.lastrowid


def verify_chain(con):
    """(ok, first broken id or None)."""
    prev = ""
    for r in con.execute("SELECT * FROM incidents ORDER BY id"):
        row = {k: r[k] for k in ("user_id", "local_id", "at", "kind", "lead_email", "detail", "how", "msg")}
        if r["prev_hash"] != prev or r["hash"] != _chain_hash(prev, row):
            return False, r["id"]
        prev = r["hash"]
    return True, None


# ---------------------------------------------------------------- sealed reports from a BSO

def _g(d, k, n=None):
    v = d.get(k)
    return (str(v)[:n] if n and v is not None else v)


def ingest(con, store, uid, box):
    k = store.load().get(f"VIS_KEY_{uid}")
    if not k:
        return 0
    try:
        data = json.loads(sealbox.open_(bytes.fromhex(k), box))
    except Exception:
        return 0
    n = 0
    for r in data.get("sent") or []:
        con.execute("INSERT OR IGNORE INTO vis_sent(user_id, local_id, lead_email, domain, campaign_id, step, variant, kind, sender, to_, cc, bcc, subject, "
                    "body, at, msg_id, angle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "domain", 200), r.get("campaign_id"), r.get("step"), _g(r, "variant", 80),
                     _g(r, "kind", 20), _g(r, "sender", 200), _g(r, "to_", 500), _g(r, "cc", 500), _g(r, "bcc", 500), _g(r, "subject", 500),
                     _g(r, "body", 30000), _g(r, "at", 40), _g(r, "msg_id", 300), _g(r, "angle", 80)))
        n += 1
    for r in data.get("received") or []:
        con.execute("INSERT OR IGNORE INTO vis_received(user_id, local_id, lead_email, inbox, folder, frm, to_, cc, subject, body, at, label, temperature, "
                    "how, path, msg_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "inbox", 200), _g(r, "folder", 40), _g(r, "frm", 200), _g(r, "to_", 500), _g(r, "cc", 500),
                     _g(r, "subject", 500), _g(r, "body", 30000), _g(r, "at", 40), _g(r, "label", 40), _g(r, "temperature", 20), _g(r, "how", 80),
                     _g(r, "path", 2000), _g(r, "msg_id", 300)))
        n += 1
    for r in data.get("messages") or []:
        con.execute("INSERT INTO vis_messages(user_id, local_id, lead_email, msg_id, direction, at, frm, to_, cc, bcc, subject, body, via, folder, deleted_at, deleted_how) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, msg_id) DO UPDATE SET deleted_at=excluded.deleted_at, deleted_how=excluded.deleted_how",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "msg_id", 300) or f"local-{r['id']}", _g(r, "direction", 5), _g(r, "at", 40), _g(r, "frm", 300),
                     _g(r, "to_", 1000), _g(r, "cc", 1000), _g(r, "bcc", 1000), _g(r, "subject", 500), _g(r, "body", 30000), _g(r, "via", 20),
                     _g(r, "folder", 60), _g(r, "deleted_at", 40), _g(r, "deleted_how", 40)))
        n += 1
    for r in data.get("leads") or []:
        con.execute("INSERT OR REPLACE INTO vis_leads(user_id, email, local_id, first_name, company, campaign, status, stage, temperature, temp_reason, check_back, "
                    "prospect_stage, handed_to, handed_from, deal_status, deal_value, lost_reason, score, came_back, data, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, str(r.get("email") or "").lower(), r.get("id"), _g(r, "first_name", 200), _g(r, "company", 200), _g(r, "campaign", 200),
                     _g(r, "status", 30), _g(r, "stage", 30), _g(r, "temperature", 20), _g(r, "temp_reason", 300), _g(r, "check_back", 20),
                     _g(r, "prospect_stage", 30), _g(r, "handed_to", 200), _g(r, "handed_from", 200), _g(r, "deal_status", 30), r.get("deal_value"),
                     _g(r, "lost_reason", 300), r.get("score"), _g(r, "came_back", 20), _g(r, "extra", 8000), now_iso()))
        n += 1
    for r in data.get("notes") or []:
        con.execute("INSERT OR IGNORE INTO vis_notes(user_id, local_id, lead_email, body, at, kind) VALUES (?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "body", 20000), _g(r, "at", 40), _g(r, "kind", 20)))
        n += 1
    for r in data.get("tasks") or []:
        con.execute("INSERT OR REPLACE INTO vis_tasks(user_id, local_id, lead_email, subject, due_date, due_at, status, priority, kind, done_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "subject", 500), _g(r, "due_date", 20), _g(r, "due_at", 30), _g(r, "status", 20),
                     _g(r, "priority", 20), _g(r, "kind", 30), _g(r, "done_at", 30)))
        n += 1
    for r in data.get("temps") or []:
        con.execute("INSERT OR IGNORE INTO vis_temps(user_id, local_id, lead_email, temperature, score, reason, by, at) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "lead_email", 200), _g(r, "temperature", 20), r.get("score"), _g(r, "reason", 300), _g(r, "by", 20), _g(r, "at", 40)))
    for r in data.get("inbox") or []:
        con.execute("INSERT OR IGNORE INTO vis_inbox(user_id, local_id, inbox, at, frm, subject, decision, folder) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "inbox", 200), _g(r, "at", 40), _g(r, "frm", 200), _g(r, "subject", 300), _g(r, "decision", 300), _g(r, "folder", 40)))
    for r in data.get("runs") or []:
        con.execute("INSERT OR IGNORE INTO vis_runs(user_id, local_id, cmd, status, started_at, finished_at, output) VALUES (?,?,?,?,?,?,?)",
                    (uid, r["id"], _g(r, "cmd", 30), _g(r, "status", 20), _g(r, "started_at", 40), _g(r, "finished_at", 40), _g(r, "output", 8000)))
    for r in data.get("incidents") or []:
        add_incident(con, uid, r["id"], r.get("at"), _g(r, "kind", 40), r.get("lead_email"), r.get("detail"), r.get("how"), r.get("msg"))
        n += 1
    for key, items in data.items():
        if not key.startswith("archive_"):
            continue
        kind = key[8:]
        for r in items:
            text = " ".join(str(r.get(f) or "") for f in ("subject", "body", "detail", "text", "email", "first_name", "company", "frm", "to_"))[:20000]
            at = r.get("at") or r.get("created_at") or r.get("sent_at") or ""
            con.execute("INSERT OR IGNORE INTO archive(user_id, kind, local_id, lead_email, at, text, data) VALUES (?,?,?,?,?,?,?)",
                        (uid, kind, r.get("id"), str(r.get("lead_email") or r.get("email") or "").lower()[:200], str(at)[:40], text,
                         json.dumps(r, default=str)[:40000]))
            n += 1
    return n


# ---------------------------------------------------------------- retention

def purge(con, months):
    if not months:
        return 0
    cut = (datetime.now(timezone.utc) - timedelta(days=30 * int(months))).isoformat(timespec="seconds")
    n = 0
    for t in ("vis_sent", "vis_received", "vis_messages", "vis_notes", "vis_inbox", "vis_runs", "vis_temps"):
        n += con.execute(f"DELETE FROM {t} WHERE at<?" if t != "vis_runs" else "DELETE FROM vis_runs WHERE started_at<?", (cut,)).rowcount
    con.commit()
    return n


# ---------------------------------------------------------------- the company archive mailbox

FREE_MAIL = None


def _free():
    global FREE_MAIL
    if FREE_MAIL is None:
        try:
            import convo
            FREE_MAIL = convo.FREE_MAIL
        except Exception:
            FREE_MAIL = set()
    return FREE_MAIL


def _dom(a):
    a = (a or "").lower()
    return a.rsplit("@", 1)[-1] if "@" in a else ""


def check_archive_box(con, conf, password, people, own_domains, allow_domains, grace_minutes=30, fetcher=None):
    """Read the company archive mailbox (a copy of every supervisor email, set up in Google Workspace) and compare it with
    what the supervisors' BSOs reported. Returns new incidents. `people`: {supervisor email: user_id}."""
    leads = {}
    for r in con.execute("SELECT email, user_id FROM vis_leads"):
        leads.setdefault(r["email"], r["user_id"])
    for r in con.execute("SELECT DISTINCT lead_email FROM vis_messages WHERE lead_email IS NOT NULL"):
        leads.setdefault(r["lead_email"], None)
    lead_domains = {_dom(e) for e in leads if _dom(e) and _dom(e) not in _free()}
    raws = fetcher() if fetcher else _fetch_box(conf, password)
    found = 0
    for raw in raws:
        msg = _email.message_from_bytes(raw)
        mid = (msg.get("Message-ID") or "").strip()
        if not mid or con.execute("SELECT 1 FROM archive_box_seen WHERE msg_id=?", (mid,)).fetchone():
            continue
        try:
            when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
        except Exception:
            when = datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - when < timedelta(minutes=grace_minutes):
            continue                                    # give the supervisor's BSO time to report it first
        frm = parseaddr(msg.get("From", ""))[1].lower()
        rcpts = [a.lower() for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])) if "@" in a]
        everyone = [frm] + rcpts
        sup = next((a for a in everyone if a in people), None)
        lead = next((a for a in everyone if a in leads), None) or next((a for a in everyone if _dom(a) in lead_domains), None)
        con.execute("INSERT OR IGNORE INTO archive_box_seen(msg_id, at) VALUES (?,?)", (mid, now_iso()))
        if not (sup and lead):
            continue
        uid = people[sup]
        direction = "out" if frm == sup else "in"
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
                except Exception:
                    pass
        known = con.execute("SELECT 1 FROM vis_messages WHERE msg_id=?", (mid,)).fetchone()
        con.execute("INSERT OR IGNORE INTO vis_messages(user_id, local_id, lead_email, msg_id, direction, at, frm, to_, cc, bcc, subject, body, via, folder) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'archive','archive mailbox')",
                    (uid, None, lead, mid, direction, when.isoformat(timespec="seconds"), msg.get("From", ""), msg.get("To", ""), msg.get("Cc", ""),
                     msg.get("Bcc", ""), msg.get("Subject", ""), body[:30000]))
        snap = {"from": msg.get("From", ""), "to": msg.get("To", ""), "cc": msg.get("Cc", ""), "bcc": msg.get("Bcc", ""),
                "subject": msg.get("Subject", ""), "date": msg.get("Date", ""), "body": body[:20000]}
        if not known:
            add_incident(con, uid, None, when.isoformat(timespec="seconds"), "outside_bso", lead,
                         f"{sup} {'sent' if direction == 'out' else 'received'} this lead email, but their BSO never saw it (sent from another app and deleted, "
                         "or the mailbox isn't connected in BSO)", "company archive mailbox", snap)
            found += 1
        if direction == "out":
            allowed = set(own_domains) | set(allow_domains) | {_dom(lead)}
            for field in ("To", "Cc", "Bcc"):
                for _, a in getaddresses(msg.get_all(field, [])):
                    a = a.lower()
                    if "@" in a and _dom(a) not in allowed and a != lead:
                        add_incident(con, uid, None, when.isoformat(timespec="seconds"), f"{field.lower()}_outside", lead,
                                     f"{sup} put {a} in {field} on a lead email", "company archive mailbox", snap)
                        found += 1
    con.commit()
    return found


def _fetch_box(conf, password, days=3):
    out = []
    with imaplib.IMAP4_SSL(conf["host"], int(conf.get("port") or 993), ssl_context=ssl.create_default_context(), timeout=60) as M:
        M.login(conf["user"], password)
        M.select("INBOX", readonly=True)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = M.search(None, "SINCE", since)
        for num in (data[0].split() if data and data[0] else [])[-2000:]:
            _, msg = M.fetch(num, "(BODY.PEEK[])")
            if msg and isinstance(msg[0], tuple):
                out.append(msg[0][1])
    return out
