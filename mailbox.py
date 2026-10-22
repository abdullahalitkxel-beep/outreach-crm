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


class People(dict):
    """Prospects (email -> lead id) plus everything else that ties an email to a prospect: their other addresses,
    their colleagues, their company's domain and the threads already in the conversation."""
    def __init__(self, con):
        super().__init__(prospects(con))
        import convo
        ids = set(self.values())
        self.aliases, self.domains, self.threads = {}, {}, {}
        if not ids:
            return
        q = ",".join("?" * len(ids))
        for r in con.execute(f"SELECT email, lead_id FROM lead_addresses WHERE lead_id IN ({q})", tuple(ids)):
            self.aliases[r["email"].lower()] = r["lead_id"]
        for r in con.execute(f"SELECT email, lead_id FROM related_contacts WHERE lead_id IN ({q})", tuple(ids)):
            self.aliases.setdefault(r["email"].lower(), r["lead_id"])
        for e, lid in self.items():
            d = convo.company_domain(e)
            if d:
                self.domains.setdefault(d, lid)
        for r in con.execute(f"SELECT msg_id, lead_id FROM messages WHERE lead_id IN ({q})", tuple(ids)):
            if r["msg_id"]:
                self.threads[r["msg_id"]] = r["lead_id"]
        self.by_id = {v: k for k, v in self.items()}

    def lead_for(self, addr):
        a = (addr or "").lower()
        if a in self:
            return self[a]
        if a in self.aliases:
            return self.aliases[a]
        import convo
        return self.domains.get(convo.company_domain(a))


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


def _trash_folder(M):
    typ, data = M.list()
    names = []
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\) "?(?P<sep>[^" ]*)"? (?P<name>.+)$', line)
        if not m:
            continue
        if "\\trash" in m.group("flags").lower():
            return m.group("name").strip()
        names.append(m.group("name").strip())
    for want in ("[Gmail]/Trash", "[Gmail]/Bin", "Trash", "Deleted Items", "Deleted Messages", "INBOX.Trash", "INBOX/Trash"):
        for n in names:
            if n.strip('"').lower() == want.lower():
                return n
    return None


def fetch_trash_ids(c, since_days=LOOKBACK_DAYS):
    """Message-IDs in the Trash folder (to tell 'moved to Trash' from 'permanently deleted'). Tests can replace it."""
    ids = set()
    M = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], timeout=30)
    try:
        M.login(c.get("username") or c["email"], c["password"])
        folder = _trash_folder(M)
        if folder and M.select(folder, readonly=True)[0] == "OK":
            since = (now_utc() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            _, data = M.search(None, "SINCE", since)
            for i in (data[0].split() if data and data[0] else [])[-2000:]:
                _, hdr = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                if hdr and isinstance(hdr[0], tuple):
                    m = re.search(rb"<[^>]+>", hdr[0][1])
                    if m:
                        ids.add(m.group(0).decode(errors="replace"))
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return ids


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
    """(prospect email, 'in'|'out') if this message belongs to a prospect's conversation, else None.
    Follows the thread first, then the prospect's addresses, their other addresses and colleagues, then their company."""
    kind = (msg.get("X-BSO-Kind") or "").lower()
    if kind == "brief":
        return None
    tagged = (msg.get("X-BSO-Prospect") or "").strip().lower()
    if kind == "forward" and tagged in people:
        return tagged, "in"
    frm = parseaddr(msg.get("From", ""))[1].lower()
    mine = frm == (me or "").lower()
    by_id = getattr(people, "by_id", None) or {v: k for k, v in people.items()}
    sender_lid = None if mine else (people.lead_for(frm) if hasattr(people, "lead_for") else people.get(frm))
    direction = "in" if sender_lid in by_id else "out"         # from the client's side = in; from us (anyone at our end) = out
    for ref in re.findall(r"<[^<>\s]+>", f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"):
        lid = getattr(people, "threads", {}).get(ref)
        if lid in by_id:
            return by_id[lid], direction
    if sender_lid in by_id:
        return by_id[sender_lid], "in"
    for _, addr in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])):
        lid = people.lead_for(addr) if hasattr(people, "lead_for") else people.get(addr.lower())
        if lid in by_id:
            return by_id[lid], "out"
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


