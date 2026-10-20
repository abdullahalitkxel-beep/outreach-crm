#!/usr/bin/env python3
"""
Outreach bot: personalized cold-email sequences with follow-ups, reply detection,
bounce handling and a suppression list.

Commands:
  python bot.py import leads.csv      # add leads
  python bot.py run [--dry-run]       # check replies, then send whatever is due
  python bot.py check                 # only check inboxes for replies/bounces
  python bot.py stats                 # campaign numbers
  python bot.py suppress a@b.com      # never email this address
"""
import argparse
import csv
import email
import imaplib
import json
import logging
import os
import random
import re
import smtplib
import socket
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv

# ROOT = where the code (and default config.yaml) lives.
# DATA = where YOUR data lives: database, settings, keys. In the Windows/Mac app this is
#        %APPDATA%\OutreachCRM (or ~/Library/Application Support/OutreachCRM); from source it's this folder.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _package_family():
    """Package family name when running as a Microsoft Store (MSIX) app, else None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        n = wintypes.UINT(0)
        if ctypes.windll.kernel32.GetCurrentPackageFamilyName(ctypes.byref(n), None) == 15700:  # no package
            return None
        buf = ctypes.create_unicode_buffer(n.value)
        if ctypes.windll.kernel32.GetCurrentPackageFamilyName(ctypes.byref(n), buf) == 0:
            return buf.value
    except Exception:
        pass
    return None


def _data_dir():
    pfn = None if os.getenv("OUTREACH_HOME") else _package_family()
    if os.getenv("OUTREACH_HOME"):
        d = Path(os.environ["OUTREACH_HOME"])
    elif pfn:  # Store app: the package's own data folder (removed cleanly on uninstall)
        d = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Packages" / pfn / "LocalState" / "OutreachCRM"
    elif getattr(sys, "frozen", False):
        if sys.platform == "win32":
            d = Path(os.getenv("APPDATA", Path.home())) / "OutreachCRM"
        elif sys.platform == "darwin":
            d = Path.home() / "Library" / "Application Support" / "OutreachCRM"
        else:
            d = Path.home() / ".outreachcrm"
    else:
        d = Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", ".env.example"):  # first run of the app: copy defaults
        if not (d / name).exists() and (ROOT / name).exists():
            (d / name).write_bytes((ROOT / name).read_bytes())
    return d


DATA = _data_dir()
load_dotenv(DATA / ".env")  # non-secret overrides only; secrets live encrypted in secrets.dat

from secretstore import SecretStore  # noqa: E402

SECRETS = SecretStore(DATA)
try:
    SECRETS.migrate_env_file(DATA / ".env")
except Exception as _e:
    print("secrets migration:", _e, file=sys.stderr)
SECRETS.apply_to_environ()
log = logging.getLogger("outreach")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FIND_EMAILS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CORE_FIELDS = {"email", "first_name", "company", "website"}
REPLY_LABELS = ["interested", "meeting_request", "question", "not_interested", "unsubscribe", "out_of_office", "referral", "other"]
HANDOVER_LABELS = ("interested", "meeting_request", "question")


# ---------------------------------------------------------------- helpers

def env(name):
    """Read a secret from .env, treating untouched placeholders (sk-ant-..., your-app-password) as missing."""
    v = (os.getenv(name) or "").strip()
    return "" if (not v or "..." in v or v.lower().startswith("your")) else v


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class SafeDict(dict):
    """format_map dict that leaves unknown placeholders blank instead of crashing."""
    def __missing__(self, key):
        return ""


def _deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def settings_path(config_path):
    return Path(config_path).with_name("settings.yaml")


def load_config(path):
    """config.yaml (defaults, hand-editable) overlaid with settings.yaml (written by the app's Settings page)."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sp = settings_path(path)
    if sp.exists():
        with open(sp, encoding="utf-8") as f:
            _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    first_name TEXT, company TEXT, website TEXT,
    extra TEXT DEFAULT '{}',
    status TEXT DEFAULT 'active',      -- active | done | replied | not_interested | unsubscribed | bounced
    step INTEGER DEFAULT 0,            -- index of the next sequence step to send
    next_send_at TEXT,
    sender TEXT,                       -- inbox that owns this lead (follow-ups use the same one)
    personal_line TEXT,
    first_msg_id TEXT,
    first_subject TEXT,
    created_at TEXT,
    stage TEXT DEFAULT 'new',          -- CRM pipeline stage (see STAGES)
    deal_value REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY, lead_id INTEGER, body TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, lead_id INTEGER, sender TEXT, type TEXT, detail TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS suppression (email TEXT PRIMARY KEY, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS seen_messages (msg_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS outbox (                -- drafts waiting for review (review mode)
    id INTEGER PRIMARY KEY, lead_id INTEGER, step INTEGER, sender TEXT,
    subject TEXT, body TEXT,
    status TEXT,                                   -- pending | approved | sent | rejected | cancelled
    created_at TEXT, decided_at TEXT, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY, name TEXT, domain TEXT, industry TEXT, country TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY, name TEXT, account_id INTEGER, lead_id INTEGER,
    stage TEXT, amount REAL DEFAULT 0, close_date TEXT, next_step TEXT,
    created_at TEXT, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, lead_id INTEGER, opp_id INTEGER, subject TEXT,
    due_date TEXT, status TEXT DEFAULT 'open', created_at TEXT, done_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, cmd TEXT, args TEXT, status TEXT, output TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (                -- a supervisor's emails with handed-over prospects
    id INTEGER PRIMARY KEY, lead_id INTEGER, msg_id TEXT UNIQUE, direction TEXT, at TEXT, frm TEXT, to_ TEXT, cc TEXT,
    subject TEXT, body TEXT, seen INTEGER DEFAULT 0, via TEXT
);
CREATE TABLE IF NOT EXISTS handover_out (                -- handovers waiting to reach the team hub
    id INTEGER PRIMARY KEY, lead_id INTEGER, supervisor TEXT, payload TEXT, at TEXT, synced INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS apollo_seen (apollo_id TEXT PRIMARY KEY, result TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS apollo_progress (search TEXT PRIMARY KEY, page INTEGER);
"""


# Lead path (Salesforce-style): open stages, then closed as converted or unqualified
LEAD_OPEN_STAGES = ["new", "contacted", "replied", "qualified"]
STAGES = LEAD_OPEN_STAGES + ["converted", "unqualified"]
EARLY_STAGES = ("new", "contacted")  # the bot only moves leads that a human hasn't advanced
HUMAN_STAGES = ("qualified", "converted", "unqualified")  # a person has taken over: stop emails

# Opportunity path with default win probability (%)
OPP_STAGES = {"Discovery": 20, "Proposal": 50, "Negotiation": 75, "Closed Won": 100, "Closed Lost": 0}
OPP_OPEN_STAGES = ["Discovery", "Proposal", "Negotiation"]


def _add_column(con, table, col, decl):
    cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


_migrated = set()


def db_connect(path):
    """Open the database. WAL mode lets the CRM and bot runs read and write at the same time;
    the schema check/upgrade runs only once per process."""
    full = DATA / path
    con = sqlite3.connect(full, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    if str(full) not in _migrated:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        # upgrade databases created by earlier versions
        _add_column(con, "leads", "stage", "TEXT DEFAULT 'new'")
        _add_column(con, "leads", "deal_value", "REAL DEFAULT 0")
        _add_column(con, "leads", "account_id", "INTEGER")
        _add_column(con, "notes", "kind", "TEXT DEFAULT 'note'")
        _add_column(con, "outbox", "variant", "TEXT")
        _add_column(con, "events", "variant", "TEXT")
        _add_column(con, "leads", "handed_to", "TEXT")          # supervisor this prospect was handed to (sender side)
        _add_column(con, "leads", "handed_at", "TEXT")
        _add_column(con, "leads", "handed_from", "TEXT")        # who handed it over (supervisor side)
        _add_column(con, "leads", "prospect_stage", "TEXT")     # supervisor's stage for a handed-over prospect
        con.execute("UPDATE leads SET stage='qualified' WHERE stage IN ('meeting','proposal','won')")
        con.execute("UPDATE leads SET stage='unqualified' WHERE stage='lost'")
        con.commit()
        _migrated.add(str(full))
    return con


def add_task(con, subject, due_date=None, lead_id=None, opp_id=None):
    due = due_date or datetime.now().date().isoformat()
    con.execute("INSERT INTO tasks(lead_id, opp_id, subject, due_date, status, created_at) "
                "VALUES (?,?,?,?,'open',?)", (lead_id, opp_id, subject, due, iso(now_utc())))


def set_stage(con, lead_id, stage, auto=True):
    """Move a lead in the pipeline. Automatic moves never override a human's later stage."""
    if auto:
        cur = con.execute(
            f"UPDATE leads SET stage=? WHERE id=? AND stage IN ({','.join('?' * len(EARLY_STAGES))})",
            (stage, lead_id, *EARLY_STAGES),
        )
    else:
        cur = con.execute("UPDATE leads SET stage=? WHERE id=?", (stage, lead_id))
    if cur.rowcount:
        log_event(con, lead_id, "stage", stage)


def log_event(con, lead_id, type_, detail="", sender=None, variant=None):
    con.execute(
        "INSERT INTO events(lead_id, sender, type, detail, at, variant) VALUES (?,?,?,?,?,?)",
        (lead_id, sender, type_, detail, iso(now_utc()), variant),
    )


def cancel_drafts(con, lead_id):
    """Withdraw any unsent drafts for a lead (they replied, unsubscribed, were paused...)."""
    con.execute("UPDATE outbox SET status='cancelled', decided_at=? "
                "WHERE lead_id=? AND status IN ('pending','approved')", (iso(now_utc()), lead_id))


def suppress(con, addr, reason):
    con.execute(
        "INSERT OR IGNORE INTO suppression(email, reason, at) VALUES (?,?,?)",
        (addr.lower(), reason, iso(now_utc())),
    )


# ---------------------------------------------------------------- senders

def load_senders(cfg):
    senders = []
    for s in cfg["senders"]:
        pw = env(s["password_env"])
        if not pw:
            log.warning("No password in env var %s for %s", s["password_env"], s["email"])
        senders.append({**s, "password": pw})
    return senders


def sent_today(con, sender_email, cfg):
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    row = con.execute(
        "SELECT COUNT(*) FROM events WHERE type='sent' AND sender=? AND at>=?",
        (sender_email, iso(start)),
    ).fetchone()
    return row[0]


def in_send_window(cfg):
    w = cfg["schedule"]
    t = datetime.now(ZoneInfo(w["timezone"]))
    return t.isoweekday() in w["days"] and w["start_hour"] <= t.hour < w["end_hour"]


# ---------------------------------------------------------------- LLM

def _reply_text(msg):
    """The answer text of a Messages API reply (newer models put thinking blocks before it)."""
    return "".join(getattr(b, "text", "") for b in (msg.content or []) if getattr(b, "type", "") == "text").strip()


def llm(cfg, model_key, prompt, max_tokens=200):
    """Call Claude; returns text or None if unavailable/failing."""
    if not env("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
        model = cfg["models"][model_key]
        try:
            # short, simple writing: no thinking needed (Haiku accepts this; others may refuse it)
            msg = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}],
                                         extra_body={"thinking": {"type": "disabled"}})
        except anthropic.BadRequestError:
            # model can't switch thinking off: leave room for it, keep only the answer
            msg = client.messages.create(model=model, max_tokens=max_tokens + 4000, messages=[{"role": "user", "content": prompt}])
        text = _reply_text(msg)
        return text or None
    except Exception as e:  # network, quota, etc. -> fall back gracefully
        log.warning("LLM call failed: %s", e)
        return None


def fetch_site_text(url, limit=4000):
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())[:limit]
    except Exception as e:
        log.info("Could not fetch %s: %s", url, e)
        return ""



_AVOID_CACHE = {}


def _avoid_patterns(cfg):
    phrases = tuple(cfg.get("avoid_phrases") or ())
    if phrases not in _AVOID_CACHE:
        pats = []
        for ph in phrases:
            ph = str(ph).strip().replace("\u2019", "'")
            if not ph:
                continue
            if ph.startswith("regex:"):
                pats.append((ph, re.compile(ph[6:], re.I)))
            elif re.search(r"[$%]", ph):          # symbols: match literally
                pats.append((ph, re.compile(re.escape(ph), re.I)))
            else:                                  # whole words; spaces and hyphens are interchangeable
                words = [re.escape(w) for w in re.split(r"[\s-]+", ph) if w]
                pats.append((ph, re.compile(r"(?<![\w])" + r"[\s-]+".join(words) + r"(?![\w])", re.I)))
        _AVOID_CACHE[phrases] = pats
    return _AVOID_CACHE[phrases]


def find_avoid(cfg, *texts):
    """Phrases from the avoid list (config: avoid_phrases) that appear in the text, e.g. spam-trigger words."""
    text = "\n".join(t or "" for t in texts).replace("\u2019", "'")
    return sorted({ph.replace("regex:", "") for ph, rx in _avoid_patterns(cfg) if rx.search(text)}, key=str.lower)


def template_avoid_hits(cfg):
    """Avoid-list phrases found in the email templates after the facts are filled in. {step: {variant: [phrases]}}"""
    out = {}
    facts = SafeDict(**{k: str(v) for k, v in (cfg.get("facts") or {}).items()})
    for i, st in enumerate(cfg.get("sequence") or [], 1):
        for v in st.get("variants") or [st]:
            if v.get("enabled", True) is False:
                continue
            filled = ((v.get("subject") or "") + "\n" + (v.get("body") or "")).format_map(facts)
            hits = find_avoid(cfg, filled, cfg.get("footer", ""))
            if hits:
                out.setdefault(i, {})[v.get("name") or f"step {i}"] = hits
    return out


def personalize(cfg, lead):
    """One personalised opening line. Privacy: the AI only sees business information — the company's
    public website text, company name, job title and industry — never the contact's name, email,
    LinkedIn or other personal details."""
    pc = cfg["personalization"]
    fallback = pc["fallback_line"].format_map(SafeDict(company=short_company(lead["company"]) or "your team"))
    if not pc.get("enabled"):
        return fallback
    site = fetch_site_text(lead["website"])
    extra = json.loads(lead["extra"] or "{}")
    business = {k: extra[k] for k in ("title", "industry") if extra.get(k)}
    prompt = f"""You write the opening line of a B2B cold email.

What we offer: {cfg['offer']}

The prospect's company:
- Company: {short_company(lead['company']) or 'unknown'}
- Recipient's job title and industry: {json.dumps(business) if business else 'unknown'}
- Text from the company's public website: {site or 'not available'}

Write ONE sentence (max 30 words) addressed to the recipient that shows we looked at their company
specifically and leads naturally toward our offer. Rules: reference a concrete detail from the info
above; don't use any person's name; no flattery clichés; no questions; never invent facts.
If there isn't enough specific information, output exactly: NONE
Output only the sentence."""
    line = llm(cfg, "personalize", prompt)
    if not line or line.upper().startswith("NONE") or not (10 < len(line) < 300):
        return fallback
    line = line.strip().strip('"')
    if find_avoid(cfg, line):  # the AI used a word you asked us to avoid
        return fallback
    return line


def classify_reply(cfg, msg, body):
    low = body.lower()
    if any(k in low for k in ("unsubscribe", "remove me", "stop emailing", "take me off")):
        return "unsubscribe"
    auto = (msg.get("Auto-Submitted", "") + msg.get("X-Autoreply", "")).lower()
    if auto and auto != "no":
        return "out_of_office"
    if not (cfg.get("ai_reply_sorting") or {}).get("enabled"):
        return keyword_label(low)  # default: sorted on this computer; reply text isn't sent anywhere
    prompt = f"""Classify this reply to a cold sales email. Answer with exactly one label from:
{', '.join(REPLY_LABELS)}
(interested = positive, wants to know more; meeting_request = asks for or proposes a call/meeting/time;
question = asks something specific such as price, process or examples; referral = they point us to someone else;
other = anything that needs a human look)

Reply:
\"\"\"{body[:2000]}\"\"\""""
    label = llm(cfg, "classify", prompt, max_tokens=12)
    if label is None:
        return keyword_label(low)
    label = label.lower().strip(" .\n")
    return label if label in REPLY_LABELS else "other"


def keyword_label(low):
    """Fallback reply sorting when no AI key is set (or the AI is unavailable)."""
    if re.search(r"\b(out of (the )?office|on (annual )?leave|on vacation|on holiday|away until|limited access to (my )?email)\b", low):
        return "out_of_office"
    if re.search(r"\b(not interested|no thanks|no thank you|not a (good )?fit|we('re| are) (all )?set|not (looking|right now)|please don'?t (contact|email))\b", low):
        return "not_interested"
    if re.search(r"\b(reach out to|contact|speak (to|with)|talk to) [a-z]+ (instead|who|on our)|\bright person\b|\bnot the right (person|contact)\b", low):
        return "referral"
    if re.search(r"\b(schedule|book a|set up a (call|meeting|time)|(a|quick|short|intro) (call|chat|meeting)|meeting|calendar|calendly|zoom|teams call|google meet|"
                 r"what time|which time|(are|is) you (free|available)|i'?m (free|available)|available (on|next|this|tomorrow)|"
                 r"(monday|tuesday|wednesday|thursday|friday)( at| morning| afternoon)?|tomorrow at|next week)\b", low):
        return "meeting_request"
    if re.search(r"\b(interested|let'?s (talk|chat|connect|meet)|(can|could|shall) we (talk|chat|meet|speak|connect)|happy to (talk|chat|connect)|sounds (good|interesting|great)|tell me more|send (me |over )?(your |the |some )?(more )?(info|information|details|deck|brochure|pricing)|keen to (learn|hear))\b", low):
        return "interested"
    if "?" in low and re.search(r"\b(how much|price|pricing|cost|rates?|fees?|budget|case stud(y|ies)|examples?|portfolio|references?|how (does|do|would|long)|what (is|are|would|does)|do you (have|offer|work|do)|can you|which (tech|stack|technolog))", low):
        return "question"
    return "other"


# ---------------------------------------------------------------- sending

LEGAL_SUFFIX = re.compile(r"[,\s]+(inc\.?|incorporated|llc|l\.l\.c\.|ltd\.?|limited|corp\.?|corporation|co\.|gmbh|plc|pvt\.? ltd\.?)$", re.I)


def short_company(name):
    """'Technology & Marketing Ventures, Inc.' -> 'Technology & Marketing Ventures' (reads naturally in a sentence)."""
    name = (name or "").strip()
    prev = None
    while prev != name:
        prev, name = name, LEGAL_SUFFIX.sub("", name).strip()
    return name


def render(template, lead, sender, cfg):
    ctx = SafeDict(json.loads(lead.get("extra") or "{}"))
    ctx.update(
        first_name=lead.get("first_name") or "there",
        company=short_company(lead.get("company")) or "your company",
        website=lead.get("website") or "",
        personal_line=lead.get("personal_line") or "",
        sender_name=sender["name"],
        sender_address=cfg.get("sender_address", ""),
    )
    for k, v in (cfg.get("facts") or {}).items():
        ctx.setdefault(k, v)
    text = template.format_map(ctx)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_email(sender, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None):
    msg = EmailMessage()
    msg["From"] = f'{sender["name"]} <{sender["email"]}>'
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    for k, v in (headers or {}).items():
        msg[k] = v
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg_id = make_msgid(domain=sender["email"].split("@")[1])
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["List-Unsubscribe"] = f"<mailto:{sender['email']}?subject=unsubscribe>"
    msg.set_content(body)

    if dry_run:
        print("=" * 70)
        print(f"FROM: {msg['From']}\nTO: {to}\nSUBJECT: {subject}\n")
        print(body)
        return msg_id

    port = sender.get("smtp_port", 587)
    user = sender.get("username", sender["email"])
    if sender.get("smtp_security") == "none":  # local test mail servers only — never sends credentials
        if sender["smtp_host"] not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Unencrypted SMTP is only allowed for a local test server")
        with smtplib.SMTP(sender["smtp_host"], port, timeout=30) as s:
            s.send_message(msg)
    elif port == 465:
        with smtplib.SMTP_SSL(sender["smtp_host"], port, timeout=30) as s:
            s.login(user, sender["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(sender["smtp_host"], port, timeout=30) as s:
            s.starttls()
            s.login(user, sender["password"])
            s.send_message(msg)
    return msg_id


def pick_sender(lead, senders, capacity):
    if lead["sender"]:  # follow-ups stay in the same inbox/thread
        match = [s for s in senders if s["email"] == lead["sender"]]
        return match[0] if match and capacity[match[0]["email"]] > 0 else None
    open_ = [s for s in senders if capacity[s["email"]] > 0]
    return max(open_, key=lambda s: capacity[s["email"]]) if open_ else None


def pick_variant(cfg, lead, idx, force_name=None):
    """Each step can have several template variants; leads are spread evenly across the enabled ones."""
    step = cfg["sequence"][idx]
    if "variants" not in step:
        return {"name": "", "subject": step.get("subject", ""), "body": step["body"]}
    variants = [v for v in step["variants"] if v.get("enabled", True) and (v.get("body") or "").strip()]
    variants = variants or [v for v in step["variants"] if (v.get("body") or "").strip()]
    if force_name:
        for v in step["variants"]:
            if v.get("name") == force_name:
                return v
    return variants[lead["id"] % len(variants)]


def compose(cfg, con, lead, sender, idx, fresh_opener=False, variant_name=None):
    """Build (subject, body-without-footer, variant name) for sequence step idx."""
    v = pick_variant(cfg, lead, idx, variant_name)
    if idx == 0 and (fresh_opener or not lead["personal_line"]):
        lead["personal_line"] = personalize(cfg, lead)
        con.execute("UPDATE leads SET personal_line=? WHERE id=?", (lead["personal_line"], lead["id"]))
        con.commit()
    body = render(v["body"], lead, sender, cfg)
    if idx > 0 and lead["first_subject"]:
        subject = "Re: " + lead["first_subject"]  # keeps follow-ups in the same thread
    else:
        subject = render(v.get("subject") or "Quick question", lead, sender, cfg)
    return subject, body, v.get("name", "")


def regenerate_draft(cfg, con, draft_id):
    """Rewrite a pending draft with a new AI opener (used by the CRM review page)."""
    d = con.execute("SELECT * FROM outbox WHERE id=?", (draft_id,)).fetchone()
    lead = dict(con.execute("SELECT * FROM leads WHERE id=?", (d["lead_id"],)).fetchone())
    sender = next(s for s in cfg["senders"] if s["email"] == d["sender"])
    subject, body, _ = compose(cfg, con, lead, sender, d["step"], fresh_opener=True, variant_name=d["variant"])
    con.execute("UPDATE outbox SET subject=?, body=? WHERE id=?", (subject, body, draft_id))
    con.commit()


def cmd_run(cfg, con, args):
    lg_ver = str((cfg.get("legal") or {}).get("version", "1.0"))
    if str((cfg.get("legal_acceptance") or {}).get("version")) != lg_ver and not args.dry_run:
        print("PROBLEM: Open BSO and accept the Terms of Use first.")
        sys.exit(1)
    team_cap = 0
    team_mod = None
    if not args.dry_run:
        import team as team_mod
        ok, why, team_cap = team_mod.gate(con)
        if not ok:
            print("PROBLEM:", why)
            sys.exit(1)
    if not args.force and not in_send_window(cfg):
        print("Outside the sending window; nothing sent (use --force to override).")
        return
    if not args.dry_run and not args.no_check:
        cmd_check(cfg, con, args)

    steps = cfg["sequence"]
    senders = [s for s in load_senders(cfg) if s["password"] or args.dry_run]
    if not senders:
        print("No usable sender inboxes (check your .env passwords).")
        return
    capacity = {s["email"]: s["daily_limit"] - sent_today(con, s["email"], cfg) for s in senders}

    review = ((cfg.get("review") or {}).get("enabled", False) or (team_mod and team_mod.holds_approval())) and not args.dry_run
    cap_left = None
    if team_cap:
        cap_left = max(0, team_cap - team_mod.sent_today_total(con))
        if cap_left == 0:
            print(f"Your manager's daily cap ({team_cap}) is reached; nothing more is sent today.")
            return
    tm = cfg.get("test_mode") or {}
    redirect = tm.get("redirect_to") if tm.get("enabled") else None
    if tm.get("enabled") and not redirect:
        print("test_mode is on but redirect_to is empty; refusing to send.")
        return

    # never queue more drafts than the inboxes can send today
    queued = con.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('pending','approved')").fetchone()[0]
    draft_budget = max(0, sum(max(c, 0) for c in capacity.values()) - queued)
    if cap_left is not None:
        draft_budget = min(draft_budget, max(0, cap_left - queued))

    due = con.execute(
        "SELECT * FROM leads WHERE status='active' AND next_send_at<=? "
        "ORDER BY step DESC, id LIMIT ?",  # finish running sequences before starting new ones
        (iso(now_utc()), args.limit),
    ).fetchall()

    sent = drafted = waiting = failed = 0
    problems = []
    lo, hi = cfg.get("delay_between_sends_seconds", [60, 180])
    for row in due:
        lead = dict(row)
        idx = lead["step"]
        if idx >= len(steps):
            con.execute("UPDATE leads SET status='done' WHERE id=?", (lead["id"],))
            continue

        draft = None
        if review:
            draft = con.execute(
                "SELECT * FROM outbox WHERE lead_id=? AND step=? AND status IN ('pending','approved')",
                (lead["id"], idx)).fetchone()
            if not draft:  # write it and wait for a human
                if draft_budget <= 0:
                    continue
                sender = pick_sender(lead, senders, {s["email"]: 1 for s in senders})
                subject, body, variant = compose(cfg, con, lead, sender, idx)
                hits = find_avoid(cfg, subject, body)
                if hits:
                    problems.append(f"Step {idx + 1} email for {lead['email']} uses avoided phrases ({', '.join(hits)}); not drafted. "
                                    "Edit the template in Settings → Your offer & emails.")
                    continue
                con.execute(
                    "INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at, variant) "
                    "VALUES (?,?,?,?,?,'pending',?,?)",
                    (lead["id"], idx, sender["email"], subject, body, iso(now_utc()), variant))
                log_event(con, lead["id"], "drafted", f"step {idx + 1}", sender["email"])
                con.commit()
                drafted += 1
                draft_budget -= 1
                continue
            if draft["status"] == "pending":
                waiting += 1
                continue
            sender = next((s for s in senders if s["email"] == draft["sender"]), None)
            if not sender or capacity[sender["email"]] <= 0:
                continue
            subject, body, variant = draft["subject"], draft["body"], draft["variant"]
        else:
            sender = pick_sender(lead, senders, capacity)
            if not sender:
                continue
            subject, body, variant = compose(cfg, con, lead, sender, idx)
            hits = find_avoid(cfg, subject, body)
            if hits:
                problems.append(f"Step {idx + 1} email for {lead['email']} uses avoided phrases ({', '.join(hits)}); not sent. "
                                "Edit the template in Settings → Your offer & emails.")
                continue

        reply_to = lead["first_msg_id"] if idx > 0 else None
        full_body = body + "\n\n" + render(cfg["footer"], lead, sender, cfg)
        to, shown_subject = lead["email"], subject
        if redirect:  # test mode: real lead data, but it lands in your own inbox
            to, shown_subject = redirect, f"[TEST for {lead['email']}] {subject}"

        if cap_left is not None and sent >= cap_left:
            break
        if sent > 0 and not args.dry_run:
            time.sleep(random.uniform(lo, hi))  # human-ish pacing
        try:
            msg_id = send_email(sender, to, shown_subject, full_body, reply_to, args.dry_run)
        except smtplib.SMTPAuthenticationError as e:
            log.error("Login failed for %s: %s — skipping this inbox", sender["email"], e)
            capacity[sender["email"]] = 0
            problems.append(f"{sender['email']}: login rejected. Check the app password in Settings → Email inboxes.")
            continue
        except (ConnectionError, TimeoutError, socket.gaierror) as e:
            log.error("Can't reach the mail server for %s: %s — skipping this inbox", sender["email"], e)
            capacity[sender["email"]] = 0
            problems.append(f"{sender['email']}: can't reach the mail server ({e}). Check the server details or your internet connection.")
            continue
        except Exception as e:
            log.error("Send to %s failed: %s", lead["email"], e)
            log_event(con, lead["id"], "error", str(e), sender["email"])
            con.commit()
            failed += 1
            continue

        sent += 1
        capacity[sender["email"]] -= 1
        if args.dry_run:
            continue
        if draft:
            con.execute("UPDATE outbox SET status='sent', sent_at=? WHERE id=?", (iso(now_utc()), draft["id"]))

        nxt = idx + 1
        if nxt < len(steps):
            next_at = iso(now_utc() + timedelta(days=steps[nxt]["delay_days"]))
            status = "active"
        else:
            next_at, status = None, "done"
        con.execute(
            "UPDATE leads SET step=?, sender=?, next_send_at=?, status=?, "
            "first_msg_id=COALESCE(first_msg_id, ?), first_subject=COALESCE(first_subject, ?) "
            "WHERE id=?",
            (nxt, sender["email"], next_at, status, msg_id, subject, lead["id"]),
        )
        log_event(con, lead["id"], "sent", f"step {idx + 1}" + (" (test)" if redirect else ""), sender["email"], variant)
        if lead["stage"] == "new":
            set_stage(con, lead["id"], "contacted")
        con.commit()
        log.info("Sent step %d to %s via %s", idx + 1, to, sender["email"])

    con.commit()
    if team_mod:
        try:
            team_mod.sync(con)
        except Exception as e:  # reporting must never break sending
            log.warning("Team sync failed: %s", e)
    msg = f"{'Previewed' if args.dry_run else 'Sent'} {sent} email(s)."
    if review:
        msg += f" Drafted {drafted} new for review; {waiting} waiting for your approval in the CRM."
    if failed:
        msg += f" {failed} failed to send (details above)."
    print(msg)
    for p in dict.fromkeys(problems):
        print("PROBLEM:", p)
    if problems or failed:
        sys.exit(1)  # marks the run as Failed in the CRM so it's noticed


# ---------------------------------------------------------------- inbox

def get_text_body(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain = html = ""
    for p in parts:
        if p.get_content_disposition() == "attachment":
            continue
        ctype = p.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            text = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", text)
    text = plain or html
    # keep only the new part of the reply, drop the quoted thread
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:\s*$", line.strip()):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def notify(text, cfg=None):
    print(text)
    to = ((cfg or {}).get("notifications") or {}).get("email", "").strip()
    if to and cfg:
        try:  # an email alert, sent from the first working inbox
            sender = next((x for x in load_senders(cfg) if x["password"]), None)
            if sender:
                send_email(sender, to, "BSO: " + text.split(":", 1)[0][:80], text)
        except Exception as e:
            log.warning("Email notification failed: %s", e)
    hook = env("SLACK_WEBHOOK_URL")
    if hook:
        try:
            requests.post(hook, json={"text": text}, timeout=10)
        except Exception as e:
            log.warning("Slack notify failed: %s", e)


def handle_message(cfg, con, sender, msg):
    frm = parseaddr(msg.get("From", ""))[1].lower()
    body = get_text_body(msg)

    if "mailer-daemon" in frm or "postmaster" in frm:  # bounce
        for addr in set(a.lower() for a in FIND_EMAILS_RE.findall(body)):
            lead = con.execute("SELECT id FROM leads WHERE email=?", (addr,)).fetchone()
            if lead:
                con.execute("UPDATE leads SET status='bounced' WHERE id=?", (lead["id"],))
                set_stage(con, lead["id"], "unqualified")
                cancel_drafts(con, lead["id"])
                suppress(con, addr, "bounced")
                log_event(con, lead["id"], "bounce", "", sender["email"])
                log.info("Bounce: %s", addr)
        return

    lead = con.execute("SELECT * FROM leads WHERE email=?", (frm,)).fetchone()
    if lead and lead["handed_to"] and lead["status"] not in ("unsubscribed", "bounced"):
        forward_to_supervisor(cfg, con, dict(lead), sender, msg, body)   # nothing slips through after a handover
        return
    if not lead or lead["status"] in ("replied", "unsubscribed", "not_interested", "bounced"):
        return

    label = classify_reply(cfg, msg, body)
    log_event(con, lead["id"], "reply", label, sender["email"])
    if label == "out_of_office":
        return  # keep the sequence going
    cancel_drafts(con, lead["id"])
    if label in ("unsubscribe", "not_interested"):
        status = "unsubscribed" if label == "unsubscribe" else "not_interested"
        con.execute("UPDATE leads SET status=? WHERE id=?", (status, lead["id"]))
        set_stage(con, lead["id"], "unqualified")
        suppress(con, frm, label)
        return
    con.execute("UPDATE leads SET status='replied' WHERE id=?", (lead["id"],))
    set_stage(con, lead["id"], "replied")
    con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'email')",
                (lead["id"], f"[{label} reply]\n{body[:2000]}", iso(now_utc())))
    who = lead["first_name"] or frm
    if label in HANDOVER_LABELS and handover(cfg, con, dict(lead), sender, msg, body, label):
        return
    add_task(con, f"Respond to {who} ({label.replace('_', ' ')} reply)", lead_id=lead["id"])
    snippet = body[:300].replace("\n", " ")
    notify(f"📬 {label.upper()} reply from {lead['first_name'] or ''} <{frm}> "
           f"({lead['company'] or ''}) in {sender['email']}:\n{snippet}", cfg)


# ---------------------------------------------------------------- handover to a lead gen supervisor

DEFAULT_INTRO = """Hi {first_name},

Thanks for getting back to me. I'm looping in my colleague {supervisor_name}, who works with our clients on exactly this. {supervisor_first_name} will take it from here and can find a time that suits you.

Best,
{sender_name}"""


def supervisors(cfg):
    """[{id, name, email, count}]: from the master console when on a team, else from Settings → Handover."""
    try:
        import team
        if team.joined():
            return team.supervisors()
    except Exception as e:
        log.warning("Team supervisors unavailable: %s", e)
    out = []
    for sv in (cfg.get("handover") or {}).get("supervisors") or []:
        if EMAIL_RE.match(str(sv.get("email", "")).strip()):
            out.append({"id": sv["email"].strip().lower(), "name": sv.get("name") or sv["email"], "email": sv["email"].strip().lower(), "count": 0})
    return out


def pick_supervisor(cfg, con):
    """Take turns: the supervisor with the fewest handovers gets the next one."""
    sups = supervisors(cfg)
    if not sups:
        return None
    since = ""
    try:
        import team
        since = team.state().get("sup_as_of", "") if team.joined() else ""
    except Exception:
        pass
    def load(sv):
        local = con.execute("SELECT COUNT(*) FROM leads WHERE handed_to=? AND handed_at>?", (sv["email"], since)).fetchone()[0]
        return (sv.get("count", 0) + local, sups.index(sv))
    return min(sups, key=load)


def _thread(con, lead_id):
    return [{"at": r["sent_at"] or r["created_at"], "subject": r["subject"], "body": r["body"]}
            for r in con.execute("SELECT * FROM outbox WHERE lead_id=? AND status='sent' ORDER BY id", (lead_id,))]


def handover(cfg, con, lead, sender, msg, body, label):
    """Hand an interested prospect to a lead gen supervisor. Returns False when there's nobody to hand to."""
    hc = cfg.get("handover") or {}
    if not hc.get("enabled", True):
        return False
    sup = None
    try:
        import team
        sup = team.assign_supervisor(lead["email"])     # the always-on relay keeps the turns fair for everyone
    except Exception as e:
        log.warning("Supervisor assignment unavailable: %s", e)
    sup = sup or pick_supervisor(cfg, con)
    if not sup:
        return False
    tm = cfg.get("test_mode") or {}
    redirect = tm.get("redirect_to") if tm.get("enabled") else None
    name = " ".join(p for p in (lead.get("first_name"), json.loads(lead.get("extra") or "{}").get("last_name")) if p) or lead["email"]
    company = lead.get("company") or ""
    sup_first = (sup["name"] or "").split()[0] if sup.get("name") else "they"

    # 1. warm intro from the same inbox, in the same thread, supervisor in CC
    intro_note = ""
    intro_text = ""
    if hc.get("intro_enabled", True):
        tpl = (hc.get("intro_template") or DEFAULT_INTRO).replace("{supervisor_name}", sup["name"]).replace("{supervisor_first_name}", sup_first)
        intro_text = render(tpl, lead, sender, cfg)
        hits = find_avoid(cfg, intro_text)
        subject = msg.get("Subject", "") or ("Re: " + (lead.get("first_subject") or ""))
        subject = subject if subject.lower().startswith("re:") else "Re: " + subject
        try:
            if redirect:
                send_email(sender, redirect, f"[TEST for {lead['email']}, CC {sup['email']}] {subject}", intro_text, msg.get("Message-ID"))
            else:
                send_email(sender, lead["email"], subject, intro_text, msg.get("Message-ID"), cc=sup["email"])
            log_event(con, lead["id"], "sent", "handover intro" + (" (test)" if redirect else ""), sender["email"])
            if hits:
                log.warning("Handover intro uses avoided words: %s", ", ".join(hits))
        except Exception as e:
            log.error("Handover intro to %s failed: %s", lead["email"], e)
            intro_note = f"\n⚠ The introduction email could not be sent ({e}). Please contact the prospect directly.\n"
            intro_text = ""

    # 2. brief for the supervisor
    thread = _thread(con, lead["id"])
    x = json.loads(lead.get("extra") or "{}")
    member = ""
    try:
        import team
        member = (team.policy() or {}).get("name", "") if team.joined() else ""
    except Exception:
        pass
    lines = [f"{sup['name']}, a prospect has been handed over to you.", intro_note,
             f"Prospect:   {name}", f"Title:      {x.get('title', '')}", f"Company:    {company}", f"Email:      {lead['email']}",
             f"Website:    {lead.get('website') or ''}", f"LinkedIn:   {x.get('linkedin', '')}",
             f"Reply type: {label.replace('_', ' ')}", f"From:       {member or sender['name']} ({sender['email']})", "",
             "--- Their reply ---", body.strip()[:4000], ""]
    if intro_text:
        lines += ["--- Introduction we sent (you were in CC) ---", intro_text, ""]
    for i, t in enumerate(thread, 1):
        lines += [f"--- Our email {i} ({str(t['at'])[:10]}): {t['subject']} ---", t["body"], ""]
    lines.append("Reply to the prospect from your own mailbox. In BSO, open My prospects to track the stage, notes and tasks.")
    brief_to = redirect or sup["email"]
    try:
        send_email(sender, brief_to, f"{'[TEST] ' if redirect else ''}Handover: {name}, {company} ({label.replace('_', ' ')})", "\n".join(lines),
                   headers={"X-BSO-Kind": "brief", "X-BSO-Prospect": lead["email"]})
    except Exception as e:
        log.error("Handover brief to %s failed: %s", sup["email"], e)

    # 3. record it
    at = iso(now_utc())
    con.execute("UPDATE leads SET handed_to=?, handed_at=? WHERE id=?", (sup["email"], at, lead["id"]))
    set_stage(con, lead["id"], "qualified", auto=False)
    con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                (lead["id"], f"Handed over to {sup['name']} <{sup['email']}> ({label.replace('_', ' ')} reply).", at))
    log_event(con, lead["id"], "handover", sup["id"], sender["email"])
    payload = {"lead_local_id": lead["id"], "supervisor": sup["id"], "label": label, "at": at,
               "lead": {"email": lead["email"], "first_name": lead.get("first_name"), "last_name": x.get("last_name"), "title": x.get("title"),
                        "company": company, "website": lead.get("website"), "linkedin": x.get("linkedin"), "city": x.get("city"), "country": x.get("country")},
               "reply": body.strip()[:4000], "intro": intro_text, "thread": thread, "from_inbox": sender["email"], "from_name": sender["name"]}
    con.execute("INSERT INTO handover_out(lead_id, supervisor, payload, at) VALUES (?,?,?,?)", (lead["id"], sup["id"], json.dumps(payload), at))
    con.commit()
    notify(f"🤝 Handed over {name} ({company}) to {sup['name']}: {label.replace('_', ' ')} reply in {sender['email']}", cfg)
    return True


def forward_to_supervisor(cfg, con, lead, sender, msg, body):
    """A handed-over prospect wrote to the original inbox again: pass it to their supervisor straight away."""
    tm = cfg.get("test_mode") or {}
    redirect = tm.get("redirect_to") if tm.get("enabled") else None
    name = lead.get("first_name") or lead["email"]
    text = (f"{name} <{lead['email']}> wrote to {sender['email']} after being handed over to you.\n"
            f"Reply to them directly (Reply goes to {lead['email']}).\n\n--- Their message ---\n{body.strip()[:8000]}")
    try:
        send_email(sender, redirect or lead["handed_to"], f"{'[TEST] ' if redirect else ''}Fwd: {msg.get('Subject', '')} ({name} replied to {sender['email']})",
                   text, headers={"X-BSO-Kind": "forward", "X-BSO-Prospect": lead["email"], "Reply-To": lead["email"]})
        log_event(con, lead["id"], "reply", "after handover: forwarded to supervisor", sender["email"])
    except Exception as e:
        log.error("Forward to supervisor %s failed: %s", lead["handed_to"], e)
        add_task(con, f"Pass on a new email from {name} to {lead['handed_to']} (forwarding failed)", lead_id=lead["id"])
    if classify_reply(cfg, msg, body) == "unsubscribe":
        suppress(con, lead["email"], "unsubscribe")
    con.commit()


def process_inbox(cfg, con, sender):
    since = (datetime.now() - timedelta(days=cfg.get("reply_lookback_days", 14))).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL(sender["imap_host"], sender.get("imap_port", 993))
    try:
        M.login(sender.get("username", sender["email"]), sender["password"])
        M.select("INBOX", readonly=True)  # never marks your mail as read
        _, data = M.uid("search", None, f'(SINCE "{since}")')
        for uid in data[0].split():
            # cheap header fetch first; full body only for new messages
            _, hdr = M.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            head = email.message_from_bytes(hdr[0][1]) if hdr and hdr[0] else {}
            key = (head.get("Message-ID") or "").strip() or f"{sender['email']}:{uid.decode()}"
            if con.execute("SELECT 1 FROM seen_messages WHERE msg_id=?", (key,)).fetchone():
                continue
            _, full = M.uid("fetch", uid, "(BODY.PEEK[])")
            msg = email.message_from_bytes(full[0][1])
            try:
                handle_message(cfg, con, sender, msg)
            finally:
                con.execute("INSERT OR IGNORE INTO seen_messages VALUES (?)", (key,))
                con.commit()
    finally:
        try:
            M.logout()
        except Exception:
            pass


def cmd_check(cfg, con, args):
    for s in load_senders(cfg):
        if not s["password"]:
            continue
        try:
            process_inbox(cfg, con, s)
        except Exception as e:
            log.error("Inbox check failed for %s: %s", s["email"], e)
    con.commit()


# ---------------------------------------------------------------- other commands

HEADER_ALIASES = {
    "email": "email", "email address": "email", "work email": "email", "e-mail": "email",
    "first name": "first_name", "firstname": "first_name", "first_name": "first_name",
    "last name": "last_name", "lastname": "last_name", "last_name": "last_name",
    "company": "company", "company name": "company", "organization": "company", "account": "company",
    "title": "title", "job title": "title", "position": "title",
    "website": "website", "company website": "website", "domain": "website", "url": "website",
    "employee linkedin": "linkedin", "linkedin": "linkedin", "person linkedin": "linkedin",
    "linkedin url": "linkedin", "company li": "company_linkedin", "company linkedin": "company_linkedin",
    "industry": "industry", "city": "city", "country": "country", "source": "source",
    # Instantly exports (SuperSearch / lead lists / campaign leads)
    "companyname": "company", "jobtitle": "title", "company domain": "website", "companydomain": "website",
    "linkedin profile": "linkedin", "linkedinurl": "linkedin", "location": "city", "company_name": "company",
    "job_title": "title", "website url": "website",
}
ROLE_INBOXES = {"info", "hello", "help", "support", "sales", "contact", "admin", "office",
                "team", "hr", "careers", "jobs", "marketing", "billing", "noreply", "no-reply"}
FIT_RULES = [  # (grade, title keywords) — first match wins
    ("A", ["cto", "chief technology", "vp engineering", "vp of engineering", "head of engineering",
           "director of engineering", "head of product", "cpo", "chief product", "vp technology",
           "head of it", "cio", "chief information", "technology architect", "engineering"]),
    ("B", ["ceo", "chief executive", "founder", "co-founder", "president", "coo", "owner",
           "managing director", "executive director", "partner", "technology", "product"]),
]


def fit_grade(title):
    """A = technical decision maker, B = executive/founder, C = outside target roles.
    Whole-word matching, so 'Director' doesn't count as 'CTO'."""
    t = (title or "").lower()
    for grade, words in FIT_RULES:
        if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words):
            return grade
    return "C"


def clean_website(url, email=""):
    """'http://www.Foo.com/about' -> 'foo.com'. Falls back to the email's domain if broken."""
    host = re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0].removeprefix("www.")
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        host = email.split("@")[-1] if "@" in email else ""
    return host


def normalize_row(row):
    """Map any reasonable spreadsheet headers to our fields and tidy the values."""
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).strip().lower()
        key = HEADER_ALIASES.get(key, key.replace(" ", "_"))
        out[key] = "" if v is None else str(v).strip()
    for f in ("first_name", "last_name"):
        if out.get(f, "").isupper() and len(out[f]) > 2:
            out[f] = out[f].title()  # ALLEN -> Allen
    out["email"] = out.get("email", "").lower()
    out["website"] = clean_website(out.get("website", ""), out["email"])
    return out


