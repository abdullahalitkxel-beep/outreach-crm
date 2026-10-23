"""Following conversations instead of senders.

Every email BSO sends is written into a permanent ledger (sent_log): its hidden Message-ID, the lead, the company,
the campaign, step and version, the inbox and the text. When anything arrives, BSO works out whose conversation it
belongs to in this order:

    1. thread      the reply's In-Reply-To / References point at an email BSO sent (or received from the lead)
    2. sender      the sender is the lead, or an address linked to the lead (personal address, colleague)
    3. ref code    the "Ref: NS-7F3K" code from our footer appears anywhere in it (survives forwards)
    4. test tag    "[TEST for lead@x]" in the subject (test mode)
    5. quoted      our original is quoted inside it: a forwarded block "From: <our inbox> ... To: lead@x"
    6. company     someone at the same company (not gmail & co) writes to us; linked but flagged "please confirm"

It also records who else is in the conversation (CC, forwards), links personal addresses, sorts replies into
hot / warm / cold / closed with the follow-up timing they mention, and keeps an activity log per inbox.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
    "mail.com", "zoho.com", "yandex.com", "yandex.ru", "qq.com", "163.com", "126.com", "rediffmail.com", "web.de", "t-online.de",
    "orange.fr", "free.fr", "libero.it", "btinternet.com", "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "fastmail.com",
    "hey.com", "tutanota.com", "pm.me",
}
TEMPS = ("hot", "warm", "cold", "closed")
TEMP_ICON = {"hot": "🔥", "warm": "☀", "cold": "❄", "closed": "⛔"}
ADDR_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
REF_RE = re.compile(r"\bRef:?\s*([A-Z]{2,4}-[0-9A-Z]{5})\b")
TEST_TAG_RE = re.compile(r"\[TEST for ([^\],\s]+@[^\],\s]+)")
LIKELY_MONTHS = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_log (                  -- the touch ledger: every email BSO ever sent (kept when a lead is deleted)
    id INTEGER PRIMARY KEY, msg_id TEXT, lead_id INTEGER, lead_email TEXT, domain TEXT, campaign_id INTEGER, step INTEGER,
    variant TEXT, kind TEXT, sender TEXT, to_ TEXT, cc TEXT, bcc TEXT, subject TEXT, body TEXT, at TEXT, test INTEGER DEFAULT 0,
    angle TEXT, synced INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sent_log_msg ON sent_log(msg_id);
CREATE INDEX IF NOT EXISTS sent_log_lead ON sent_log(lead_id);
CREATE INDEX IF NOT EXISTS sent_log_domain ON sent_log(domain);
CREATE TABLE IF NOT EXISTS received (                  -- every email that arrived in a lead's conversation
    id INTEGER PRIMARY KEY, msg_id TEXT UNIQUE, lead_id INTEGER, inbox TEXT, folder TEXT, frm TEXT, to_ TEXT, cc TEXT,
    subject TEXT, body TEXT, at TEXT, label TEXT, temperature TEXT, how TEXT, path TEXT, synced INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lead_addresses (            -- other addresses of a lead: personal email, alias
    email TEXT PRIMARY KEY, lead_id INTEGER, how TEXT, confirmed INTEGER DEFAULT 0, at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS related_contacts (          -- other people in a lead's conversation (CC, forwards, replied for them)
    email TEXT, lead_id INTEGER, name TEXT, role TEXT, at TEXT, replied INTEGER DEFAULT 0, PRIMARY KEY (email, lead_id)
);
CREATE TABLE IF NOT EXISTS inbox_log (                 -- what BSO did with each email it saw, per inbox
    id INTEGER PRIMARY KEY, inbox TEXT, at TEXT, msg_id TEXT, frm TEXT, subject TEXT, decision TEXT, lead_id INTEGER, folder TEXT
);
CREATE INDEX IF NOT EXISTS inbox_log_inbox ON inbox_log(inbox, id);
CREATE TABLE IF NOT EXISTS temp_log (                  -- every temperature decision, by the bot or a person
    id INTEGER PRIMARY KEY, lead_id INTEGER, temperature TEXT, score INTEGER, reason TEXT, by TEXT, at TEXT
);
"""