def allowed_domains(cfg, lead_email, me):
    """Where a supervisor may send a prospect's emails: the prospect's company, our company and the allow-list."""
    import convo
    import team
    sec = team.security() or {}
    ds = {d.strip().lower() for d in str(sec.get("allow_domains") or "").replace(";", ",").split(",") if d.strip()}
    ds |= {d.lower() for d in sec.get("own_domains") or []}
    ds |= {convo.domain_of(me)} | {convo.domain_of(s.get("email")) for s in cfg.get("senders") or [] if s.get("email")}
    d = convo.company_domain(lead_email)
    if d:
        ds.add(d)
    ds.discard("")
    return ds


def outside(cfg, con, lead_id, lead_email, me, addrs):
    """The addresses among `addrs` that are outside the company, the prospect's company, their own addresses and the allow-list."""
    import convo
    ok = allowed_domains(cfg, lead_email, me)
    own = {r["email"].lower() for r in con.execute("SELECT email FROM lead_addresses WHERE lead_id=?", (lead_id,))} | {lead_email.lower()}
    return [a for a in addrs if a and a.lower() not in own and convo.domain_of(a) not in ok]


def store(con, raw, people, me, folder="", cfg=None):
    msg = message_from_bytes(raw)
    hit = match(msg, people, me)
    if not hit:
        return None
    email_, direction = hit
    mid = (msg.get("Message-ID") or "").strip() or "<" + hashlib.sha1(raw).hexdigest() + "@bso>"
    body = get_text_body(msg)
    if (msg.get("X-BSO-Kind") or "").lower() == "forward":
        body = body.split("--- Their message ---", 1)[-1]
    lid = people[email_]
    cur = con.execute("INSERT OR IGNORE INTO messages(lead_id, msg_id, direction, at, frm, to_, cc, bcc, subject, body, seen, via, folder) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (lid, mid, direction, _when(msg), msg.get("From", ""), msg.get("To", ""), msg.get("Cc", ""), msg.get("Bcc", ""),
                       msg.get("Subject", ""), _clean(body), 0 if direction == "in" else 1, "mailbox", folder))
    if not cur.rowcount:
        return None
    if direction == "out" and cfg is not None and parseaddr(msg.get("From", ""))[1].lower() == (me or "").lower():   # their own email: any copy outside?
        import report
        snap = {"from": msg.get("From", ""), "to": msg.get("To", ""), "cc": msg.get("Cc", ""), "bcc": msg.get("Bcc", ""),
                "subject": msg.get("Subject", ""), "date": msg.get("Date", ""), "body": body[:20000]}
        fwd = re.match(r"^\s*(fw|fwd|wg|tr)\s*:", msg.get("Subject", "") or "", re.I)
        for field in ("To", "Cc", "Bcc"):
            addrs = [a.lower() for _, a in getaddresses(msg.get_all(field, [])) if "@" in a]
            for a in outside(cfg, con, lid, email_, me, addrs):
                kind = "forward_outside" if fwd else f"{field.lower()}_outside"
                report.incident(con, kind, email_, f"{me} {'forwarded this lead conversation to' if fwd else 'put'} {a}"
                                f"{'' if fwd else ' in ' + field} (outside the company and the client)", "found in the Sent folder", snap)
                log_event(con, lid, "incident", f"{kind}: {a}")
    return direction


def check(cfg, con):
    """Collect new emails with prospects. Returns (new incoming, message). Never raises."""
    c = conf(cfg)
    if not c:
        return 0, "Connect your mailbox in Settings → Handover first."
    people = People(con)
    if not people:
        return 0, "No handed-over prospects yet."
    try:
        raws = fetch(c)
    except imaplib.IMAP4.error as e:
        return 0, f"Your mailbox refused the login ({e}). Check the password in Settings → Handover."
    except Exception as e:
        return 0, f"Couldn't read your mailbox ({e})."
    new_in = 0
    present = set()
    for kind, raw in raws:
        try:
            m = re.search(rb"(?im)^message-id:\s*(<[^>]+>)", raw[:8000])
            if m:
                present.add(m.group(1).decode(errors="replace"))
            if store(con, raw, people, c["email"], "Sent" if kind == "sent" else "INBOX", cfg) == "in":
                new_in += 1
        except Exception as e:
            log.warning("mailbox message skipped: %s", e)
    con.commit()
    try:
        track_deletions(cfg, con, c, present, len(raws))
    except Exception as e:
        log.warning("deletion check skipped: %s", e)
    try:
        import team
        st = team.state()
        st["mailbox_checked"] = iso(now_utc())
        team.save_state(st)
    except Exception:
        pass
    return new_in, (f"{new_in} new repl{'y' if new_in == 1 else 'ies'} from prospects." if new_in else "Mailbox checked: nothing new.")