def add_lead(con, row, status="active", why=None):
    """Insert one lead from a dict. Returns the new id, or None if invalid/duplicate/suppressed.
    If `why` is a list, the skip reason is appended to it."""
    row = normalize_row(row)
    addr = row.get("email", "")
    reason = None
    if not EMAIL_RE.match(addr):
        reason = "invalid email"
    elif addr.split("@")[0] in ROLE_INBOXES:
        reason = "shared inbox (e.g. info@, hello@)"
    elif con.execute("SELECT 1 FROM suppression WHERE email=?", (addr,)).fetchone():
        reason = "on do-not-contact list"
    elif con.execute("SELECT 1 FROM leads WHERE email=?", (addr,)).fetchone():
        reason = "already in CRM"
    if reason:
        if why is not None:
            why.append(reason)
        return None
    row["fit"] = fit_grade(row.get("title"))
    extra = {k: v for k, v in row.items() if k not in CORE_FIELDS and v}
    cur = con.execute(
        "INSERT OR IGNORE INTO leads(email, first_name, company, website, extra, status, "
        "next_send_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (addr, row.get("first_name"), row.get("company"), row.get("website"),
         json.dumps(extra), status, iso(now_utc()), iso(now_utc())),
    )
    return cur.lastrowid if cur.rowcount else None