LEAD_COLUMNS = (("temperature", "TEXT"), ("temp_reason", "TEXT"), ("check_back", "TEXT"), ("ref", "TEXT"),
                ("came_back", "TEXT"), ("journey", "TEXT"), ("deal_status", "TEXT"), ("lost_reason", "TEXT"),
                ("score", "REAL"), ("fit_score", "REAL"))


def ensure(con, add_column):
    con.executescript(SCHEMA)
    for col, decl in LEAD_COLUMNS:
        add_column(con, "leads", col, decl)
    for col, decl in (("priority", "TEXT"), ("due_at", "TEXT"), ("kind", "TEXT"), ("escalated", "INTEGER DEFAULT 0")):
        add_column(con, "tasks", col, decl)
    add_column(con, "outbox", "kind", "TEXT")
    add_column(con, "outbox", "angle", "TEXT")


# ---------------------------------------------------------------- small helpers

def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")      # same format as the rest of BSO


def domain_of(addr):
    a = str(addr or "").strip().lower()
    return a.rsplit("@", 1)[-1] if "@" in a else ""


def company_domain(addr):
    """The company part of an address, or '' for free / personal mail providers."""
    d = domain_of(addr)
    return "" if not d or d in FREE_MAIL else d


def norm_ids(*headers):
    out = []
    for h in headers:
        out += re.findall(r"<[^<>\s]+>", str(h or ""))
    return out


def addresses(msg, *fields):
    out = []
    for f in fields:
        for name, a in getaddresses(msg.get_all(f, []) or []):
            a = a.strip().lower()
            if "@" in a:
                out.append((name.strip(), a))
    return out


# ---------------------------------------------------------------- the ledger

def ref_code(con, lead_id, prefix="NS"):
    """A short reference that identifies the lead even after any number of forwards: e.g. NS-7F3KQ."""
    r = con.execute("SELECT ref FROM leads WHERE id=?", (lead_id,)).fetchone()
    if r and r["ref"]:
        return r["ref"]
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    n = int(hashlib.sha256(f"bso-ref:{lead_id}".encode()).hexdigest(), 16)
    for salt in range(50):
        code = prefix.upper()[:4] + "-" + "".join(alphabet[(n >> (5 * i + salt)) % 32] for i in range(5))
        if not con.execute("SELECT 1 FROM leads WHERE ref=?", (code,)).fetchone():
            con.execute("UPDATE leads SET ref=? WHERE id=?", (code, lead_id))
            return code
    return ""


def record_sent(con, msg_id, lead, kind, sender_email, to, subject, body, step=None, variant=None, cc=None, bcc=None, test=False, angle=None):
    lead = dict(lead) if lead else {}
    con.execute("INSERT INTO sent_log(msg_id, lead_id, lead_email, domain, campaign_id, step, variant, kind, sender, to_, cc, bcc, subject, body, at, test, angle) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msg_id, lead.get("id"), (lead.get("email") or "").lower(), company_domain(lead.get("email")), lead.get("campaign_id") or 1,
                 step, variant, kind, (sender_email or "").lower(), to, cc, bcc, subject, body, iso(now()), 1 if test else 0, angle))


def record_received(con, msg, lead_id, inbox, folder, frm, body, label=None, temperature=None, how=None, path=None):
    mid = (msg.get("Message-ID") or "").strip() or None
    to = ", ".join(a for _, a in addresses(msg, "To"))
    cc = ", ".join(a for _, a in addresses(msg, "Cc"))
    con.execute("INSERT OR IGNORE INTO received(msg_id, lead_id, inbox, folder, frm, to_, cc, subject, body, at, label, temperature, how, path) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, lead_id, inbox, folder, frm, to, cc, str(msg.get("Subject") or "")[:300], (body or "")[:20000], iso(now()),
                 label, temperature, how, json.dumps(path) if path else None))