def track_deletions(cfg, con, c, present, n_fetched):
    """Lead emails BSO saw in the mailbox that are no longer there: moved to Trash or permanently deleted."""
    if n_fetched >= MAX_PER_FOLDER:            # only part of the mailbox was read: can't tell what's gone
        return 0
    window = iso(now_utc() - timedelta(days=LOOKBACK_DAYS - 2))
    settled = iso(now_utc() - timedelta(minutes=10))
    gone = [r for r in con.execute("SELECT m.*, l.email lead_email FROM messages m JOIN leads l ON l.id=m.lead_id WHERE m.via='mailbox' "
                                   "AND m.deleted_at IS NULL AND m.at>=? AND m.at<=?", (window, settled)) if r["msg_id"] not in present]
    if not gone:
        return 0
    trash = fetch_trash_ids(c)
    import report
    for r in gone:
        how = "trashed" if r["msg_id"] in trash else "deleted"
        con.execute("UPDATE messages SET deleted_at=?, deleted_how=? WHERE id=?", (iso(now_utc()), how, r["id"]))
        snap = {"from": r["frm"], "to": r["to_"], "cc": r["cc"], "bcc": r["bcc"], "subject": r["subject"], "date": r["at"], "body": r["body"]}
        report.incident(con, how, r["lead_email"], f"{c['email']} {'moved to Trash' if how == 'trashed' else 'permanently deleted'} a "
                        f"{'received' if r['direction'] == 'in' else 'sent'} email with {r['lead_email']} (BSO kept its copy)", "mailbox check", snap)
        log_event(con, r["lead_id"], "incident", f"{how}: {r['subject']}")
    con.commit()
    return len(gone)


# ---------------------------------------------------------------- replying from BSO

def send_reply(cfg, con, lead_id, body, cc="", bcc=""):
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
    to, cc, bcc = lead["email"], (cc or "").strip(), (bcc or "").strip()
    extra = [a.strip().lower() for a in re.split(r"[,;\s]+", f"{cc},{bcc}") if "@" in a]
    bad = outside(cfg, con, lead_id, to, c["email"], extra)
    if bad:
        import report
        report.incident(con, "blocked", to, f"{c['email']} tried to copy {', '.join(bad)} on an email to {to}: not sent",
                        "blocked in BSO before sending", {"from": c["email"], "to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body.strip()[:20000]})
        log_event(con, lead_id, "incident", f"blocked: {', '.join(bad)}")
        con.commit()
        return False, (f"Not sent: {', '.join(bad)} is outside the company and this client. Client emails can only go to the client, "
                       "their colleagues and our own company. Ask your manager to add a partner's domain to the allowed list if needed.")
    try:
        if redirect:
            mid = bot.send_email(sender, redirect, f"[TEST for {to}] {subject}", body.strip(), last["msg_id"] if last else None)
        else:
            mid = bot.send_email(sender, to, subject, body.strip(), last["msg_id"] if last else None, cc=cc or None,
                                 headers={"Bcc": bcc} if bcc else None)
    except Exception as e:
        return False, f"Couldn't send: {e}"
    con.execute("INSERT OR IGNORE INTO messages(lead_id, msg_id, direction, at, frm, to_, cc, bcc, subject, body, seen, via) VALUES (?,?,?,?,?,?,?,?,?,?,1,'bso')",
                (lead_id, mid, "out", iso(now_utc()), f"{c['name']} <{c['email']}>", to, cc, bcc, subject, body.strip()))
    if (lead["prospect_stage"] or "new") == "new":
        con.execute("UPDATE leads SET prospect_stage='contacted' WHERE id=?", (lead_id,))
        log_event(con, lead_id, "prospect_stage", "contacted")
        con.execute("UPDATE tasks SET status='done', done_at=? WHERE lead_id=? AND status='open' AND subject LIKE 'Contact %'", (iso(now_utc())[:10], lead_id))
    con.commit()
    return True, ("Sent to your test address (test mode)." if redirect else f"Sent to {to}.")