def read_rows(path_or_file, filename=""):
    """Yield dict rows from a CSV or Excel file (path or file-like object)."""
    name = (filename or str(path_or_file)).lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path_or_file, read_only=True, data_only=True)
        rows = wb.active.iter_rows(values_only=True)
        header = next(rows, None) or []
        for r in rows:
            if any(c not in (None, "") for c in r):
                yield dict(zip(header, r))
    else:
        if isinstance(path_or_file, (str, Path)):
            with open(path_or_file, newline="", encoding="utf-8-sig") as f:
                yield from csv.DictReader(f)
        else:
            yield from csv.DictReader(path_or_file)


def import_rows(con, rows, status="active", source=""):
    """Returns (added, {reason: count})."""
    added, reasons = 0, []
    for row in rows:
        if source and not any(str(k).strip().lower() == "source" for k in row if k):
            row = {**row, "source": source}
        if add_lead(con, row, status, reasons):
            added += 1
    con.commit()
    summary = {}
    for r in reasons:
        summary[r] = summary.get(r, 0) + 1
    return added, summary


def import_csv(con, f, status="active"):  # kept for older callers
    added, skipped = import_rows(con, csv.DictReader(f), status)
    return added, sum(skipped.values())


def cmd_import(cfg, con, args):
    added, skipped = import_rows(con, read_rows(args.file), source=Path(args.file).name)
    print(f"Imported {added} lead(s).")
    for reason, n in skipped.items():
        print(f"  skipped {n}: {reason}")