def log_inbox(con, inbox, msg, decision, lead_id=None, folder="INBOX"):
    con.execute("INSERT INTO inbox_log(inbox, at, msg_id, frm, subject, decision, lead_id, folder) VALUES (?,?,?,?,?,?,?,?)",
                (inbox.lower(), iso(now()), (msg.get("Message-ID") or "").strip(), parseaddr(msg.get("From", ""))[1].lower(),
                 str(msg.get("Subject") or "")[:200], decision[:300], lead_id, folder))
    con.execute("DELETE FROM inbox_log WHERE inbox=? AND id < (SELECT COALESCE(MIN(id),0) FROM (SELECT id FROM inbox_log WHERE inbox=? ORDER BY id DESC LIMIT 500))",
                (inbox.lower(), inbox.lower()))


# ---------------------------------------------------------------- whose conversation is this?

def _lead(con, lid):
    return con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone() if lid else None


def _by_email(con, addr):
    addr = (addr or "").lower()
    row = con.execute("SELECT * FROM leads WHERE email=?", (addr,)).fetchone()
    if row:
        return row
    a = con.execute("SELECT lead_id FROM lead_addresses WHERE email=?", (addr,)).fetchone()
    return _lead(con, a["lead_id"]) if a else None


def _quoted_blocks(body):
    """Forwarded / quoted header blocks in the text, oldest last: [{'from':..., 'to': [...]}, ...]."""
    blocks, cur = [], None
    for line in (body or "").splitlines():
        s = line.strip().lstrip(">").strip()
        m = re.match(r"^(From|Von|De|Da|Van)\s*:\s*(.+)$", s, re.I)
        if m:
            cur = {"from": (ADDR_RE.findall(m.group(2)) or [""])[0].lower(), "to": []}
            blocks.append(cur)
            continue
        m = re.match(r"^(To|An|À|A|Cc|Aan)\s*:\s*(.+)$", s, re.I)
        if m and cur is not None:
            cur["to"] += [a.lower() for a in ADDR_RE.findall(m.group(2))]
        m = re.match(r"^On .+ wrote:\s*$", s)            # Gmail style "On Tue, ... <a@b> wrote:"
        if m:
            a = ADDR_RE.findall(s)
            if a:
                blocks.append({"from": a[-1].lower(), "to": []})
    return blocks


