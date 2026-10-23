"""The BSO Bot's day-to-day jobs around sending.

- Lead intake mailbox (e.g. leads@blacswanken.com): only the owner's address is accepted, and only when the mail
  server confirms it (SPF/DKIM/DMARC). Lead files (.xlsx, .xls, .csv, BSO's template) are checked row by row and a
  receipt goes back with a file of the rejected rows. STOP ALL / START ALL in the subject stops or restarts sending.
- Low lead stock alert (below 10 days, repeated every 2 days, urgent below 3) with the template attached.
- Daily digest to the owner.
- Readiness check when an inbox is added (login, SPF/DKIM/DMARC, blocklists, a test send), daily reputation rating,
  weekly spam-placement tests to the owner's own test addresses.
- Answers to simple information requests, from the owner's prompt or template for that campaign.
"""
import csv
import email
import imaplib
import io
import json
import random
import re
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from zoneinfo import ZoneInfo

import autopilot
import bot
from bot import iso, log, now_utc

TEMPLATE_COLUMNS = [
    ("campaign_code", True, "NEO-SMB-UK", "Copy it from the Campaigns sheet."),
    ("email", True, "jane.doe@acme.co.uk", "A real, known address. BSO never guesses addresses."),
    ("first_name", True, "Jane", "Used in the greeting."),
    ("last_name", False, "Doe", ""),
    ("job_title", True, "Operations Director", "Used to choose the angle."),
    ("company", True, "Acme Ltd", "Used for the per-company limit."),
    ("website", False, "acme.co.uk", "Helps the writer if it differs from the email domain."),
    ("country", True, "United Kingdom", "Sets the lead's time zone for sending hours."),
    ("city", False, "Manchester", "Sharper time zone in large countries."),
    ("industry", False, "Logistics", ""),
    ("company_size", False, "50-200", ""),
    ("linkedin_url", False, "", "Only if you already have it. BSO doesn't look people up."),
    ("source", False, "Apollo export Oct", "Tracks which source converts."),
    ("notes", False, "Met at expo", "Context the writer may use."),
]
REQUIRED = [c for c, req, _, _ in TEMPLATE_COLUMNS if req]
FILE_TYPES = (".xlsx", ".xlsm", ".xls", ".csv")
MAX_BYTES = 15 * 1024 * 1024


# ---------------------------------------------------------------- the intake mailbox

def box(cfg):
    b = dict(cfg.get("intake_box") or {})
    if not b.get("email"):
        return None
    b["password"] = bot.env(b.get("password_env") or "INTAKE_PASSWORD")
    b.setdefault("name", "BSO Bot")
    b.setdefault("owner", autopilot.conf(cfg)["owner"])
    return b


def owner(cfg):
    b = box(cfg)
    return ((b or {}).get("owner") or autopilot.conf(cfg)["owner"]).lower()