def cmd_suppress(cfg, con, args):
    suppress(con, args.email, "manual")
    con.execute("UPDATE leads SET status='unsubscribed' WHERE email=?", (args.email.lower(),))
    row = con.execute("SELECT id FROM leads WHERE email=?", (args.email.lower(),)).fetchone()
    if row:
        set_stage(con, row["id"], "unqualified")
        cancel_drafts(con, row["id"])
    con.commit()
    print(f"{args.email} will never be emailed.")


def cmd_selfcheck(cfg, con, args):
    """Verify every library the app loads on demand is present (used by the Windows build test)."""
    problems = []
    for mod in ("anthropic", "bs4", "openpyxl", "yaml", "requests", "dotenv", "flask", "apollo", "legal", "secretstore", "pystray", "PIL"):
        try:
            __import__(mod)
        except Exception as e:
            problems.append(f"{mod}: {e}")
    try:
        ZoneInfo(cfg["schedule"]["timezone"])
        in_send_window(cfg)
    except Exception as e:
        problems.append(f"time zones: {e}")
    try:  # secrets round-trip through the encrypted store
        import secretstore
        st = secretstore.SecretStore(DATA)
        before = st.load()
        st.set("SELFCHECK_PROBE", "probe-1234")
        assert secretstore.SecretStore(DATA).load().get("SELFCHECK_PROBE") == "probe-1234"
        assert b"probe-1234" not in (DATA / "secrets.dat").read_bytes() or not secretstore.ENCRYPTED
        st.save(before)
        print("secrets: encrypted with Windows DPAPI" if secretstore.ENCRYPTED else "secrets: development mode (not encrypted)")
    except Exception as e:
        problems.append(f"secret store: {e}")
    try:
        import legal
        for d in legal.DOCS:
            assert "{{" not in legal.source(d, cfg)
        print("legal documents: OK, version", legal.app_version())
    except Exception as e:
        problems.append(f"legal documents: {e}")
    try:
        bad = template_avoid_hits(cfg)
        assert not bad, "; ".join(f"step {i} '{v}': {', '.join(h)}" for i, vs in bad.items() for v, h in vs.items())
        if cfg.get("avoid_phrases"):
            assert find_avoid(cfg, "Act now, it is FREE!"), "avoid-list matcher found nothing in a test sentence"
        print("email templates: no avoided phrases (%d phrases checked)" % len(cfg.get("avoid_phrases") or []))
    except Exception as e:
        problems.append(f"email templates use avoided phrases: {e}")
    for p in problems:
        print("PROBLEM:", p)
    print("SELFCHECK OK" if not problems else "SELFCHECK FAILED")
    if problems:
        sys.exit(1)