def match(cfg, con, msg, frm, inbox, body):
    """Returns {'lead': row|None, 'how': str, 'sure': bool, 'path': [addresses], 'note': str}."""
    ours = {s.get("email", "").lower() for s in cfg.get("senders") or [] if s.get("email")}
    ours.add((inbox or "").lower())
    out = {"lead": None, "how": "", "sure": False, "path": [], "note": ""}
    # 1. thread
    ids = norm_ids(msg.get("In-Reply-To"), msg.get("References"))
    if ids:
        q = ",".join("?" * len(ids))
        hit = con.execute(f"SELECT lead_id FROM sent_log WHERE msg_id IN ({q}) AND lead_id IS NOT NULL ORDER BY id DESC LIMIT 1", ids).fetchone() or \
            con.execute(f"SELECT id lead_id FROM leads WHERE first_msg_id IN ({q}) LIMIT 1", ids).fetchone() or \
            con.execute(f"SELECT lead_id FROM received WHERE msg_id IN ({q}) AND lead_id IS NOT NULL LIMIT 1", ids).fetchone()
        if hit and _lead(con, hit["lead_id"]):
            out.update(lead=_lead(con, hit["lead_id"]), how="thread", sure=True)
    # 2. sender
    if not out["lead"]:
        row = _by_email(con, frm)
        if row:
            out.update(lead=row, how="sender" if row["email"] == frm else "linked address", sure=True)
    # 3. reference code
    if not out["lead"]:
        m = REF_RE.search(f"{msg.get('Subject', '')}\n{body or ''}")
        row = con.execute("SELECT * FROM leads WHERE ref=?", (m.group(1),)).fetchone() if m else None
        if row:
            out.update(lead=row, how="reference code", sure=True)
    # 4. test tag
    tm = cfg.get("test_mode") or {}
    if not out["lead"] and tm.get("enabled") and not msg.get("X-BSO-Kind"):
        subj = re.sub(r"\s+", " ", msg.get("Subject", "") or "").strip()
        m = TEST_TAG_RE.search(subj)
        if m and re.match(r"^(re|aw|antw|sv|r|fw|fwd)\s*:", subj, re.I):
            row = _by_email(con, m.group(1))
            if row:
                out.update(lead=row, how="test tag", sure=True)
    # 5. our original quoted inside (forwards)
    blocks = _quoted_blocks(body)
    if not out["lead"] and blocks:
        for b in reversed(blocks):                      # the oldest block is our email
            if b["from"] in ours:
                for a in b["to"]:
                    row = _by_email(con, a)
                    if row:
                        out.update(lead=row, how="quoted original", sure=True)
                        break
            if out["lead"]:
                break
        if not out["lead"]:                             # any lead address quoted alongside one of our inboxes
            quoted = {a for b in blocks for a in [b["from"]] + b["to"]}
            if quoted & ours:
                for a in quoted - ours:
                    row = _by_email(con, a)
                    if row:
                        out.update(lead=row, how="quoted original", sure=True)
                        break
    # 6. same company
    if not out["lead"]:
        cd = company_domain(frm)
        if cd and cd not in {domain_of(o) for o in ours}:
            hit = con.execute("SELECT lead_id, at FROM sent_log WHERE domain=? AND lead_id IS NOT NULL ORDER BY id DESC LIMIT 1", (cd,)).fetchone()
            row = _lead(con, hit["lead_id"]) if hit else None
            if row:
                old = hit["at"] < iso(now() - timedelta(days=30 * LIKELY_MONTHS))
                out.update(lead=row, how="same company" + (" (over a year later)" if old else ""), sure=False,
                           note="possible connection" if old else "likely")
    # the path the email travelled before reaching us (abc -> def -> xyz)
    if blocks:
        chain = []
        for b in reversed(blocks):
            for a in [b["from"]]:
                if a and a not in ours and a not in chain:
                    chain.append(a)
        if frm not in chain:
            chain.append(frm)
        out["path"] = chain if len(chain) > 1 else []
    return out


def own_addresses(cfg):
    """Our own people and domains: never linked to a lead as 'their' address."""
    emails, domains = set(), set()
    for s in cfg.get("senders") or []:
        if s.get("email"):
            emails.add(s["email"].lower())
            domains.add(domain_of(s["email"]))
    li = cfg.get("lead_intake") or {}
    emails |= {a.strip().lower() for a in li.get("allowed_senders") or [] if a.strip()}
    tm = cfg.get("test_mode") or {}
    if tm.get("redirect_to"):
        emails.add(tm["redirect_to"].strip().lower())
    for s in (cfg.get("handover") or {}).get("supervisors") or []:
        if isinstance(s, dict) and s.get("email"):
            emails.add(s["email"].lower())
    try:
        import team
        for s in team.supervisors():
            if s.get("email"):
                emails.add(s["email"].lower())
    except Exception:
        pass
    domains |= {company_domain(e) for e in emails}
    domains.discard("")
    return emails, domains


def is_own(cfg, addr):
    emails, domains = own_addresses(cfg)
    a = (addr or "").lower()
    return a in emails or company_domain(a) in domains