def _smtp_send(b, msg):
    port = int(b.get("smtp_port") or 465)
    if b.get("smtp_security") == "none":
        if b["smtp_host"] not in ("127.0.0.1", "localhost"):
            raise ValueError("Unencrypted SMTP is only allowed for a local test server")
        with smtplib.SMTP(b["smtp_host"], port, timeout=30) as s:
            s.send_message(msg)
    elif port == 465:
        with smtplib.SMTP_SSL(b["smtp_host"], port, timeout=30, context=ssl.create_default_context()) as s:
            s.login(b.get("username") or b["email"], b["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(b["smtp_host"], port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(b.get("username") or b["email"], b["password"])
            s.send_message(msg)


SENT = []          # tests read what would have gone out


def mail_owner(cfg, subject, text, attachments=(), in_reply_to=None, to=None):
    """Email the owner from the intake mailbox (falls back to the usual notification). Returns True when sent."""
    b = box(cfg)
    to = to or owner(cfg)
    if not b or not (b.get("password") or b.get("smtp_security") == "none"):
        bot.notify(subject + ": " + text[:3000], cfg)
        return False
    msg = EmailMessage()
    msg["From"] = f'{b["name"]} <{b["email"]}>'
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=b["email"].split("@")[1])
    msg["X-BSO-Kind"] = "bot"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(text)
    for name, data, mime in attachments:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    SENT.append(msg)
    try:
        _smtp_send(b, msg)
        return True
    except Exception as e:
        log.error("Couldn't email the owner: %s", e)
        bot.notify(subject + ": " + text[:3000], cfg)
        return False


def _fetch_new(b, con, days=3):
    """New messages in the intake mailbox (each handled once)."""
    port = int(b.get("imap_port") or 993)
    if b.get("imap_security") == "none":
        M = imaplib.IMAP4(b["imap_host"], port)
    else:
        M = imaplib.IMAP4_SSL(b["imap_host"], port, ssl_context=ssl.create_default_context())
    out = []
    try:
        M.login(b.get("username") or b["email"], b["password"])
        M.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, "SINCE", since)
        for num in (data[0] or b"").split()[-100:]:
            typ, d = M.fetch(num, "(RFC822)")
            raw = next((x[1] for x in d if isinstance(x, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            mid = "intake:" + (msg.get("Message-ID") or f"{b['email']}:{num.decode()}").strip()
            if con.execute("SELECT 1 FROM seen_messages WHERE msg_id=?", (mid,)).fetchone():
                continue
            con.execute("INSERT OR IGNORE INTO seen_messages(msg_id) VALUES (?)", (mid,))
            con.commit()
            out.append(msg)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


def check_intake(cfg, con):
    """Read the intake mailbox and act on what the owner sent. Returns lines for the run log."""
    b = box(cfg)
    if not b or not b.get("imap_host") or not (b.get("password") or b.get("imap_security") == "none"):
        return []
    lines = []
    try:
        msgs = _fetch_new(b, con)
    except Exception as e:
        return [f"PROBLEM: can't read the lead intake mailbox {b['email']} ({e})."]
    for msg in msgs:
        try:
            lines.append(handle_intake(cfg, con, msg))
        except Exception as e:
            lines.append(f"PROBLEM: an email in the intake mailbox couldn't be handled ({e}).")
    return lines


def handle_intake(cfg, con, msg):
    frm = parseaddr(msg.get("From", ""))[1].lower()
    subject = (msg.get("Subject") or "").strip()
    allowed = {owner(cfg)} | {a.lower() for a in (box(cfg) or {}).get("also_accept") or []}
    def log_row(files, added, rejected, detail):
        con.execute("INSERT INTO intake_log(at, frm, subject, files, added, rejected, detail) VALUES (?,?,?,?,?,?,?)",
                    (iso(now_utc()), frm, subject[:200], files, added, rejected, detail[:2000]))
        con.commit()
    if msg.get("X-BSO-Kind"):
        return "intake: ignored an email BSO sent itself"
    if frm not in allowed:
        log_row("", 0, 0, "ignored: not from the owner")
        autopilot.note(con, "intake", f"ignored an email from {frm}: only {owner(cfg)} may send lead files")
        return f"intake: ignored an email from {frm} (only the owner's address is accepted)"
    if (box(cfg) or {}).get("verified_only", True) and not bot._sender_verified(msg, frm):
        log_row("", 0, 0, "refused: the mail server couldn't confirm the sender")
        autopilot.note(con, "intake", f"refused an email claiming to be from {frm}: SPF/DKIM/DMARC didn't pass")
        return f"intake: refused an email claiming to be from {frm} (sender not verified)"
    cmd = subject.upper()
    if re.search(r"\bSTOP ALL\b", cmd):
        autopilot.set_kill(con, True, "STOP ALL email")
        mail_owner(cfg, "Re: " + subject, "All sending is stopped, for every client. Replies and handovers carry on.\n"
                   "Send an email with START ALL in the subject to start again.", in_reply_to=msg.get("Message-ID"))
        log_row("", 0, 0, "STOP ALL")
        return "intake: STOP ALL received, sending stopped"
    if re.search(r"\bSTART ALL\b", cmd):
        autopilot.set_kill(con, False)
        k, why = autopilot.killed(con)
        mail_owner(cfg, "Re: " + subject, "Sending has started again." if not k else
                   "The STOP ALL is lifted, but sending is still stopped from the Master Console. Press Start there.",
                   in_reply_to=msg.get("Message-ID"))
        log_row("", 0, 0, "START ALL")
        return "intake: START ALL received"
    files = [(p.get_filename() or "", p.get_payload(decode=True) or b"") for p in msg.walk()
             if (p.get_filename() or "").lower().endswith(FILE_TYPES)]
    if not files:
        mail_owner(cfg, "Re: " + subject, "BSO found no lead file in your email. Attach the filled-in template (.xlsx, .xls or .csv) "
                   "and send it again. The template is attached.", [template_attachment(con)], in_reply_to=msg.get("Message-ID"))
        log_row("", 0, 0, "no file attached")
        return "intake: email from the owner without a lead file (answered)"
    total_added, rejects, summary = 0, [], []
    for name, data in files:
        if len(data) > MAX_BYTES:
            summary.append(f"{name}: too large (over 15 MB), not read.")
            continue
        try:
            added, rej, per = import_file(cfg, con, name, data, frm)
        except Exception as e:
            summary.append(f"{name}: couldn't be read ({e}). Use BSO's template with its header row.")
            continue
        total_added += added
        rejects += [(name, *r) for r in rej]
        summary.append(f"{name}: {added} added, {len(rej)} rejected." + ("".join(f"\n   {k}: {v} added" for k, v in per.items()) if per else ""))
    text = "BSO checked your lead file.\n\n" + "\n".join(summary)
    att = []
    if rejects:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["file", "row", "email", "reason"])
        w.writerows(rejects)
        att.append(("rejected-rows.csv", buf.getvalue().encode("utf-8-sig"), "text/csv"))
        reasons = {}
        for r in rejects:
            reasons[r[3]] = reasons.get(r[3], 0) + 1
        text += "\n\nRejected rows, by reason:\n" + "\n".join(f"   {n} × {k}" for k, n in sorted(reasons.items(), key=lambda x: -x[1]))
        text += "\n\nThe attached rejected-rows.csv lists each one."
    text += "\n\nLead stock now:\n" + stock_lines(cfg, con)
    mail_owner(cfg, "Re: " + subject if subject else "Your lead file", text, att, in_reply_to=msg.get("Message-ID"))
    log_row(", ".join(n for n, _ in files), total_added, len(rejects), "\n".join(summary))
    autopilot.note(con, "intake", f"lead file from {frm}: {total_added} added, {len(rejects)} rejected")
    return f"intake: {total_added} lead(s) added, {len(rejects)} rejected"


_mx_cache = {}


def has_mail_server(domain):
    """True / False from DNS, None when DNS can't be reached (the row is accepted)."""
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        import health
        ok = bool(health._txt(domain, "MX")) or bool(health._txt(domain, "A"))
    except Exception:
        ok = None
    _mx_cache[domain] = ok
    return ok


def import_file(cfg, con, name, data, frm=""):
    """Check every row and add the good ones. Returns (added, [(row, email, reason)], {campaign: added})."""
    if name.lower().endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        rows = list(bot.read_rows(io.StringIO(text), name))
    else:
        rows = list(bot.read_rows(io.BytesIO(data), name))
    camps = {}
    for c in con.execute("SELECT * FROM campaigns").fetchall():
        if c["code"]:
            camps[c["code"].strip().lower()] = c
        camps.setdefault(c["name"].strip().lower(), c)
    seen, rejected, per, added = set(), [], {}, 0
    for i, raw in enumerate(rows, start=2):
        row = bot.normalize_row(raw)
        if not any(v for v in row.values()):
            continue
        e = row.get("email", "")
        missing = [f for f in ("campaign_code", "email", "first_name", "title", "company", "country") if not row.get(f)]
        reason = None
        c = camps.get((row.get("campaign_code") or "").strip().lower())
        if missing:
            reason = "missing " + ", ".join("job_title" if m == "title" else m for m in missing)
        elif not c:
            reason = f"unknown campaign code {row.get('campaign_code')}"
        elif not bot.EMAIL_RE.match(e):
            reason = "bad address format"
        elif e in seen:
            reason = "duplicate in the file"
        elif e.split("@")[0] in bot.ROLE_INBOXES and not (c["allow_roles"] if "allow_roles" in c.keys() else 0):
            reason = "role address (info@, sales@ …)"
        elif con.execute("SELECT 1 FROM suppression WHERE email=?", (e,)).fetchone():
            reason = "unsubscribed or on do-not-contact"
        else:
            ex = con.execute("SELECT status, step FROM leads WHERE email=?", (e,)).fetchone()
            if ex and ex["status"] in ("active", "replied", "nurture", "paused"):
                reason = "already in an active sequence" if ex["status"] == "active" else f"already in BSO ({ex['status']})"
            elif ex:
                reason = f"already in BSO ({ex['status']})"
            elif has_mail_server(e.split("@")[1]) is False:
                reason = "the domain has no mail server"
        seen.add(e)
        if reason:
            rejected.append((i, e, reason))
            continue
        row["campaign_id"] = c["id"]
        row["_batch"] = f"{date.today().isoformat()}-{name[:40]}"          # one imported file = one list for the circuit breaker
        row.setdefault("source", f"emailed by {frm}: {name}" if frm else name)
        why = []
        lid = bot.add_lead(con, row, "active", why)
        if lid:
            added += 1
            per[c["name"]] = per.get(c["name"], 0) + 1
        else:
            rejected.append((i, e, why[0] if why else "not added"))
    con.commit()
    return added, rejected, per


# ---------------------------------------------------------------- the template

def template_xlsx(con, cfg=None):
    rows, stocks = [], {}
    try:
        cfg = cfg or bot.load_config(str(bot.DATA / "config.yaml"))
        stocks = {c["id"]: c.get("stock") or {} for c in autopilot.report(cfg, con)["campaigns"]}
    except Exception:
        pass
    for c in con.execute("SELECT c.*, cl.name client FROM campaigns c LEFT JOIN clients cl ON cl.id=c.client_id ORDER BY c.id").fetchall():
        st = stocks.get(c["id"]) or {}
        rows.append({"code": c["code"], "client": c["client"] or "", "name": c["name"], "status": c["status"] or "active",
                     "days": st.get("days"), "ready": st.get("ready"), "new": st.get("new_per_day")})
    return build_template(rows)


def build_template(campaigns):
    """BSO's lead template: Leads (to fill in), Instructions, Campaigns (current codes)."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Leads"
    head = Font(bold=True, color="FFFFFF")
    for j, (col, req, ex, _) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=j, value=col)
        cell.font = head
        cell.fill = PatternFill("solid", fgColor="1F4E79" if req else "7F7F7F")
        ws.column_dimensions[cell.column_letter].width = max(14, len(col) + 4, len(ex) + 2)
    ws.freeze_panes = "A2"
    ins = wb.create_sheet("Instructions")
    for col, w in zip("ABCD", (18, 10, 26, 70)):
        ins.column_dimensions[col].width = w
    lines = [("How to send leads to BSO", "", "", ""), ("", "", "", ""),
             ("1.", "", "", "Fill in the Leads sheet: one person per row. Blue columns are required, grey are optional."),
             ("2.", "", "", "campaign_code: copy it from the Campaigns sheet. One file can carry several campaigns."),
             ("3.", "", "", "Email the file to the lead intake mailbox from your own address (.xlsx, .xls or .csv)."),
             ("4.", "", "", "BSO replies within minutes: rows added, rows rejected and why (a file lists each one)."),
             ("", "", "", ""), ("Column", "Required", "Example", "Notes")]
    lines += [(c, "Yes" if r else "No", ex, n) for c, r, ex, n in TEMPLATE_COLUMNS]
    lines += [("", "", "", ""), ("Rows are rejected for:", "", "", "a bad address, a domain with no mail server, a role address (info@, sales@) unless allowed "
              "for the campaign, a duplicate, an unsubscribed or do-not-contact address, or a person already in an active sequence."),
              ("", "", "", "People contacted before are accepted but flagged, and BSO suggests a reconnect email instead of a cold one.")]
    for i, line in enumerate(lines, start=1):
        for j, v in enumerate(line, start=1):
            cell = ins.cell(row=i, column=j, value=v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if i in (1, 8):
                cell.font = Font(bold=True)
    cs = wb.create_sheet("Campaigns")
    hdr = ["campaign_code", "client", "campaign", "status", "days of stock", "leads waiting", "new leads per day"]
    for j, h in enumerate(hdr, start=1):
        cs.cell(row=1, column=j, value=h).font = Font(bold=True)
        cs.column_dimensions[cs.cell(row=1, column=j).column_letter].width = 30 if j == 3 else 18
    for r, c in enumerate(campaigns, start=2):
        for j, k in enumerate(("code", "client", "name", "status", "days", "ready", "new"), start=1):
            cs.cell(row=r, column=j, value=c.get(k))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def template_attachment(con, cfg=None):
    return ("BSO-lead-template.xlsx", template_xlsx(con, cfg), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------- stock alert and digest

def _owner_now(cfg):
    return datetime.now(ZoneInfo(autopilot.conf(cfg)["owner_tz"]))


def _done_today(con, key):
    r = con.execute("SELECT v FROM app_mode WHERE k=?", (key,)).fetchone()
    return r and r[0] == date.today().isoformat()


def _mark_today(con, key):
    con.execute("INSERT INTO app_mode(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, date.today().isoformat()))
    con.commit()


def stock_rows(cfg, con):
    rp = autopilot.report(cfg, con)
    return [c for c in rp["campaigns"] if (c["status"] or "active") == "active" and c.get("stock")]


def stock_lines(cfg, con):
    out = []
    for c in stock_rows(cfg, con):
        st = c["stock"]
        days = "no first emails possible yet (no capacity)" if st["days"] is None else f"{st['days']} working days"
        out.append(f"   {c['name']} ({c['code']}): {st['ready']} leads waiting, about {st['new_per_day']} new a day → {days}")
    return "\n".join(out) or "   (no active campaigns)"


def stock_alert(cfg, con, force=False):
    """Email the owner when a campaign has less than 10 days of leads. Returns the alert text or None."""
    ap = autopilot.conf(cfg)
    low, urgent = [], False
    for c in stock_rows(cfg, con):
        st = c["stock"]
        if st["days"] is None and st["ready"] > 0:
            continue                            # nothing can send yet: a setup problem, not a stock problem
        days = st["days"] if st["days"] is not None else 0
        if days >= float(ap["stock_alert_days"]):
            continue
        key = f"stock:{c['id']}"
        last = con.execute("SELECT at, text FROM alerts WHERE kind='stock' AND key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        is_urgent = days < float(ap["stock_urgent_days"])
        recent = last and last["at"] > iso(now_utc() - timedelta(days=float(ap["alert_repeat_days"])) + timedelta(hours=1))
        if recent and not force and not (is_urgent and "URGENT" not in (last["text"] or "")):
            continue
        urgent = urgent or is_urgent
        low.append((c, st, days, is_urgent))
    if not low:
        return None
    names = ", ".join(c["name"] for c, *_ in low)
    subject = ("URGENT: " if urgent else "") + f"Leads running low: {names}"
    body = ["These campaigns have less than %s working days of leads left:" % ap["stock_alert_days"], ""]
    for c, st, days, u in low:
        body.append(f"{'⚠ ' if u else ''}{c['name']} ({c['code']}, {c['client'] or 'no client'}): {days} days left, "
                    f"{st['ready']} leads waiting, about {st['new_per_day']} new leads a day.")
    body += ["", "To refill: fill in the attached template (the Campaigns sheet already lists the campaign codes) and reply to this email "
             "with it attached, or email it to the lead intake mailbox.", "",
             "When a campaign runs out, follow-ups and replies carry on; only new first emails stop."]
    text = "\n".join(body)
    mail_owner(cfg, subject, text, [template_attachment(con, cfg)])
    for c, st, days, u in low:
        con.execute("INSERT INTO alerts(kind, key, at, text) VALUES ('stock', ?, ?, ?)", (f"stock:{c['id']}", iso(now_utc()), subject))
    con.commit()
    autopilot.note(con, "alert", subject)
    return text


def digest(cfg, con):
    since = iso(now_utc() - timedelta(days=1))
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    temps = {r[0]: r[1] for r in con.execute("SELECT temperature, COUNT(*) FROM received WHERE at>=? GROUP BY temperature", (since,))}
    rp = autopilot.report(cfg, con)
    lines = [f"BSO Bot, the last 24 hours ({date.today().isoformat()})", "",
             f"Sent: {q('SELECT COUNT(*) FROM events WHERE type=? AND at>=? AND detail NOT LIKE ?', 'sent', since, '%(test)%')}",
             f"Replies: {q('SELECT COUNT(*) FROM events WHERE type=? AND at>=?', 'reply', since)}"
             + (" (" + ", ".join(f"{v} {k}" for k, v in temps.items() if k) + ")" if temps else ""),
             f"Handed to supervisors: {q('SELECT COUNT(*) FROM events WHERE type=? AND at>=?', 'handover', since)}",
             f"Answered information requests: {q('SELECT COUNT(*) FROM events WHERE type=? AND at>=?', 'info_reply', since)}",
             f"Waiting in Review: {q('SELECT COUNT(*) FROM outbox WHERE status=?', 'pending')}", ""]
    hot = con.execute("SELECT l.email, l.company, t.due_at FROM tasks t JOIN leads l ON l.id=t.lead_id WHERE t.status='open' AND t.kind='respond' "
                      "AND t.priority='high' ORDER BY t.due_at LIMIT 10").fetchall()
    if hot:
        lines += ["Hot leads waiting for a person:"] + [f"   {h['email']} ({h['company'] or ''}), due {str(h['due_at'] or '')[:16]}" for h in hot] + [""]
    probs = [w for k, w in rp["why_not"] if k in ("stopped", "paused", "not ready", "setup")]
    errs = q("SELECT COUNT(*) FROM events WHERE type='error' AND at>=?", since)
    if errs:
        probs.append(f"{errs} email(s) failed to send")
    lines += ["Problems:"] + ([f"   {p}" for p in probs] if probs else ["   none"]) + [""]
    lines += ["Lead stock:", stock_lines(cfg, con), ""]
    try:
        lines.append(f"AI spend this month: ${bot.ai_spend():.2f}")
    except Exception:
        pass
    opens = [s for s in rp["spot_checks"]]
    if opens:
        lines.append(f"Spot checks waiting for a look: {len(opens)} (Master Console → BSO Bot)")
    text = "\n".join(lines)
    mail_owner(cfg, f"BSO daily digest: {date.today().strftime('%a %d %b')}", text)
    return text


# ---------------------------------------------------------------- inbox readiness, reputation and spam-placement tests

def blocklisted(domain):
    """Name of the blocklist that lists the domain, '' when clean, None when it can't be checked."""
    try:
        import health
        for zone in ("dbl.spamhaus.org", "multi.surbl.org"):
            ans = health._txt(f"{domain}.{zone}", "A")
            hits = [a for a in ans if a.startswith("127.") and not a.startswith("127.255.255.")]
            if hits:
                return zone
        return ""
    except Exception:
        return None


def readiness(cfg, con, s, send=True):
    """Check an inbox before it's used: login, SPF/DKIM/DMARC, blocklists and one test send. Returns (ready, detail)."""
    problems = []
    try:
        if s.get("imap_host"):
            M = imaplib.IMAP4_SSL(s["imap_host"], int(s.get("imap_port") or 993), ssl_context=ssl.create_default_context())
            M.login(s.get("username", s["email"]), s["password"])
            M.logout()
    except Exception as e:
        problems.append(f"inbox login failed ({str(e)[:80]})")
    dom = autopilot.domain_of(s["email"])
    try:
        import health
        h = health.check(dom)
        if "error" not in h:
            for k in ("spf", "dkim", "dmarc"):
                if not h[k][0]:
                    problems.append(f"{k.upper()} {h[k][1]}")
    except Exception:
        pass
    bl = blocklisted(dom)
    if bl:
        problems.append(f"domain listed on {bl}")
    if not problems and send:
        try:
            bot.send_email(s, s["email"], "BSO readiness check", "This test email checks that BSO can send from this inbox. It can be deleted.",
                           headers={"X-BSO-Kind": "readiness"})
        except Exception as e:
            problems.append(f"test send failed ({str(e)[:80]})")
    autopilot.rep(con, s["email"])
    ok = not problems
    detail = "ready" if ok else "not ready: " + "; ".join(problems)
    con.execute("UPDATE inbox_rep SET ready=?, ready_detail=?, ready_at=?, started_at=COALESCE(started_at, CASE WHEN ?=1 THEN ? END) WHERE email=?",
                (1 if ok else 0, detail, iso(now_utc()), 1 if ok else 0, date.today().isoformat(), s["email"].lower()))
    con.commit()
    autopilot.note(con, "readiness", f"{s['email']}: {detail}")
    return ok, detail


def seeds(cfg):
    out = []
    for x in (cfg.get("seeds") or []):
        if not x.get("email"):
            continue
        d = x["email"].split("@")[1].lower()
        host = x.get("imap_host") or ("imap.gmail.com" if d in ("gmail.com", "googlemail.com") else
                                      "outlook.office365.com" if d in ("hotmail.com", "outlook.com", "live.com", "msn.com") else "")
        out.append(dict(x, imap_host=host, password=bot.env(x.get("password_env") or ""), microsoft=d in ("hotmail.com", "outlook.com", "live.com", "msn.com")))
    return out


def send_seed_tests(cfg, con, senders):
    """Once a week per inbox: one test email to each of the owner's test addresses."""
    ss = seeds(cfg)
    if not ss:
        return 0
    n = 0
    for s in senders:
        r = autopilot.rep(con, s["email"])
        if r["seed_at"] and r["seed_at"] > iso(now_utc() - timedelta(days=7)):
            continue
        token = f"bso-{random.randint(100000, 999999)}"
        for x in ss:
            try:
                bot.send_email(s, x["email"], f"Quick note {token}", f"Hello,\n\nJust checking in on this thread.\n\n{s.get('name', '')}",
                               headers={"X-BSO-Kind": "seed", "X-BSO-Seed": token})
                n += 1
            except Exception as e:
                log.warning("Seed test from %s failed: %s", s["email"], e)
        con.execute("UPDATE inbox_rep SET seed_at=?, seed_result=? WHERE email=?",
                    (iso(now_utc()), json.dumps({"token": token, "pending": True}), s["email"].lower()))
    con.commit()
    return n


def collect_seed_results(cfg, con):
    """Look in the test addresses for this week's test emails: inbox or spam folder."""
    pend = [r for r in con.execute("SELECT * FROM inbox_rep WHERE seed_result LIKE '%\"pending\": true%' AND seed_at<=?",
                                   (iso(now_utc() - timedelta(minutes=30)),)).fetchall()]
    if not pend:
        return
    found = {r["email"]: {"inbox": 0, "spam": 0, "missing": 0, "token": json.loads(r["seed_result"])["token"]} for r in pend}
    for x in seeds(cfg):
        if x["microsoft"] and not x.get("password"):
            continue
        if not x.get("password") or not x["imap_host"]:
            continue
        try:
            M = imaplib.IMAP4_SSL(x["imap_host"], 993, ssl_context=ssl.create_default_context())
            M.login(x["email"], x["password"])
            folders = {"inbox": ["INBOX"], "spam": ["[Gmail]/Spam", "Junk", "Junk Email", "Spam"]}
            for e, f in found.items():
                hit = None
                for where, names in folders.items():
                    for name in names:
                        try:
                            if M.select(f'"{name}"', readonly=True)[0] != "OK":
                                continue
                            typ, data = M.search(None, "SUBJECT", f["token"])
                            if data and data[0].split():
                                hit = where
                                break
                        except Exception:
                            continue
                    if hit:
                        break
                f[hit or "missing"] += 1
            M.logout()
        except Exception as e:
            log.warning("Spam-placement check in %s failed: %s", x["email"], e)
    for e, f in found.items():
        con.execute("UPDATE inbox_rep SET seed_result=? WHERE email=?", (json.dumps({k: v for k, v in f.items() if k != "token"}), e))
    con.commit()


def rate_all(cfg, con, senders):
    dom_bl, dom_auth = {}, {}
    for s in senders:
        d = autopilot.domain_of(s["email"])
        if d not in dom_bl:
            dom_bl[d] = blocklisted(d)
            try:
                import health
                h = health.check(d)
                dom_auth[d] = None if "error" in h else all(h[k][0] for k in ("spf", "dkim", "dmarc"))
            except Exception:
                dom_auth[d] = None
        sig = autopilot.signals_from_history(con, s["email"])
        if dom_bl[d]:
            sig["blocklisted"] = dom_bl[d]
        if dom_auth[d] is False:
            sig["auth_ok"] = False
        r = autopilot.rep(con, s["email"])
        try:
            sr = json.loads(r["seed_result"] or "{}")
            if not sr.get("pending"):
                sig["spam_tests"] = sr
        except ValueError:
            pass
        autopilot.rate_inbox(cfg, con, s, sig)
    con.commit()


def housekeeping(cfg, con):
    """Everything that happens around sending, called by each run of the bot. Returns lines for the run log."""
    lines = []
    lines += check_intake(cfg, con)
    for e in autopilot.auto_resume(cfg, con):
        lines.append(f"Inbox {e} resumed on its own after its pause (half volume for 3 days).")
    senders = [s for s in bot.load_senders(cfg) if s.get("password") and "example" not in s["email"]]
    todo = []
    for s in senders:
        r = autopilot.rep(con, s["email"])
        if not r["ready"] and (not r["ready_at"] or r["ready_at"] < iso(now_utc() - timedelta(hours=1))):
            todo.append(s)
    todo = todo[:40]                                  # a batch per round: hundreds of new inboxes are checked over a few rounds
    if todo:
        con.commit()
        from concurrent.futures import ThreadPoolExecutor
        def check_one(s):
            c = bot.db_connect(cfg.get("database", "outreach.db"))
            try:
                return s["email"], readiness(cfg, c, s)
            finally:
                c.close()
        with ThreadPoolExecutor(max_workers=8) as ex:
            for e, (ok, detail) in ex.map(check_one, todo):
                lines.append(f"Readiness check {e}: {detail}")
    ap = autopilot.conf(cfg)
    now = _owner_now(cfg)
    if now.hour >= int(ap["alert_hour"]) and not _done_today(con, "daily_rating"):
        _mark_today(con, "daily_rating")
        try:
            collect_seed_results(cfg, con)
            rate_all(cfg, con, [s for s in senders if autopilot.rep(con, s["email"])["ready"]])
            n = send_seed_tests(cfg, con, [s for s in senders if autopilot.rep(con, s["email"])["ready"]])
            if n:
                lines.append(f"Sent {n} spam-placement test email(s) to the owner's test addresses.")
        except Exception as e:
            lines.append(f"PROBLEM: daily reputation check failed ({e}).")
        try:
            if stock_alert(cfg, con):
                lines.append("Lead stock is low: the owner was emailed the template.")
        except Exception as e:
            lines.append(f"PROBLEM: stock alert failed ({e}).")
    if now.hour >= int(ap["digest_hour"]) and not _done_today(con, "digest"):
        _mark_today(con, "digest")
        try:
            digest(cfg, con)
            lines.append("Daily digest emailed to the owner.")
        except Exception as e:
            lines.append(f"PROBLEM: daily digest failed ({e}).")
    else:
        try:
            collect_seed_results(cfg, con)
        except Exception:
            pass
    try:
        import safety
        if safety.due_backup(con):
            r = safety.backup(con, folder=(cfg.get("backup") or {}).get("folder") or None)
            lines.append(f"Backup {r['file']}: {r['detail']}" +
                         (f"; off-laptop copy: {r['offsite_detail']}" if r.get("offsite_detail") else "; no off-laptop folder set (Settings → Safety)"))
            if not r["ok"] or r.get("offsite_ok") is False:
                bot.notify(f"⚠ BSO Bot backup problem: {r['detail']}" + (f" / {r.get('offsite_detail')}" if r.get("offsite_detail") else ""), cfg)
    except Exception as e:
        lines.append(f"PROBLEM: daily backup failed ({e}).")
    return lines


# ---------------------------------------------------------------- answers to information requests

INFO_RE = re.compile(r"\b(more (info|information|details)|send (me |over |us )?(some |more )?(info|information|details|a deck|the deck|a brochure|"
                     r"case stud|examples?)|what (exactly )?do you (do|offer)|tell me more|learn more|case stud(y|ies)|brochure|portfolio|"
                     r"who are your clients|which (technologies|tech stack)|where are you based|how does (it|this) work)", re.I)
HUMAN_RE = re.compile(r"\b(price|pricing|cost|rates?|quote|budget|how much|fee|discount|contract|call|meet|meeting|chat|"
                      r"schedule|calendar|available|availability|demo|interested|let'?s talk|speak|zoom|teams)\b", re.I)


def is_info_request(body):
    text = bot._without_quotes(body or "")[:3000]
    return bool(INFO_RE.search(text)) and not HUMAN_RE.search(text)


def info_answer(cfg, con, lead, sender, c, body):
    mode = (c["info_mode"] if c and "info_mode" in c.keys() else "off") or "off"
    instr = (c["info_text"] or "").strip() if c else ""
    if mode == "off" or not instr:
        return None
    ccfg = bot.campaign_cfg(cfg, con, c["id"])
    if mode == "template":
        return bot.render(instr, lead, sender, ccfg)
    import guard
    import writer
    if guard.injection(body):
        return None
    b = writer.brief(ccfg, con, c["id"])
    approved = f"Offer: {b.get('offer')}\nProof points: {'; '.join(b.get('proof_points') or [])}"
    system = (f"You are {sender.get('name')} answering, by email, a prospect who asked for information.\n"
              f"Owner's instructions for these answers:\n{instr}\n\n"
              f"Approved facts you may use (and no others):\n{approved}\n\n"
              "You may only choose one of these actions and must return only JSON:\n"
              '{"action": "answer", "reply": "<the email body>"}  when the approved facts and instructions fully answer what they asked;\n'
              '{"action": "hand_over"}  for anything else: pricing or terms not in the facts, a complaint, a request to change '
              "anything, an attachment, a meeting time, anything unusual or anything you're unsure about;\n"
              '{"action": "stop"}  if they ask not to be contacted.\n'
              "The reply: plain text, under 130 words, friendly and direct, answers what they asked, invites one next step, "
              f"signs off as {sender.get('name')}. No figures, links, email addresses or phone numbers that are not in the approved facts.\n\n"
              + guard.SYSTEM_RULE)
    prompt = (f"The prospect ({lead.get('first_name') or ''} at {lead.get('company') or ''}) wrote:\n"
              + guard.untrusted(guard.new_text(body), limit=2000))
    pick = guard.choose(bot.llm(ccfg, "reply", prompt, max_tokens=500, system=system))
    if pick["action"] != "answer" or not str(pick.get("reply") or "").strip():
        log.info("Information answer for %s: the AI chose %s; a person answers", lead["email"], pick["action"])
        return None
    text = str(pick["reply"]).strip()
    if writer.unsupported_facts(text, b):
        log.warning("Information answer for %s used figures outside the approved facts: handed to a person instead", lead["email"])
        return None
    problems = guard.check_outgoing(text, approved + "\n" + instr, [sender.get("email", "")], max_words=180)
    if problems:
        log.warning("Information answer for %s not sent (%s): handed to a person instead", lead["email"], "; ".join(problems))
        return None
    return text


def info_reply(cfg, con, lead, sender, msg, body):
    """Answer a simple information request in the same thread. Returns a description, or None to hand over as usual."""
    import convo
    c = bot.campaign_row(con, lead.get("campaign_id") or 1)
    import guard
    if not c or (c["info_mode"] or "off") == "off" or not is_info_request(body) or guard.injection(body):
        return None
    text = info_answer(cfg, con, lead, sender, c, body)
    if not text:
        return None
    tm = cfg.get("test_mode") or {}
    redirect = tm.get("redirect_to") if tm.get("enabled") else None
    subject = msg.get("Subject") or ("Re: " + (lead.get("first_subject") or ""))
    subject = subject if subject.lower().startswith("re:") else "Re: " + subject
    to, shown = (redirect, f"[TEST for {lead['email']}] {subject}") if redirect else (parseaddr(msg.get("From", ""))[1] or lead["email"], subject)
    full = text + "\n\n" + bot.render(bot.campaign_cfg(cfg, con, c["id"]).get("footer") or "", lead, sender, cfg)
    mid = bot.send_email(sender, to, shown, full, msg.get("Message-ID"), headers={"X-BSO-Kind": "info"},
                         key="info-" + (msg.get("Message-ID") or f"{lead['id']}-{bot.iso(now_utc())}").strip()[:200])
    convo.record_sent(con, mid, lead, "info", sender["email"], to, shown, full, test=bool(redirect))
    bot.log_event(con, lead["id"], "info_reply", "answered an information request", sender["email"])
    con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,'note')",
                (lead["id"], f"BSO answered their information request ({c['info_mode']}):\n{text}", bot.iso(now_utc())))
    autopilot.note(con, "info_reply", f"answered an information request from {lead['email']}", lead["id"], c["id"])
    con.commit()
    return f"answered an information request from {lead['email']} ({c['info_mode']})"