def cmd_apollo_preview(cfg, con, args):
    import apollo
    try:
        apollo.preview(cfg)
    except apollo.ApolloError as e:
        sys.exit(f"Apollo: {e}")


def cmd_apollo_pull(cfg, con, args):
    import apollo
    try:
        apollo.pull(cfg, con, args.max or cfg["apollo"].get("max_per_pull", 25), add_lead)
    except apollo.ApolloError as e:
        sys.exit(f"Apollo: {e}")


def cmd_stats(cfg, con, args):
    print("Leads by status:")
    for r in con.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status ORDER BY n DESC"):
        print(f"  {r['status']:<15} {r['n']}")
    print("\nEmails sent by step:")
    for r in con.execute("SELECT detail, COUNT(*) n FROM events WHERE type='sent' GROUP BY detail ORDER BY detail"):
        print(f"  {r['detail']:<15} {r['n']}")
    print("\nReplies by type:")
    for r in con.execute("SELECT detail, COUNT(*) n FROM events WHERE type='reply' GROUP BY detail ORDER BY n DESC"):
        print(f"  {r['detail']:<15} {r['n']}")
    contacted = con.execute("SELECT COUNT(DISTINCT lead_id) FROM events WHERE type='sent'").fetchone()[0]
    replied = con.execute(
        "SELECT COUNT(DISTINCT lead_id) FROM events WHERE type='reply' AND detail!='out_of_office'"
    ).fetchone()[0]
    bounced = con.execute("SELECT COUNT(*) FROM leads WHERE status='bounced'").fetchone()[0]
    if contacted:
        print(f"\nContacted {contacted} | reply rate {replied / contacted:.1%} | bounce rate {bounced / contacted:.1%}")