def link_people(con, lead, msg, frm, how, sure, cfg=None):
    """Remember who else is in this lead's conversation."""
    lid, lemail = lead["id"], lead["email"]
    at = iso(now())
    own = own_addresses(cfg or {})
    mine = lambda a: a in own[0] or company_domain(a) in own[1]
    if frm and frm != lemail and not mine(frm):
        if company_domain(frm) and company_domain(frm) == company_domain(lemail):
            con.execute("INSERT OR IGNORE INTO related_contacts(email, lead_id, name, role, at, replied) VALUES (?,?,?,?,?,1)",
                        (frm, lid, parseaddr(msg.get("From", ""))[0], "replied", at))
            con.execute("UPDATE related_contacts SET replied=1 WHERE email=? AND lead_id=?", (frm, lid))
        elif not con.execute("SELECT 1 FROM lead_addresses WHERE email=?", (frm,)).fetchone():
            con.execute("INSERT OR IGNORE INTO lead_addresses(email, lead_id, how, confirmed, at, note) VALUES (?,?,?,?,?,?)",
                        (frm, lid, how, 1 if sure else 0, at, "wrote in the conversation"))
    for name, a in addresses(msg, "To", "Cc"):
        if a == lemail or "@" not in a or mine(a):
            continue
        if company_domain(a) and company_domain(a) == company_domain(lemail):
            con.execute("INSERT OR IGNORE INTO related_contacts(email, lead_id, name, role, at) VALUES (?,?,?,?,?)", (a, lid, name, "cc", at))


def add_path_contacts(con, lead, path):
    for a in path:
        if a != lead["email"] and company_domain(a) and company_domain(a) == company_domain(lead["email"]):
            con.execute("INSERT OR IGNORE INTO related_contacts(email, lead_id, name, role, at) VALUES (?,?,?,?,?)",
                        (a, lead["id"], "", "forward", iso(now())))


def protected(con, email):
    """True when this address is part of another lead's live conversation (no cold emails while a deal is open)."""
    r = con.execute("SELECT 1 FROM related_contacts rc JOIN leads l ON l.id=rc.lead_id WHERE rc.email=? AND rc.lead_id != "
                    "COALESCE((SELECT id FROM leads WHERE email=?), -1) AND l.status NOT IN ('unsubscribed','bounced') "
                    "AND COALESCE(l.temperature,'') != 'closed' AND COALESCE(l.deal_status,'') NOT IN ('lost')", (email.lower(), email.lower())).fetchone()
    return bool(r)


def company_stop(con, lead, why):
    """Someone at this company replied: stop cold emails to everyone else there."""
    cd = company_domain(lead["email"])
    if not cd:
        return 0
    rows = con.execute("SELECT id FROM leads WHERE id!=? AND status='active' AND lower(email) LIKE ? "
                       "AND COALESCE(json_extract(extra, '$.referred_by_email'), '') != ?",      # the people they pointed us to carry on
                       (lead["id"], "%@" + cd, lead["email"])).fetchall()
    for r in rows:
        con.execute("UPDATE leads SET status='paused' WHERE id=?", (r["id"],))
        con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                    (r["id"], f"Emails paused: {why}. They're part of that conversation now.", iso(now())))
    return len(rows)


def previously(con, email):
    """What we've sent this person or their company before (for new imports and late returns)."""
    e = (email or "").lower()
    me = con.execute("SELECT * FROM sent_log WHERE (lead_email=? OR lead_id IN (SELECT lead_id FROM lead_addresses WHERE email=?)) AND test=0 "
                     "ORDER BY id", (e, e)).fetchall()
    cd = company_domain(e)
    co = con.execute("SELECT COUNT(DISTINCT lead_email) FROM sent_log WHERE domain=? AND lead_email!=? AND test=0", (cd, e)).fetchone()[0] if cd else 0
    return {"touches": [dict(r) for r in me], "company_contacts": co}


def journey(con, lead_id):
    """First touch, last touch and everything in between, for credit and the timeline."""
    t = [dict(r) for r in con.execute("SELECT s.*, c.name campaign FROM sent_log s LEFT JOIN campaigns c ON c.id=s.campaign_id "
                                      "WHERE s.lead_id=? AND s.test=0 ORDER BY s.id", (lead_id,))]
    r = [dict(x) for x in con.execute("SELECT * FROM received WHERE lead_id=? ORDER BY id", (lead_id,))]
    return {"touches": t, "replies": r, "first": t[0] if t else None, "last": t[-1] if t else None}


