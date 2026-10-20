"""The supervisor's own mailbox, inside BSO: every email with a handed-over prospect (both directions,
including replies sent from a normal email app) is collected into the prospect's conversation, and
replies can be sent from BSO in the same thread.

Settings → Handover → "Your mailbox". The password is stored encrypted like the other inbox passwords.
"""
import hashlib
import imaplib
import re
from datetime import timedelta
from email import message_from_bytes
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import bot
from bot import env, get_text_body, iso, log, log_event, now_utc

PW_KEY = "PROSPECT_MAILBOX_PASSWORD"
LOOKBACK_DAYS = 45
MAX_PER_FOLDER = 400


def conf(cfg):
    m = dict(cfg.get("prospect_mailbox") or {})
    m["password"] = env(PW_KEY)
    if not (m.get("email") and m.get("smtp_host") and m.get("imap_host") and m["password"]):
        return None
    m.setdefault("name", m["email"])
    m["smtp_port"] = int(m.get("smtp_port") or 465)
    m["imap_port"] = int(m.get("imap_port") or 993)
    return m


def prospects(con):
    return {r["email"].lower(): r["id"] for r in con.execute("SELECT id, email FROM leads WHERE handed_from IS NOT NULL")}


# ---------------------------------------------------------------- reading the mailbox

def _sent_folder(M):
    """The Sent folder, by its special-use flag (works for Gmail, Outlook, SiteGround…), else by common names."""
    typ, data = M.list()
    names = []
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\) "?(?P<sep>[^" ]*)"? (?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip()
        if "\\sent" in m.group("flags").lower():
            return name
        names.append(name)
    for want in ("[Gmail]/Sent Mail", "Sent", "Sent Items", "Sent Messages", "INBOX.Sent", "INBOX/Sent"):
        for n in names:
            if n.strip('"').lower() == want.lower():
                return n
    return None


def fetch(c, since_days=LOOKBACK_DAYS):
    """[(folder kind 'in'|'sent', raw message bytes)] from the last weeks. Separate so tests can replace it."""
    out = []
    M = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], timeout=30)
    try:
        M.login(c.get("username") or c["email"], c["password"])
        since = (now_utc() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        for kind, folder in (("in", "INBOX"), ("sent", _sent_folder(M))):
            if not folder:
                continue
            if M.select(folder, readonly=True)[0] != "OK":
                continue
            typ, data = M.search(None, "SINCE", since)
            ids = (data[0].split() if data and data[0] else [])[-MAX_PER_FOLDER:]
            for i in ids:
                typ, msg = M.fetch(i, "(BODY.PEEK[])")
                if typ == "OK" and msg and isinstance(msg[0], tuple):
                    out.append((kind, msg[0][1]))
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


def match(msg, people, me):
    """(prospect email, 'in'|'out') if this message belongs to a prospect's conversation, else None."""
    kind = (msg.get("X-BSO-Kind") or "").lower()
    if kind == "brief":
        return None
    tagged = (msg.get("X-BSO-Prospect") or "").strip().lower()
    if kind == "forward" and tagged in people:
        return tagged, "in"
    frm = parseaddr(msg.get("From", ""))[1].lower()
    if frm in people:
        return frm, "in"
    for _, addr in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])):
        if addr.lower() in people:
            return addr.lower(), "out"
    return None


def _when(msg):
    try:
        return iso(parsedate_to_datetime(msg.get("Date")))
    except Exception:
        return iso(now_utc())


def _clean(body):
    """Drop the quoted history below a reply so the conversation stays readable."""
    lines = []
    for line in body.replace("\r\n", "\n").split("\n"):
        if re.match(r"^\s*On .{5,200} wrote:\s*$", line) or line.strip().startswith("-----Original Message-----") or re.match(r"^\s*From: .+", line) and lines:
            break
        if line.startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:20000]