# ---------------------------------------------------------------- main

def acquire_lock():
    """Stop two overlapping cron runs from double-sending."""
    try:
        import fcntl
    except ImportError:  # Windows: no lock
        return None
    fh = open(DATA / ".bot.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another run is in progress; exiting.")
        sys.exit(0)
    return fh


def main(argv=None):
    job_log = os.getenv("OUTREACH_JOB_LOG")
    if job_log:
        sys.stdout = sys.stderr = open(job_log, "a", buffering=1, encoding="utf-8")
    p = argparse.ArgumentParser(description="Cold email outreach bot")
    p.add_argument("--config", default=str(DATA / "config.yaml"))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="import leads from a CSV or Excel file")
    s.add_argument("file")
    s = sub.add_parser("run", help="check replies, then send due emails")
    s.add_argument("--dry-run", action="store_true", help="print emails instead of sending")
    s.add_argument("--force", action="store_true", help="ignore the sending window")
    s.add_argument("--no-check", action="store_true", help="skip the reply check")
    s.add_argument("--limit", type=int, default=50, help="max leads to process this run")
    sub.add_parser("check", help="check inboxes for replies and bounces")
    sub.add_parser("stats", help="show campaign stats")
    s = sub.add_parser("suppress", help="never email an address")
    s.add_argument("email")
    sub.add_parser("selfcheck", help="verify the installation")
    sub.add_parser("apollo-preview", help="free: see what your Apollo searches match")
    s = sub.add_parser("apollo-pull", help="add new verified leads from Apollo (uses credits)")
    s.add_argument("--max", type=int, help="max new leads this run (default: apollo.max_per_pull)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    con = db_connect(cfg.get("database", "outreach.db"))
    lock = acquire_lock() if args.cmd in ("run", "check") else None

    {"import": cmd_import, "run": cmd_run, "check": cmd_check,
     "stats": cmd_stats, "suppress": cmd_suppress,
     "apollo-preview": cmd_apollo_preview, "selfcheck": cmd_selfcheck, "apollo-pull": cmd_apollo_pull}[args.cmd](cfg, con, args)


if __name__ == "__main__":
    main()