# ---------------------------------------------------------------- temperature

HOT_RE = re.compile(r"\b(call|meet(ing)?|schedule|calendar|available|free (on|at)|tomorrow|next week|monday|tuesday|wednesday|thursday|friday|"
                    r"zoom|teams|demo|price|pricing|cost|quote|quotation|proposal|contract|rates?|budget (for|is)|asap|urgent|"
                    r"this (week|month|quarter)|timeline|start (on|by|in)|how soon|send (me|us) (a|the) (quote|proposal))\b", re.I)
COLD_RE = re.compile(r"\b(not (right )?now|not at the moment|not at this time|maybe later|later this year|next year|no budget|"
                     r"budget (is )?(frozen|cut)|already (have|work with|use)|have a (vendor|partner|supplier)|in-house|not a priority|"
                     r"reach out (again )?in|circle back|check back|get back to (me|us) in|not interested|no thanks|no thank you|"
                     r"not looking|we'?re good|all set|pass)\b", re.I)
CLOSED_RE = re.compile(r"\b(unsubscribe|remove me|stop (emailing|contacting|sending)|take me off|do not contact|don'?t contact|"
                       r"never (email|contact)|spam|report(ed)? (you|this))\b", re.I)
SENIOR_RE = re.compile(r"\b(ceo|cto|cio|coo|cfo|chief|founder|co-founder|owner|president|vp|vice president|head of|director|partner)\b", re.I)

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                      "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def _next_date(month, day=1, today=None):
    today = today or date.today()
    y = today.year if (month, day) > (today.month, today.day) else today.year + 1
    try:
        return date(y, month, day)
    except ValueError:
        return date(y, month, 28)