def store(con, raw, people, me):
    msg = message_from_bytes(raw)
    hit = match(msg, people, me)
    if not hit:
        return None
    email_, direction = hit
    mid = (msg.get("Message-ID") or "").strip() or "<" + hashlib.sha1(raw).hexdigest() + "@bso>"
    body = get_text_body(msg)
    if (msg.get("X-BSO-Kind") or "").lower() == "forward":
        body = body.split("--- Their message ---", 1)[-1]
    cur = con.execute("INSERT OR IGNORE INTO messages(lead_id, msg_id, direction, at, frm, to_, cc, subject, body, seen, via) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (people[email_], mid, direction, _when(msg), msg.get("From", ""), msg.get("To", ""), msg.get("Cc", ""),
                       msg.get("Subject", ""), _clean(body), 0 if direction == "in" else 1, "mailbox"))
    return direction if cur.rowcount else None


def check(cfg, con):
    """Collect new emails with prospects. Returns (new incoming, message). Never raises."""
    c = conf(cfg)
    if not c:
        return 0, "Connect your mailbox in Settings → Handover first."
    people = prospects(con)
    if not people:
        return 0, "No handed-over prospects yet."
    try:
        raws = fetch(c)
    except imaplib.IMAP4.error as e:
        return 0, f"Your mailbox refused the login ({e}). Check the password in Settings → Handover."
    except Exception as e:
        return 0, f"Couldn't read your mailbox ({e})."
    new_in = 0
    for _, raw in raws:
        try:
            if store(con, raw, people, c["email"]) == "in":
                new_in += 1
        except Exception as e:
            log.warning("mailbox message skipped: %s", e)
    con.commit()
    return new_in, (f"{new_in} new repl{'y' if new_in == 1 else 'ies'} from prospects." if new_in else "Mailbox checked: nothing new.")


# ---------------------------------------------------------------- replying from BSO

def send_reply(cfg, con, lead_id, body, cc=""):
    """Send from the supervisor's mailbox in the prospect's thread. Returns (ok, message)."""
    c = conf(cfg)
    if not c:
        return False, "Connect your mailbox in Settings → Handover first."
    lead = con.execute("SELECT * FROM leads WHERE id=? AND handed_from IS NOT NULL", (lead_id,)).fetchone()
    if not lead or not body.strip():
        return False, "Write a message first."
    last = con.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY at DESC, id DESC LIMIT 1", (lead_id,)).fetchone()
    base = (last["subject"] if last and last["subject"] else None) or lead["first_subject"] or "Following up"
    subject = base if base.lower().startswith("re:") else "Re: " + base
    tm = cfg.get("test_mode") or {}
    redirect = tm.get("redirect_to") if tm.get("enabled") else None
    sender = {"email": c["email"], "name": c["name"], "smtp_host": c["smtp_host"], "smtp_port": c["smtp_port"],
              "password": c["password"], "username": c.get("username") or c["email"]}
    to, cc = lead["email"], (cc or "").strip()
    try:
        if redirect:
            mid = bot.send_email(sender, redirect, f"[TEST for {to}] {subject}", body.strip(), last["msg_id"] if last else None)
        else:
            mid = bot.send_email(sender, to, subject, body.strip(), last["msg_id"] if last else None, cc=cc or None)
    except Exception as e:
        return False, f"Couldn't send: {e}"
    con.execute("INSERT OR IGNORE INTO messages(lead_id, msg_id, direction, at, frm, to_, cc, subject, body, seen, via) VALUES (?,?,?,?,?,?,?,?,?,1,'bso')",
                (lead_id, mid, "out", iso(now_utc()), f"{c['name']} <{c['email']}>", to, cc, subject, body.strip()))
    if (lead["prospect_stage"] or "new") == "new":
        con.execute("UPDATE leads SET prospect_stage='contacted' WHERE id=?", (lead_id,))
        log_event(con, lead_id, "prospect_stage", "contacted")
        con.execute("UPDATE tasks SET status='done', done_at=? WHERE lead_id=? AND status='open' AND subject LIKE 'Contact %'", (iso(now_utc())[:10], lead_id))
    con.commit()
    return True, ("Sent to your test address (test mode)." if redirect else f"Sent to {to}.")