def timing(text, today=None):
    """A follow-up date someone mentions: 'after Q1', 'next month', 'in March', 'in 6 weeks', 'next year'."""
    t = (text or "").lower()
    today = today or date.today()
    m = re.search(r"\b(after|end of|beyond|in|by|until|from)?\s*q([1-4])\b", t)
    if m:
        q = int(m.group(2))
        start = date(today.year, 3 * (q - 1) + 1, 1)
        after = m.group(1) in ("after", "end of", "beyond", "until")
        d = date(today.year + (3 * q) // 12, (3 * q) % 12 + 1, 1) if after else start
        if d <= today:
            d = date(d.year + 1, d.month, 1)
        return d
    m = re.search(r"\bin (\d{1,2}|a|one|two|three|four|six) (week|month)s?\b", t)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6}.get(m.group(1)) or int(m.group(1))
        return today + (timedelta(weeks=n) if m.group(2) == "week" else timedelta(days=30 * n))
    if re.search(r"\bnext month\b", t):
        return date(today.year + today.month // 12, today.month % 12 + 1, 1)
    if re.search(r"\bnext quarter\b", t):
        q = (today.month - 1) // 3 + 1
        return date(today.year + (3 * q) // 12, (3 * q) % 12 + 1, 1)
    if re.search(r"\bnext year\b|\bin the new year\b", t):
        return date(today.year + 1, 1, 15)
    m = re.search(r"\b(?:in|after|from|until|end of|early|mid|late)\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b", t)
    if m:
        return _next_date(MONTHS[m.group(1)], 1, today)
    return None


def return_date(text, today=None):
    """The 'back on ...' date in an out-of-office reply."""
    t = (text or "").lower()
    today = today or date.today()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"\b(?:back|return(?:ing)?|in the office|available)\b[^.\n]{0,40}?\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", t)
    if m:
        d_, mo = int(m.group(1)), int(m.group(2))
        if mo > 12:
            d_, mo = mo, d_
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            r = date(y, mo, d_)
            return r if r >= today else date(y + 1, mo, d_)
        except ValueError:
            pass
    names = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(r"\b(?:back|return(?:ing)?|until|in the office|available)\b[^.\n]{0,40}?\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(" + names + r")|(" + names +
                  r")\s+(\d{1,2})(?:st|nd|rd|th)?)\b", t)
    if m:
        d_ = int(m.group(1) or m.group(4))
        mo = MONTHS[m.group(2) or m.group(3)]
        return _next_date(mo, d_, today - timedelta(days=1))
    return None


def temperature(label, body, lead=None, title="", engaged=0, ai=None, cold_days=90):
    """('hot'|'warm'|'cold'|'closed', score 0-100, reason, check_back date or None)."""
    text = body or ""
    import guard
    if label == "unsubscribe" or guard.stop_reason("", text):           # the same stop rules everywhere, decided in code
        return "closed", 0, "asked not to be contacted", None
    base = {"meeting_request": 80, "interested": 55, "question": 50, "referral": 45, "other": 42, "not_interested": 15,
            "out_of_office": 30}.get(label, 35)
    reasons = []
    hot = HOT_RE.findall(text)
    cold = COLD_RE.findall(text)
    if hot:
        base += 20
        reasons.append("asks about " + ", ".join(sorted({h[0].lower() for h in hot}))[:60])
    if cold:
        base -= 25
        reasons.append("says " + ", ".join(sorted({c[0].lower() for c in cold}))[:60])
    if SENIOR_RE.search(title or ""):
        base += 10
        reasons.append("senior title")
    if engaged:
        base += min(15, 5 * engaged)
        reasons.append("more people / replies from the company")
    if ai and ai.get("temperature") in TEMPS:               # the AI's view counts as much as the signals
        ai_score = {"hot": 85, "warm": 55, "cold": 20, "closed": 0}[ai["temperature"]]
        base = round((base + ai_score) / 2)
        if ai.get("reason"):
            reasons.insert(0, str(ai["reason"])[:120])
    score = max(0, min(100, base))
    temp = "hot" if score >= 70 else "warm" if score >= 40 else "cold"
    when = None
    if temp == "cold":
        when = (ai or {}).get("check_back") and _parse_iso(ai["check_back"]) or timing(text) or (date.today() + timedelta(days=cold_days))
    return temp, score, "; ".join(reasons) or label.replace("_", " "), when


def _parse_iso(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def add_working_hours(start, hours, day_start=9, day_end=18):
    """start + N working hours (Mon-Fri, 9-18 local time of the machine doing the work)."""
    t = start
    left = timedelta(hours=hours)
    while left > timedelta(0):
        if t.weekday() >= 5 or t.hour >= day_end:
            t = (t + timedelta(days=1)).replace(hour=day_start, minute=0, second=0)
            continue
        if t.hour < day_start:
            t = t.replace(hour=day_start, minute=0, second=0)
        end = t.replace(hour=day_end, minute=0, second=0)
        step = min(left, end - t)
        t += step
        left -= step
    return t


PLAYBOOK = {   # hours to respond, how many follow-up nudges and when (days), escalate after (hours)
    "hot": {"respond_hours": 2, "escalate_hours": 4, "nudges": [1, 3]},
    "warm": {"respond_hours": 9, "escalate_hours": 18, "nudges": [3, 7, 14]},
    "cold": {"check_back_days": 90},
}


def playbook(cfg):
    pb = json.loads(json.dumps(PLAYBOOK))
    for k, v in ((cfg.get("playbooks") or {}).items() if isinstance(cfg.get("playbooks"), dict) else []):
        if k in pb and isinstance(v, dict):
            pb[k].update(v)
    return pb


def set_temperature(con, lead_id, temp, score, reason, by="bot", check_back=None):
    con.execute("UPDATE leads SET temperature=?, temp_reason=?, score=COALESCE(?, score), check_back=? WHERE id=?",
                (temp, (reason or "")[:300], score, check_back.isoformat() if isinstance(check_back, date) else check_back, lead_id))
    con.execute("INSERT INTO temp_log(lead_id, temperature, score, reason, by, at) VALUES (?,?,?,?,?,?)",
                (lead_id, temp, score, (reason or "")[:300], by, iso(now())))
