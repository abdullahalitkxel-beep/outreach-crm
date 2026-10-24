"""Master Console pages for the BSO Bot: its control room mirrored (kill switch, campaigns, clients, sending domains,
inbox health, why-not panel, spot checks, lead intake), the device list with approvals, update control, and the
Client Scope room (a client's scope document turned into a campaign setup for the owner to approve).

Changes made here travel to the bot as sealed commands through the same channel as handovers and approvals.
"""
import io
import json
import math
import re
import zipfile
from datetime import datetime, timedelta

from flask import abort, g, redirect, request, send_file, url_for

H = None          # the hub module, set by install()


def install(hub):
    global H
    H = hub
    for rule, ep, fn, methods in ROUTES:
        hub.app.add_url_rule(rule, ep, hub.need()(fn) if ep not in OWNER_ONLY else hub.need("owner")(fn), methods=methods)
    hub.T.update(TEMPLATES)


def ensure(con):
    if True:
        cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
        if "bot_report" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN bot_report TEXT")
        if "device_info" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN device_info TEXT")
        dcols = {r[1] for r in con.execute("PRAGMA table_info(devices)")}
        if "approved" not in dcols:
            con.execute("ALTER TABLE devices ADD COLUMN approved INTEGER DEFAULT 1")
        if "info" not in dcols:
            con.execute("ALTER TABLE devices ADD COLUMN info TEXT")
        con.executescript("""
CREATE TABLE IF NOT EXISTS scopes (
    id INTEGER PRIMARY KEY, client TEXT, line TEXT, version INTEGER, filename TEXT, text TEXT, card TEXT, feas TEXT, proposal TEXT,
    samples TEXT, status TEXT DEFAULT 'draft', created_at TEXT, approved_at TEXT, approved_by TEXT, campaign TEXT
);
CREATE TABLE IF NOT EXISTS bot_commands (
    id INTEGER PRIMARY KEY, user_id INTEGER, notice_id INTEGER, cmd TEXT, at TEXT, by TEXT
);
""")
        con.commit()


# ---------------------------------------------------------------- reports coming in, commands going out

def ingest_extra(con, uid, e):
    """Handle the bot's report and device details. True when handled."""
    t = e.get("type") if isinstance(e, dict) else None
    if t == "__botreport":
        d = e.get("data") or {}
        rp = H.open_team(d.get("box")) if d.get("box") else d
        if isinstance(rp, dict):
            con.execute("UPDATE users SET bot_report=? WHERE id=?", (json.dumps(rp)[:2_000_000], uid))
        return True
    if t == "__dnc":                                   # new do-not-contact codes from a laptop: kept here; everyone else catches up
        _dnc_table(con)
        for h in ((e.get("data") or {}).get("h") or [])[:5000]:
            if isinstance(h, str) and len(h) == 16 and all(c in "0123456789abcdef" for c in h):
                con.execute("INSERT OR IGNORE INTO dnc(h, at, user_id) VALUES (?,?,?)", (h, H.iso(), uid))
        return True
    if t == "__dnc_del":                               # the owner took a mistaken stop back: off the list, and off everyone's
        who = con.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if not who or who["role"] != "owner":          # nobody else can unblock anyone, whatever their laptop sends
            con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)",
                        (H.iso(), f"user {uid}", "dnc_remove_refused", "a non-owner laptop tried to take addresses off do-not-contact"))
            return True
        _dnc_table(con)
        hs = [h for h in ((e.get("data") or {}).get("h") or [])[:500]
              if isinstance(h, str) and len(h) == 16 and all(c in "0123456789abcdef" for c in h)]
        for h in hs:
            con.execute("DELETE FROM dnc WHERE h=?", (h,))
        if hs:
            for u in con.execute("SELECT id FROM users WHERE id!=? AND status='active' AND last_seen IS NOT NULL", (uid,)).fetchall():
                con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (u["id"], json.dumps({"action": "dnc_del", "h": hs})))
            con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)",
                        (H.iso(), f"user {uid}", "dnc_removed", f"{len(hs)} address code(s) taken off the team's do-not-contact list"))
        return True
    if t == "__dnc_req":                               # a laptop without the whole list (new, rebuilt or restored): start it from 0
        _dnc_table(con)
        con.execute("DELETE FROM notices WHERE user_id=? AND delivered=0 AND item LIKE ?", (uid, '{"action": "dnc"%'))
        H._m_set(con, f"dnc_upto_{uid}", "0")
        H._m_set(con, f"dnc_req_{uid}", "1")
        return True
    if t == "__device":
        info = e.get("data") or {}
        con.execute("UPDATE users SET device_info=? WHERE id=?", (json.dumps(info)[:4000], uid))
        con.execute("UPDATE devices SET info=? WHERE user_id=? AND revoked=0 AND name=?", (json.dumps(info)[:4000], uid, str(info.get("device") or "")[:80]))
        return True
    return False


def _dnc_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS dnc (h TEXT PRIMARY KEY, at TEXT, user_id INTEGER)")


def dnc_catch_up(con, uid):
    """Give this person the do-not-contact codes they don't have yet: at most one waiting notice at a time (1,000 codes),
    never changed once written. When it is acknowledged their position moves on (dnc_acked) and the next one follows."""
    _dnc_table(con)
    u = con.execute("SELECT status, last_seen FROM users WHERE id=?", (uid,)).fetchone()
    if not u or u["status"] != "active" or not u["last_seen"]:
        return                                         # invited, disabled or removed: nothing waits for them
    if con.execute("SELECT 1 FROM notices WHERE user_id=? AND delivered=0 AND item LIKE ?", (uid, '{"action": "dnc"%')).fetchone():
        return
    upto = int(H._m_get(con, f"dnc_upto_{uid}", "0") or 0)
    rows = con.execute("SELECT rowid, h FROM dnc WHERE rowid>? ORDER BY rowid LIMIT 1000", (upto,)).fetchall()
    top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM dnc").fetchone()[0]
    asked = H._m_get(con, f"dnc_req_{uid}", "") == "1"
    if rows:
        last = rows[-1][0]
        con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)",
                    (uid, json.dumps({"action": "dnc", "h": [r[1] for r in rows], "to": last, "last": last >= top})))
    elif asked:
        con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (uid, json.dumps({"action": "dnc", "h": [], "to": upto, "last": True})))
    if asked:
        H._m_set(con, f"dnc_req_{uid}", "")
    con.commit()                                       # the notice must exist when the laptop acknowledges it


def dnc_acked(con, uid, item):
    if isinstance(item, dict) and item.get("action") == "dnc" and isinstance(item.get("to"), int):
        H._m_set(con, f"dnc_upto_{uid}", str(item["to"]))


def bots(con):
    return [dict(u) for u in con.execute("SELECT * FROM users WHERE role='bot' AND status!='removed' ORDER BY id").fetchall()]


def send_cmd(con, uid, cmd):
    """Queue a change for the bot. Sealed with the team key once the bot has it."""
    vk = H._secret_store().load().get(f"VIS_KEY_{uid}")
    item = {"action": "bot_cmd", "cmd": H.seal_team(cmd) if vk else cmd}
    nid = con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (uid, json.dumps(item))).lastrowid
    con.execute("INSERT INTO bot_commands(user_id, notice_id, cmd, at, by) VALUES (?,?,?,?,?)",
                (uid, nid, json.dumps(cmd)[:4000], H.iso(), (g.me or {}).get("email", "")))
    con.commit()
    H.audit("bot_command", f"{cmd.get('cmd')} → bot {uid}")
    return nid


def config_extra(con):
    """Added to the settings every BSO receives: the kill switch and update control."""
    m = lambda k: (H._m_get(con, k, "") or "")
    inboxes = []
    for u in con.execute("SELECT bot_report FROM users WHERE role='bot' AND status!='removed' AND bot_report IS NOT NULL").fetchall():
        try:
            inboxes += [i["email"].lower() for i in json.loads(u[0]).get("inboxes", [])]
        except Exception:
            pass
    if not m("dnc_list"):                              # this console's do-not-contact list identity (a new console gets a new one)
        import secrets as _secrets
        H._m_set(con, "dnc_list", _secrets.token_hex(8))
    return {"kill": m("bot_kill"), "updates": {"hold": m("upd_hold") == "1", "min_version": m("upd_min"), "release_now": m("upd_now")},
            "bot_inboxes": sorted(set(inboxes)), "dnc_list": m("dnc_list")}


def watch(con):
    """Email the owner when a bot hasn't checked in for 2 hours (once per silence)."""
    for u in bots(con):
        if u["status"] != "active" or not u["last_seen"]:
            continue
        ts = H.parse(u["last_seen"])
        if not ts or H.now() - ts < timedelta(hours=2):
            continue
        key = f"silent_{u['id']}"
        if H._m_get(con, key) == u["last_seen"]:
            continue
        H._m_set(con, key, u["last_seen"])
        owner = con.execute("SELECT email FROM users WHERE role='owner' LIMIT 1").fetchone()
        try:
            if owner:
                H.send_mail(owner["email"], f"BSO Bot silent: {u['name']}",
                            f"The BSO Bot \"{u['name']}\" hasn't checked in since {u['last_seen']} (over 2 hours).\n\n"
                            "Check the bot laptop: is it on, plugged in and online, and is BSO running? Nothing is lost while it's off; "
                            "it catches up when it starts again.")
        except Exception as e:
            print("bot silent alert:", e)
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)",
                    (H.iso(), "console", "bot_silent", f"{u['name']} last seen {u['last_seen']}"))
        con.commit()
    for u in bots(con):                     # online but sending nothing: a stuck run, every inbox paused, the kill switch...
        why = not_sending(u)
        if not why:
            continue
        key = f"nosend_{u['id']}"
        last = H._m_get(con, key) or ""
        if last and H.parse(last) and H.now() - H.parse(last) < timedelta(hours=6):
            continue
        H._m_set(con, key, H.iso())
        owner = con.execute("SELECT email FROM users WHERE role='owner' LIMIT 1").fetchone()
        try:
            if owner:
                H.send_mail(owner["email"], f"BSO Bot not sending: {u['name']}",
                            f"The BSO Bot \"{u['name']}\" is online but hasn't sent an email for over 3 hours while leads are waiting.\n\n{why}\n\n"
                            "Open Master Console → BSO Bot to see why (\"Why not more emails today?\").")
        except Exception as e:
            print("bot not-sending alert:", e)
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (H.iso(), "console", "bot_not_sending", f"{u['name']}: {why}"[:500]))
        con.commit()


def not_sending(u, hours=3, loc=None):
    """Why an online bot is not sending (None when it's fine, off-hours, in test mode or has nothing due)."""
    if u["status"] != "active" or not u["last_seen"] or not u["bot_report"]:
        return None
    ts = H.parse(u["last_seen"])
    if not ts or H.now() - ts > timedelta(hours=2):
        return None                                       # silent altogether: the other alert covers it
    try:
        rp = json.loads(u["bot_report"])
    except ValueError:
        return None
    if rp.get("test_mode") or not rp.get("due_now"):
        return None
    loc = loc or datetime.now()
    if loc.weekday() >= 5 or not (9 <= loc.hour < 18):
        return None
    last = H.parse(rp.get("last_sent_at") or "") if rp.get("last_sent_at") else None
    if last and H.now() - last < timedelta(hours=hours):
        return None
    lr = rp.get("last_run") or {}
    parts = [f"{rp.get('due_now')} lead(s) are due."]
    if rp.get("killed"):
        parts.append(f"Sending is stopped: {rp.get('killed_why') or 'kill switch on'}.")
    if rp.get("dnc_wait"):
        parts.append("The bot is waiting for the team's do-not-contact list from this console before sending first emails. "
                     "Keep the console open and on the same version as the bot.")
    if lr.get("status") == "running" and lr.get("started_at") and H.parse(lr["started_at"]) and H.now() - H.parse(lr["started_at"]) > timedelta(hours=2):
        parts.append(f"A run has been 'running' since {lr['started_at'][:16]} UTC: it is probably stuck. Restart BSO on the bot laptop.")
    if rp.get("breakers"):
        parts.append(f"{len(rp['breakers'])} campaign(s) or list(s) paused by the safety breaker.")
    paused = [i for i in rp.get("inboxes") or [] if i.get("paused")]
    if paused:
        parts.append(f"{len(paused)} of {len(rp.get('inboxes') or [])} inboxes are paused.")
    parts += [f"Why not more emails: {w[1] if isinstance(w, (list, tuple)) else w}" for w in (rp.get("why_not") or [])[:3]]
    return "\n".join(parts)


# ---------------------------------------------------------------- the bot page

def _pick_bot(con):
    bs = bots(con)
    uid = request.values.get("u", type=int)
    b = next((x for x in bs if x["id"] == uid), bs[0] if bs else None)
    return b, bs


def bot_page():
    con = H.db()
    b, bs = _pick_bot(con)
    rp = json.loads((b or {}).get("bot_report") or "{}") if b else {}
    pending = []
    if b:
        pending = [dict(r, cmd=json.loads(r["cmd"] or "{}")) for r in con.execute(
            "SELECT c.*, n.delivered FROM bot_commands c LEFT JOIN notices n ON n.id=c.notice_id WHERE c.user_id=? ORDER BY c.id DESC LIMIT 15", (b["id"],))]
    return H.render("bot.html", b=b, bs=bs, rp=rp, pending=pending, kill=H.meta_get("bot_kill", ""), tab="bot",
                    online=H.is_online(b["last_seen"]) if b else False, ago=H.ago)


def _bot_uid():
    con = H.db()
    b, _ = _pick_bot(con)
    if not b:
        abort(400, "No BSO Bot user yet. Add one under Users with the role BSO Bot.")
    return con, b["id"]


def bot_kill():
    on = request.form.get("on") == "1"
    H.meta_set("bot_kill", f"stopped by {g.me['name']} {H.iso()[:16]}" if on else "")
    con, uid = _bot_uid()
    send_cmd(con, uid, {"cmd": "kill", "on": on})
    return redirect(url_for("bot_page", u=uid, msg="All sending stops within a minute." if on else "Sending starts again at the next check-in."))


def bot_client():
    con, uid = _bot_uid()
    f = request.form
    if not f.get("name", "").strip():
        return redirect(url_for("bot_page", u=uid, msg="Give the client a name."))
    send_cmd(con, uid, {"cmd": "client", "name": f["name"].strip(), "line": f.get("line", "").strip()})
    return redirect(url_for("bot_page", u=uid, msg="Client sent to the bot."))


def bot_domain():
    con, uid = _bot_uid()
    f = request.form
    dom = f.get("domain", "").strip().lower().lstrip("@")
    rp = json.loads((con.execute("SELECT bot_report FROM users WHERE id=?", (uid,)).fetchone() or {"bot_report": "{}"})["bot_report"] or "{}")
    other = next((d for d in rp.get("domains", []) if d["domain"] == dom and d.get("client") and d["client"] != f.get("client")), None)
    if other:
        return redirect(url_for("bot_page", u=uid, msg=f"{dom} belongs to {other['client']}. A domain is never shared between clients.", err=1))
    send_cmd(con, uid, {"cmd": "domain", "domain": dom, "client": f.get("client"), "cap": int(f.get("cap") or 0)})
    return redirect(url_for("bot_page", u=uid, msg=f"{dom} sent to the bot."))


def bot_campaign():
    con, uid = _bot_uid()
    f = request.form
    cmd = {"cmd": "campaign", "id": f.get("id", type=int) or None}
    for k in ("name", "client", "autonomy", "info_mode", "status"):
        if f.get(k):
            cmd[k] = f[k]
    if f.get("arrive_from") and f.get("arrive_to"):
        cmd["arrive"] = f"{f['arrive_from']}-{f['arrive_to']}"
    if "info_text" in f:
        cmd["info_text"] = f.get("info_text", "").replace("\r\n", "\n")
    if f.get("weight"):
        cmd["weight"] = int(f["weight"])
    if "domains_sent" in f:
        cmd["domains"] = f.getlist("domains")
    send_cmd(con, uid, cmd)
    return redirect(url_for("bot_page", u=uid, msg="Campaign changes sent to the bot. They apply at its next check-in (about a minute)."))


def bot_spot():
    con, uid = _bot_uid()
    send_cmd(con, uid, {"cmd": "spot", "id": request.form.get("id", type=int), "status": request.form.get("status", "ok"),
                        "note": request.form.get("note", "")})
    return redirect(url_for("bot_page", u=uid, msg="Spot check recorded."))


def bot_resume():
    con, uid = _bot_uid()
    send_cmd(con, uid, {"cmd": "resume_inbox", "email": request.form.get("email", "")})
    return redirect(url_for("bot_page", u=uid, msg="The inbox resumes at the bot's next check-in."))


def bot_breaker():
    con, uid = _bot_uid()
    send_cmd(con, uid, {"cmd": "resume_breaker", "id": request.form.get("id", type=int)})
    return redirect(url_for("bot_page", u=uid, msg="Resumes at the bot's next check-in."))


def bot_template():
    con, uid = _bot_uid()
    rp = json.loads((con.execute("SELECT bot_report FROM users WHERE id=?", (uid,)).fetchone()["bot_report"]) or "{}")
    import ops
    rows = [{"code": c.get("code"), "client": c.get("client"), "name": c.get("name"), "status": c.get("status"),
             "days": (c.get("stock") or {}).get("days"), "ready": (c.get("stock") or {}).get("ready"),
             "new": (c.get("stock") or {}).get("new_per_day")} for c in rp.get("campaigns", [])]
    data = ops.build_template(rows)
    return send_file(io.BytesIO(data), as_attachment=True, download_name="BSO-lead-template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------- devices and updates

def devices_page():
    con = H.db()
    devs = [dict(r) for r in con.execute(
        "SELECT d.*, u.name uname, u.role, u.username, u.app_version FROM devices d JOIN users u ON u.id=d.user_id "
        "WHERE u.status!='removed' ORDER BY d.revoked, d.approved, u.name, d.last_seen DESC").fetchall()]
    for d in devs:
        d["info"] = json.loads(d.get("info") or "{}")
    reported = [dict(u, info=json.loads(u["device_info"] or "{}")) for u in con.execute(
        "SELECT id, name, role, username, app_version, last_seen, device_info FROM users WHERE device_info IS NOT NULL AND status!='removed' ORDER BY name")]
    upd = config_extra(con)["updates"]
    return H.render("devices.html", devs=devs, reported=reported, upd=upd, tab="devices", ago=H.ago)


def device_action(did):
    con = H.db()
    d = con.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not d:
        abort(404)
    act = request.form.get("action")
    if act == "approve":
        con.execute("UPDATE devices SET approved=1 WHERE id=?", (did,))
        H.audit("device_approved", f"device {d['name']} of user {d['user_id']}")
    elif act == "revoke":
        con.execute("UPDATE devices SET revoked=1 WHERE id=?", (did,))
        H.audit("device_revoked", f"device {d['name']} of user {d['user_id']}")
    con.commit()
    return redirect(url_for("devices_page", msg="Saved."))


def updates_save():
    f = request.form
    H.meta_set("upd_hold", "1" if f.get("hold") == "on" else "")
    H.meta_set("upd_min", f.get("min_version", "").strip())
    if f.get("release_now"):
        H.meta_set("upd_now", H.iso())
    H.audit("updates", f"hold={f.get('hold') == 'on'} min={f.get('min_version', '')}")
    return redirect(url_for("devices_page", msg="Update settings saved; every BSO picks them up at its next check-in."))


# ---------------------------------------------------------------- Client Scope room

SCOPE_FIELDS = [
    ("campaign_type", "Campaign type", "meeting booking, event or webinar invite, product launch, re-engagement"),
    ("titles", "Job titles to target", ""), ("industries", "Industries", ""), ("company_size", "Company size", ""),
    ("countries", "Countries", ""), ("offer", "Offer: what the client sells", ""),
    ("proof_points", "Proof points the emails may state", ""), ("tone", "Tone", ""), ("avoid", "Things to avoid", ""),
    ("volume", "Volume", ""), ("start", "Start date", ""), ("end", "End date", ""),
    ("success", "Success measure", "e.g. 12 meetings a month"), ("handover", "Handover rules", ""),
    ("line", "Client's line of business", "e.g. tech services, real estate"),
]
CAMPAIGN_TYPES = {
    "meeting booking": "a meeting with the client's team is the goal; interest is handed over",
    "event or webinar invite": "a registration is the goal; the bot can send the registration link as an information answer",
    "product launch": "awareness and a first conversation about the new product",
    "re-engagement": "people who went quiet; softer first emails, shorter sequence",
}
UNSUPPORTED = [(r"linked\s?in", "LinkedIn steps (BSO sends email only)"), (r"\b(phone|cold call|calling|ring)\b", "phone steps"),
               (r"\b(sms|text message|whatsapp)\b", "SMS / WhatsApp steps"), (r"\battach(ment|ed)?\b", "attachments in first emails"),
               (r"\b(buy|purchase|scrape)\w*\s+(a\s+)?(list|data|leads)", "buying or scraping lead lists")]
CONSENT = ["germany", "france", "netherlands", "belgium", "spain", "italy", "portugal", "austria", "sweden", "denmark", "finland", "poland",
           "ireland", "united kingdom", "uk", "canada", "eu", "europe", "switzerland", "norway"]


def doc_text(name, data):
    n = (name or "").lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if n.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if n.endswith(".eml"):
        import email as _email
        m = _email.message_from_bytes(data)
        parts = [p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
                 for p in (m.walk() if m.is_multipart() else [m]) if p.get_content_type() == "text/plain" and p.get_payload(decode=True)]
        return (m.get("Subject") or "") + "\n" + "\n".join(parts)
    return data.decode("utf-8", "replace")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def extract_card(text):
    """The Scope card: every field with the sentence it came from, and questions for what's missing."""
    import bot
    keys = ", ".join(f'"{k}"' for k, _, _ in SCOPE_FIELDS)
    prompt = ("Read this client scope document for a cold email outreach campaign and fill in a JSON object with exactly these keys: "
              f"{keys}, plus \"questions\" (a list of questions to ask the client about anything missing or unclear). "
              "Each key except questions maps to {\"value\": \"…\", \"source\": \"the exact sentence copied word for word from the document\"}. "
              "If the document doesn't say, use {\"value\": \"\", \"source\": \"\"} and add a question. Never guess. "
              f"campaign_type must be one of: {', '.join(CAMPAIGN_TYPES)}, or \"other\".\n\nDocument:\n" + text[:30000] +
              "\n\nAnswer with the JSON only.")
    raw = bot.llm(H._learn_cfg(H.db()) | {"models": {"write": "claude-sonnet-5-5", "personalize": "claude-sonnet-5-5"}}, "write", prompt, max_tokens=3000)
    card = {}
    if raw:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            card = json.loads(m.group(0)) if m else {}
        except ValueError:
            card = {}
    out, questions = {}, [q for q in (card.get("questions") or []) if isinstance(q, str)]
    body = _norm(text)
    for k, label, _ in SCOPE_FIELDS:
        v = card.get(k) if isinstance(card.get(k), dict) else {"value": card.get(k) or "", "source": ""}
        src = str(v.get("source") or "")
        ok = bool(src) and _norm(src)[:120] in body
        out[k] = {"value": str(v.get("value") or "").strip(), "source": src.strip(), "source_ok": ok}
        if not out[k]["value"]:
            if not any(label.lower().split(":")[0] in q.lower() for q in questions):
                questions.append(f"{label}: not stated in the scope. What should it be?")
        elif src and not ok:
            out[k]["warning"] = "the quoted sentence wasn't found in the document: check this field"
    if not raw:
        questions.insert(0, "The AI reader isn't available (no AI key in BSO setup), so fill in the fields by hand.")
    return out, questions


def _num(s):
    m = re.search(r"(\d[\d,]*)", str(s or ""))
    return int(m.group(1).replace(",", "")) if m else None


def feasibility(con, card, client, text, bot_report):
    """What the scope needs: leads, inboxes, time, cost, and the conflicts with BSO's rules."""
    rp = bot_report or {}
    my_domains = [d for d in rp.get("domains", []) if (d.get("client") or "").lower() == (client or "").lower()]
    my_inboxes = [i for i in rp.get("inboxes", []) if any(i["email"].endswith("@" + d["domain"]) for d in my_domains)]
    per_inbox = round(sum(i.get("limit") or 0 for i in my_inboxes) / len(my_inboxes)) if my_inboxes else 20
    per_inbox = per_inbox or 20
    target = _num(card.get("success", {}).get("value")) or 0
    sent_first = con.execute("SELECT COUNT(*) FROM events WHERE type='sent' AND step=0 AND at>=?", (H.iso(H.now() - timedelta(days=120)),)).fetchone()[0]
    meetings = con.execute("SELECT COUNT(*) FROM handovers WHERE status IN ('meeting','met','proposal','negotiation','won') AND at>=?",
                           (H.iso(H.now() - timedelta(days=120)),)).fetchone()[0]
    rate, basis = (meetings / sent_first, f"your last 120 days ({meetings} meetings from {sent_first} first emails)") if sent_first >= 200 and meetings else \
        (0.005, "an assumed 0.5% of contacted leads (no history yet)")
    steps, stop = 4, 0.10
    cost = sum((1 - stop) ** k for k in range(steps))
    leads_month = math.ceil(target / rate) if target and rate else 0
    new_day = leads_month / 21 if leads_month else 0
    emails_day = new_day * cost
    inboxes_needed = math.ceil(emails_day / per_inbox) if emails_day else 0
    have = len(my_inboxes)
    extra = max(0, inboxes_needed - have)
    domains_needed = math.ceil(extra / 3) if extra else 0
    warm_days = 21 if extra else 0
    conflicts = []
    low = text.lower()
    for rx, what in UNSUPPORTED:
        if re.search(rx, low):
            conflicts.append(f"The scope mentions {what}: BSO can't do this; agree with the client what replaces it.")
    regions = [c for c in CONSENT if re.search(r"\b" + re.escape(c) + r"\b", (card.get("countries", {}).get("value") or "").lower())]
    if regions:
        conflicts.append("Targets include " + ", ".join(sorted(set(regions))) + ": these have stricter consent rules for cold email. "
                         "Check the legal basis before launch (this is a prompt to check, not legal advice).")
    if re.search(r"\d", card.get("proof_points", {}).get("value") or ""):
        conflicts.append("The proof points contain figures: make sure the client can back each one; the emails will only use these.")
    if not card.get("proof_points", {}).get("value"):
        conflicts.append("No proof points supplied: ask the client for 2-3 results the emails may mention.")
    vol = _num(card.get("volume", {}).get("value"))
    cap_now = have * per_inbox
    if vol and cap_now and vol > cap_now * 1.2:
        conflicts.append(f"The requested volume ({vol}) is above what this client's inboxes can send today (about {cap_now} a day).")
    return {"target": target, "rate": rate, "basis": basis, "leads_month": leads_month, "new_per_day": round(new_day, 1),
            "emails_per_day": round(emails_day, 1), "per_inbox": per_inbox, "inboxes_needed": inboxes_needed, "inboxes_have": have,
            "extra_inboxes": extra, "domains_needed": domains_needed, "warm_days": warm_days, "domains": [d["domain"] for d in my_domains],
            "ai_cost_month": round(leads_month * cost * 0.003, 2), "conflicts": conflicts, "cost_per_lead": round(cost, 2)}


def proposal(card, client, feas):
    v = lambda k: (card.get(k) or {}).get("value", "")
    brief = {"offer": v("offer"), "ideal_customer": ", ".join(x for x in (v("titles"), v("industries"), v("company_size"), v("countries")) if x),
             "proof_points": [p.strip() for p in re.split(r"[;\n•]+", v("proof_points")) if p.strip()],
             "tone": v("tone") or "plain, direct and friendly", "rules": ("Never: " + v("avoid")) if v("avoid") else "one call to action; no exaggeration"}
    name = f"{client}: {(v('campaign_type') or 'campaign').split()[0].title()} {v('countries')[:20]}".strip()
    return {"name": name[:60], "client": client, "line": v("line"), "brief": brief, "domains": feas.get("domains", []),
            "autonomy": "assisted", "arrive": "08:45-10:30", "ends_at": v("end"), "type": v("campaign_type"),
            "type_note": CAMPAIGN_TYPES.get((v("campaign_type") or "").lower(), "a type BSO has no playbook for yet: add it deliberately")}


def samples(prop):
    import bot
    b = prop["brief"]
    prompt = ("Write 3 short cold emails (under 110 words each) for this brief, one per target group if several are named. "
              f"Offer: {b['offer']}\nWho: {b['ideal_customer']}\nFacts you may use (and no others): {'; '.join(b['proof_points'])}\n"
              f"Tone: {b['tone']}\nRules: {b['rules']}\nUse {{first_name}} and {{company}}. Separate the emails with a line containing only ---.")
    raw = bot.llm(H._learn_cfg(H.db()), "write", prompt, max_tokens=1500)
    return [x.strip() for x in (raw or "").split("\n---") if x.strip()][:3]


def diff_cards(old, new):
    out = []
    for k, label, _ in SCOPE_FIELDS:
        a, b = (old.get(k) or {}).get("value", ""), (new.get(k) or {}).get("value", "")
        if _norm(a) != _norm(b):
            out.append({"field": label, "was": a, "now": b})
    return out


def scope_list():
    con = H.db()
    rows = [dict(r) for r in con.execute("SELECT id, client, version, filename, status, created_at, approved_at, campaign FROM scopes ORDER BY client, version DESC")]
    b = bots(con)
    rp = json.loads(b[0]["bot_report"] or "{}") if b else {}
    return H.render("scopes.html", rows=rows, clients=rp.get("clients", []), tab="clients")


def scope_new():
    con = H.db()
    f = request.form
    client = f.get("client", "").strip()
    if not client:
        return redirect(url_for("scope_list", msg="Name the client.", err=1))
    up = request.files.get("file")
    text, fname = f.get("text", "").strip(), ""
    if up and up.filename:
        fname = up.filename
        try:
            text = doc_text(fname, up.read()) + ("\n" + text if text else "")
        except Exception as e:
            return redirect(url_for("scope_list", msg=f"Couldn't read {fname} ({e}). Paste the text instead.", err=1))
    if len(text) < 40:
        return redirect(url_for("scope_list", msg="Upload the scope document (PDF, Word, email or text) or paste it.", err=1))
    card, questions = extract_card(text)
    if f.get("line") and not card["line"]["value"]:
        card["line"]["value"] = f["line"].strip()
    b = bots(con)
    rp = json.loads(b[0]["bot_report"] or "{}") if b else {}
    feas = feasibility(con, card, client, text, rp)
    prop = proposal(card, client, feas)
    ver = (con.execute("SELECT MAX(version) FROM scopes WHERE lower(client)=lower(?)", (client,)).fetchone()[0] or 0) + 1
    sid = con.execute("INSERT INTO scopes(client, line, version, filename, text, card, feas, proposal, samples, status, created_at) VALUES (?,?,?,?,?,?,?,?,?, 'draft', ?)",
                      (client, card["line"]["value"], ver, fname, text, json.dumps(dict(card, _questions=questions)), json.dumps(feas), json.dumps(prop),
                       json.dumps([]), H.iso())).lastrowid
    con.commit()
    H.audit("scope_added", f"{client} v{ver}")
    return redirect(url_for("scope_detail", sid=sid))


def _scope(con, sid):
    r = con.execute("SELECT * FROM scopes WHERE id=?", (sid,)).fetchone()
    if not r:
        abort(404)
    d = dict(r)
    d["card"] = json.loads(d["card"] or "{}")
    d["questions"] = d["card"].pop("_questions", [])
    d["feas"] = json.loads(d["feas"] or "{}")
    d["proposal"] = json.loads(d["proposal"] or "{}")
    d["samples"] = json.loads(d["samples"] or "[]")
    return d


def scope_detail(sid):
    con = H.db()
    s = _scope(con, sid)
    prev = con.execute("SELECT * FROM scopes WHERE lower(client)=lower(?) AND version<? ORDER BY version DESC LIMIT 1", (s["client"], s["version"])).fetchone()
    changes = diff_cards(json.loads(prev["card"] or "{}"), s["card"]) if prev else []
    progress = None
    if s["campaign"]:
        b = bots(con)
        rp = json.loads(b[0]["bot_report"] or "{}") if b else {}
        c = next((c for c in rp.get("campaigns", []) if c["name"] == s["campaign"]), None)
        met = con.execute("SELECT COUNT(*) FROM handovers WHERE status IN ('meeting','met','proposal','negotiation','won') AND payload LIKE ?",
                          (f'%{s["campaign"]}%',)).fetchone()[0]
        progress = {"campaign": c, "met": met, "target": s["feas"].get("target"), "since": s["approved_at"]}
    return H.render("scope.html", s=s, fields=SCOPE_FIELDS, changes=changes, prev=dict(prev) if prev else None, progress=progress,
                    types=CAMPAIGN_TYPES, tab="clients", summary=client_summary(s))


def scope_save(sid):
    con = H.db()
    s = _scope(con, sid)
    f = request.form
    for k, _, _ in SCOPE_FIELDS:
        if f"v_{k}" in f:
            val = f.get(f"v_{k}", "").strip()
            if val != s["card"].get(k, {}).get("value"):
                s["card"][k] = {"value": val, "source": "edited by " + g.me["name"], "source_ok": True}
    b = bots(con)
    rp = json.loads(b[0]["bot_report"] or "{}") if b else {}
    feas = feasibility(con, s["card"], s["client"], s["text"], rp)
    prop = proposal(s["card"], s["client"], feas)
    for k in ("name", "autonomy", "arrive"):
        if f.get(f"p_{k}"):
            prop[k] = f[f"p_{k}"].strip()
    smp = s["samples"]
    if f.get("action") == "samples":
        smp = samples(prop)
    con.execute("UPDATE scopes SET card=?, feas=?, proposal=?, samples=?, line=? WHERE id=?",
                (json.dumps(dict(s["card"], _questions=s["questions"])), json.dumps(feas), json.dumps(prop), json.dumps(smp), s["card"]["line"]["value"], sid))
    con.commit()
    if f.get("action") == "approve":
        if not b:
            return redirect(url_for("scope_detail", sid=sid, msg="There's no BSO Bot user to send the setup to yet.", err=1))
        uid = b[0]["id"]
        send_cmd(con, uid, {"cmd": "client", "name": s["client"], "line": prop.get("line", "")})
        existing = next((c for c in rp.get("campaigns", []) if c["name"] == prop["name"]), None)
        cmd = {"cmd": "campaign", "id": existing["id"] if existing else None, "name": prop["name"], "client": s["client"], "brief": prop["brief"],
               "autonomy": prop.get("autonomy", "assisted"), "arrive": prop.get("arrive"), "status": "active"}
        if prop.get("domains"):
            cmd["domains"] = prop["domains"]
        if prop.get("ends_at") and re.match(r"\d{4}-\d{2}-\d{2}", prop["ends_at"]):
            cmd["ends_at"] = prop["ends_at"][:10]
        send_cmd(con, uid, cmd)
        con.execute("UPDATE scopes SET status='approved', approved_at=?, approved_by=?, campaign=? WHERE id=?", (H.iso(), g.me["email"], prop["name"], sid))
        con.execute("UPDATE scopes SET status='superseded' WHERE lower(client)=lower(?) AND id!=? AND status='approved'", (s["client"], sid))
        con.commit()
        H.audit("scope_approved", f"{s['client']} v{s['version']} → {prop['name']}")
        return redirect(url_for("scope_detail", sid=sid, msg="Approved. The bot sets up the campaign at its next check-in; it starts in Assisted mode."))
    return redirect(url_for("scope_detail", sid=sid, msg="Saved."))


def client_summary(s):
    v = lambda k: (s["card"].get(k) or {}).get("value", "") or "to be confirmed"
    fz = s["feas"]
    lines = [f"Outreach scope summary: {s['client']} (version {s['version']})", "",
             f"Campaign type: {v('campaign_type')}", f"Who we will contact: {v('titles')}; {v('industries')}; company size {v('company_size')}; in {v('countries')}",
             f"What we will say: {v('offer')}", f"Facts we may use: {v('proof_points')}", f"Tone: {v('tone')}; we will avoid: {v('avoid')}",
             f"Volume: {v('volume')}", f"Timeline: {v('start')} to {v('end')}", f"Success measure: {v('success')}",
             f"Handover of interested prospects: {v('handover')}", "",
             f"What this needs: about {fz.get('leads_month', 0)} new leads a month and {fz.get('inboxes_needed', 0)} sending inboxes"
             + (f" ({fz.get('extra_inboxes')} more to set up and warm up, about {fz.get('warm_days')} days)" if fz.get("extra_inboxes") else "") + ".",
             "", "Out of scope: LinkedIn, phone and SMS steps, buying or scraping lead lists, and claims not listed above.",
             "", "Please confirm or correct anything above."]
    return "\n".join(lines)


OWNER_ONLY = {"devices_page", "device_action", "updates_save", "bot_kill", "bot_breaker"}
ROUTES = [
    ("/bot", "bot_page", bot_page, ["GET"]),
    ("/bot/kill", "bot_kill", bot_kill, ["POST"]),
    ("/bot/client", "bot_client", bot_client, ["POST"]),
    ("/bot/domain", "bot_domain", bot_domain, ["POST"]),
    ("/bot/campaign", "bot_campaign", bot_campaign, ["POST"]),
    ("/bot/spot", "bot_spot", bot_spot, ["POST"]),
    ("/bot/resume", "bot_resume", bot_resume, ["POST"]),
    ("/bot/breaker", "bot_breaker", bot_breaker, ["POST"]),
    ("/bot/template.xlsx", "bot_template", bot_template, ["GET"]),
    ("/devices", "devices_page", devices_page, ["GET"]),
    ("/devices/<int:did>", "device_action", device_action, ["POST"]),
    ("/devices/updates", "updates_save", updates_save, ["POST"]),
    ("/clients", "scope_list", scope_list, ["GET"]),
    ("/clients/scope", "scope_new", scope_new, ["POST"]),
    ("/scopes/<int:sid>", "scope_detail", scope_detail, ["GET"]),
    ("/scopes/<int:sid>", "scope_save", scope_save, ["POST"]),
]

CSRF = '<input type="hidden" name="_csrf" value="{{ csrf }}">'
TEMPLATES = {}
GRID = "<style>.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}@media(max-width:800px){.grid2{grid-template-columns:1fr}}</style>"
TEMPLATES["bot.html"] = """{% extends 'base.html' %}{% block title %}BSO Bot{% endblock %}{% block body %}
{% if not b %}<div class="card"><div class="card-b">No BSO Bot yet. Add a user with the role <b>BSO Bot</b> under Users, set it up under BSO setup, and sign it in on the bot laptop.</div></div>{% else %}
{% if bs|length > 1 %}<p>{% for x in bs %}<a class="btn {{ 'brand' if x.id == b.id }}" href="{{ url_for('bot_page', u=x.id) }}">{{ x.name }}</a> {% endfor %}</p>{% endif %}
<div class="card"><div class="card-h"><h2>{{ b.name }}</h2><span class="badge {{ 'b-ok' if online else 'b-bad' }}">{{ 'online' if online else 'last seen ' ~ (ago(b.last_seen) if b.last_seen else 'never') }}</span>
<span class="muted" style="margin-left:8px">report {{ ago(rp.at) if rp.at else 'not received yet' }}{{ ' · version ' ~ rp.version if rp.version }}</span><span style="flex:1"></span>
{% if me.role == 'owner' %}<form method="post" action="{{ url_for('bot_kill', u=b.id) }}" style="margin:0" onsubmit="return confirm('{{ 'Start sending again?' if kill or rp.killed else 'Stop ALL sending for every client?' }}')">""" + CSRF + """
{% if kill or rp.killed %}<input type="hidden" name="on" value="0"><button class="btn brand">▶ Start sending</button>{% else %}<input type="hidden" name="on" value="1"><button class="btn danger">■ Stop all sending</button>{% endif %}</form>{% endif %}</div>
<div class="card-b">{% if rp.killed %}<div class="err">{{ rp.killed_why }}</div>{% endif %}
<h3 style="margin-top:0">Why not more emails today?</h3>{% for k, w in rp.why_not or [] %}<div>• {{ w }}</div>{% else %}<div class="muted">Nothing is holding sending back.</div>{% endfor %}</div></div>

<div class="card"><div class="card-h"><h2>Campaigns</h2><span style="flex:1"></span><a class="btn" href="{{ url_for('bot_template', u=b.id) }}">⬇ Lead template</a></div><table>
<tr><th>Campaign</th><th>Client</th><th>Domains</th><th>Autonomy</th><th class="r">Sent today</th><th class="r">Leads waiting</th><th class="r">New / day</th><th class="r">Days of stock</th></tr>
{% for c in rp.campaigns or [] %}<tr><td><b>{{ c.name }}</b><br><span class="muted">{{ c.code }} · {{ c.status }}</span>{% if c.suggest %}<br><span class="badge b-warn">{{ c.suggest }}</span>{% endif %}</td><td>{{ c.client or '–' }}</td><td>{{ c.domains|join(', ') or '–' }}</td><td>{{ c.autonomy }}</td>
<td class="r">{{ c.sent_today }}</td><td class="r">{{ c.stock.ready if c.stock else '' }}</td><td class="r">{{ c.stock.new_per_day if c.stock else '' }}</td>
<td class="r">{% if c.stock %}<span class="badge {{ 'b-bad' if c.stock.days is not none and c.stock.days < 3 else 'b-warn' if c.stock.days is not none and c.stock.days < 10 else 'b-ok' }}">{{ c.stock.days if c.stock.days is not none else 'no capacity' }}</span>{% endif %}</td></tr>
<tr><td colspan="8"><details><summary>Change</summary><form method="post" action="{{ url_for('bot_campaign', u=b.id) }}" class="grid2">""" + CSRF + """<input type="hidden" name="id" value="{{ c.id }}"><input type="hidden" name="domains_sent" value="1">
<div><label>Name</label><input name="name" value="{{ c.name }}"></div><div><label>Client</label><select name="client"><option value="">–</option>{% for cl in rp.clients or [] %}<option {{ 'selected' if cl.name == c.client }}>{{ cl.name }}</option>{% endfor %}</select></div>
<div><label>Sending domains (this client's only)</label>{% for d in rp.domains or [] %}{% if not c.client or d.client == c.client %}<label style="font-weight:400"><input type="checkbox" name="domains" value="{{ d.domain }}" {{ 'checked' if d.domain in c.domains }}> {{ d.domain }} ({{ d.inboxes|length }} inboxes)</label>{% endif %}{% endfor %}</div>
<div><label>Share of shared domains (weight)</label><input name="weight" type="number" min="1" value="{{ c.weight }}"></div>
<div><label>Autonomy</label><select name="autonomy">{% for a in ['assisted','supervised','autonomous'] %}<option {{ 'selected' if a == c.autonomy }}>{{ a }}</option>{% endfor %}</select></div>
<div><label>First-email arrival window (lead's time)</label><input name="arrive_from" type="time" value="{{ c.window.split('-')[0] }}" style="width:45%"> to <input name="arrive_to" type="time" value="{{ c.window.split('-')[1] }}" style="width:45%"></div>
<div><label>Answers to information requests</label><select name="info_mode">{% for m, l in [('off','Off'),('prompt','AI prompt'),('template','Template')] %}<option value="{{ m }}" {{ 'selected' if m == c.info_mode }}>{{ l }}</option>{% endfor %}</select></div>
<div><label>Status</label><select name="status">{% for st in ['active','paused'] %}<option {{ 'selected' if st == c.status }}>{{ st }}</option>{% endfor %}</select></div>
<div style="grid-column:1/-1"><label>Prompt or template for information answers ({first_name}, {company}, {sender_name} work in templates)</label><textarea name="info_text" rows="4">{{ c.info_text }}</textarea></div>
<div><button class="btn brand">Send to the bot</button></div></form></details></td></tr>
{% else %}<tr><td colspan="8" class="muted">No report from the bot yet.</td></tr>{% endfor %}</table>
<details style="padding:10px 14px"><summary>New campaign</summary><form method="post" action="{{ url_for('bot_campaign', u=b.id) }}">""" + CSRF + """<label>Name</label><input name="name" required><label>Client</label><select name="client">{% for cl in rp.clients or [] %}<option>{{ cl.name }}</option>{% endfor %}</select><p><button class="btn">Create</button></p></form></details></div>

<div class="card"><div class="card-h"><h2>Clients and sending domains</h2></div><div class="card-b grid2">
<div><table><tr><th>Client</th><th>Line of business</th></tr>{% for cl in rp.clients or [] %}<tr><td>{{ cl.name }}</td><td>{{ cl.line or '–' }}</td></tr>{% else %}<tr><td colspan="2" class="muted">No clients yet.</td></tr>{% endfor %}</table>
<form method="post" action="{{ url_for('bot_client', u=b.id) }}">""" + CSRF + """<label>Add a client</label><input name="name" placeholder="Client name"><input name="line" placeholder="Line of business, e.g. tech services"><p><button class="btn">Add client</button></p></form></div>
<div><table><tr><th>Domain</th><th>Client</th><th class="r">Inboxes</th><th class="r">Sent today</th><th>Campaigns</th></tr>{% for d in rp.domains or [] %}<tr><td>{{ d.domain }}</td><td>{{ d.client or '–' }}</td><td class="r">{{ d.inboxes|length }}</td><td class="r">{{ d.sent_today }}{{ ' / ' ~ d.cap if d.cap }}</td><td>{{ d.campaigns|join(', ') }}</td></tr>{% else %}<tr><td colspan="5" class="muted">No sending domains yet.</td></tr>{% endfor %}</table>
<form method="post" action="{{ url_for('bot_domain', u=b.id) }}">""" + CSRF + """<label>Add a sending domain (its inboxes come from BSO setup)</label><input name="domain" placeholder="getneosofttech.com"><select name="client">{% for cl in rp.clients or [] %}<option>{{ cl.name }}</option>{% endfor %}</select><input name="cap" type="number" min="0" placeholder="Daily cap for the domain (0 = sum of its inboxes)"><p><button class="btn">Add domain</button></p></form>
<p class="muted">A domain belongs to one client only and is never shared with another.</p></div></div></div>

<div class="card"><div class="card-h"><h2>Inboxes</h2></div><table><tr><th>Inbox</th><th>Health</th><th class="r">Today's limit</th><th class="r">Sent today</th><th>Why</th><th>Spam test</th><th></th></tr>
{% for i in rp.inboxes or [] %}<tr><td>{{ i.email }}</td><td><span class="badge {{ 'b-ok' if i.rating == 'healthy' else 'b-bad' if i.rating == 'red' or i.paused else 'b-warn' if i.rating else '' }}">{{ 'paused' if i.paused else i.rating or ('ready' if i.ready else 'not checked') }}</span></td>
<td class="r">{{ i.limit }}</td><td class="r">{{ i.sent_today }}</td><td class="muted">{{ i.paused or (i.why if i.ready else i.ready_detail) }}</td><td class="muted">{{ i.seed }}</td>
<td>{% if i.paused %}<form method="post" action="{{ url_for('bot_resume', u=b.id) }}" style="margin:0">""" + CSRF + """<input type="hidden" name="email" value="{{ i.email }}"><button class="btn">Resume</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No inboxes reported yet.</td></tr>{% endfor %}</table></div>

{% if rp.breakers %}<div class="card" style="border-color:#c23934"><div class="card-h"><h2>Paused by the safety breaker</h2><span class="muted" style="margin-left:8px">too many bounces, complaints or stop requests; resuming is your decision</span></div><table>
{% for k in rp.breakers %}<tr><td>{{ {'campaign': 'Campaign', 'list': 'Imported list', 'restore': 'After a restore'}.get(k.kind, k.kind) }} <b>{{ k.key }}</b></td><td class="muted">{{ k.at[:16] }}</td><td>{{ k.reason }}</td>
<td><form method="post" action="{{ url_for('bot_breaker', u=b.id) }}" style="margin:0" onsubmit="return confirm('Resume? Fix the cause first (clean the list, check the copy).')">""" + CSRF + """<input type="hidden" name="id" value="{{ k.id }}"><button class="btn">Resume</button></form></td></tr>{% endfor %}</table></div>{% endif %}
<div class="card"><div class="card-h"><h2>Backups</h2></div><div class="card-b">{% set bk = rp.backup or {} %}{% if bk %}Last backup {{ bk.at[:16] }}: <span class="badge {{ 'b-ok' if bk.ok else 'b-bad' }}">{{ 'restore test passed' if bk.ok else 'problem' }}</span> {{ bk.detail }}<br>
Google Drive copy: {% if bk.drive_ok %}<span class="badge b-ok">saved and checked</span> {{ bk.drive }}{% elif bk.drive_detail %}<span class="badge b-bad">problem</span> {{ bk.drive_detail }}{% elif bk.offsite_ok %}<span class="badge b-ok">in the backup folder</span> {{ bk.offsite }}{% else %}<span class="badge b-warn">not yet</span> update the Google script to version 6 (Settings), then the bot copies every backup to your Google Drive{% endif %}
{% else %}<span class="muted">No backup reported yet (the bot makes one a day).</span>{% endif %}</div></div>

<div class="card"><div class="card-h"><h2>Spot checks</h2><span class="muted" style="margin-left:8px">emails the bot sent on its own, set aside for a person to look at</span></div>
{% for s in rp.spot_checks or [] %}<div class="card-b" style="border-top:1px solid #eee"><b>{{ s.lead_email }}</b> <span class="muted">from {{ s.sender }} · quality {{ s.score }} · {{ s.at[:16] }}</span><br><b>{{ s.subject }}</b><pre style="white-space:pre-wrap;font:inherit">{{ s.body }}</pre>
<form method="post" action="{{ url_for('bot_spot', u=b.id) }}" style="display:flex;gap:6px">""" + CSRF + """<input type="hidden" name="id" value="{{ s.id }}"><input name="note" placeholder="Note (optional)"><button class="btn" name="status" value="ok">Looks fine</button><button class="btn danger" name="status" value="problem">Problem</button></form></div>
{% else %}<div class="card-b muted">Nothing to look at.</div>{% endfor %}</div>

<div class="card"><div class="card-h"><h2>Lead intake</h2></div><table><tr><th>When</th><th>From</th><th>Subject</th><th class="r">Added</th><th class="r">Rejected</th><th>Detail</th></tr>
{% for r in rp.intake or [] %}<tr><td class="muted">{{ r.at[:16] }}</td><td>{{ r.frm }}</td><td>{{ r.subject }}</td><td class="r">{{ r.added }}</td><td class="r">{{ r.rejected }}</td><td class="muted" style="white-space:pre-wrap">{{ r.detail }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No lead files received yet.</td></tr>{% endfor %}</table></div>

<div class="card"><div class="card-h"><h2>Queue: next first emails</h2></div><table>{% for q in rp.queue or [] %}<tr><td>{{ q.email }}</td><td>{{ q.campaign }}</td><td class="muted">{{ q.next_send_at[:16].replace('T', ' ') }} UTC</td></tr>{% else %}<tr><td class="muted">Nothing queued.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Changes sent from here</h2></div><table>{% for p in pending %}<tr><td class="muted">{{ p.at[:16] }}</td><td>{{ p.cmd.cmd }} {{ p.cmd.name or p.cmd.domain or p.cmd.email or '' }}</td><td>{{ p.by }}</td><td><span class="badge {{ 'b-ok' if p.delivered else 'b-warn' }}">{{ 'applied' if p.delivered else 'waiting for the bot' }}</span></td></tr>{% else %}<tr><td class="muted">None yet.</td></tr>{% endfor %}</table>
{% for r in (rp.cmd_results or [])[-5:] %}<div class="muted" style="padding:2px 14px">{{ r.at[:16] }}: {{ r.result }}</div>{% endfor %}</div>
<div class="card"><div class="card-h"><h2>Activity: the bot's own decisions</h2></div><table>{% for a in rp.activity or [] %}<tr><td class="muted">{{ a.at[:16] }}</td><td>{{ a.kind }}</td><td>{{ a.detail }}</td></tr>{% else %}<tr><td class="muted">Nothing yet.</td></tr>{% endfor %}</table></div>
{% endif %}{% endblock %}"""

TEMPLATES["devices.html"] = """{% extends 'base.html' %}{% block title %}Devices{% endblock %}{% block body %}
<div class="note">Every laptop joined with a setup code from this console. A second laptop for the same person waits here for your approval before it gets any data. Revoking a laptop stops its access; use Users → Remove to also erase what's on it.</div>
<div class="card"><div class="card-h"><h2>Signed-in laptops</h2></div><table><tr><th>Person</th><th>Laptop</th><th>Windows user</th><th>Disk encrypted</th><th>Version</th><th>Last check-in</th><th></th></tr>
{% for d in devs %}<tr><td><b>{{ d.uname }}</b><br><span class="muted">{{ d.role }}</span></td><td>{{ d.name }}</td><td>{{ d.info.windows_user or '–' }}</td>
<td>{% if d.info.disk_encrypted is sameas true %}<span class="badge b-ok">yes</span>{% elif d.info.disk_encrypted is sameas false %}<span class="badge b-bad">no: turn on BitLocker</span>{% else %}<span class="muted">unknown</span>{% endif %}</td>
<td>{{ d.info.version or d.app_version or '–' }}</td><td class="muted">{{ ago(d.last_seen) if d.last_seen else '–' }}</td>
<td>{% if d.revoked %}<span class="badge b-bad">revoked</span>{% else %}<form method="post" action="{{ url_for('device_action', did=d.id) }}" style="margin:0;display:flex;gap:4px">""" + CSRF + """
{% if not d.approved %}<button class="btn brand" name="action" value="approve">Approve</button>{% endif %}<button class="btn danger" name="action" value="revoke" onclick="return confirm('Revoke this laptop?')">Revoke</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No laptops yet.</td></tr>{% endfor %}</table></div>
{% if reported %}<div class="card"><div class="card-h"><h2>Reported by each BSO</h2><span class="muted" style="margin-left:8px">(also covers BSOs that check in through Google Drive)</span></div><table>
<tr><th>Person</th><th>Laptop</th><th>Disk encrypted</th><th>Publisher certificate trusted</th><th>Version</th></tr>{% for u in reported %}<tr><td>{{ u.name }}</td><td>{{ u.info.device }}</td>
<td>{{ 'yes' if u.info.disk_encrypted is sameas true else 'no' if u.info.disk_encrypted is sameas false else 'unknown' }}</td><td>{{ 'yes' if u.info.cert_trusted else 'not yet' }}</td><td>{{ u.info.version or u.app_version }}</td></tr>{% endfor %}</table></div>{% endif %}
<div class="card"><div class="card-h"><h2>Updates</h2></div><div class="card-b"><p class="muted">Every BSO checks GitHub for a new release every 6 hours and installs only releases signed by BlacSwanken's build system. Your own BSO updates first, supervisors a few hours later, the bot 24 hours later and only when no email is due.</p>
<form method="post" action="{{ url_for('updates_save') }}">""" + CSRF + """<label style="font-weight:400"><input type="checkbox" name="hold" {{ 'checked' if upd.hold }}> Hold: nobody updates until I untick this (test a release first)</label>
<label>Minimum version (older BSOs update at their next check, for urgent fixes)</label><input name="min_version" value="{{ upd.min_version }}" placeholder="e.g. 1.0.60">
<label style="font-weight:400"><input type="checkbox" name="release_now" value="1"> Release the latest version to everyone now (skip the staged roll-out)</label><p><button class="btn brand">Save</button></p></form></div></div>{% endblock %}"""

TEMPLATES["scopes.html"] = """{% extends 'base.html' %}{% block title %}Clients{% endblock %}{% block body %}
<div class="note">The <b>Scope room</b>: upload a client's scope document and BSO turns it into a campaign setup with every field traced to the sentence it came from, checks what it needs (leads, inboxes, time, cost) and flags conflicts. Nothing reaches the bot until you approve it.</div>
<div class="card"><div class="card-h"><h2>New scope</h2></div><div class="card-b"><form method="post" action="{{ url_for('scope_new') }}" enctype="multipart/form-data">""" + CSRF + """
<div class="grid2"><div><label>Client</label><input name="client" list="clients" required><datalist id="clients">{% for c in clients %}<option>{{ c.name }}</option>{% endfor %}</datalist></div>
<div><label>Line of business (if the document doesn't say)</label><input name="line" placeholder="e.g. tech services"></div></div>
<label>Scope document (PDF, Word, email or text)</label><input type="file" name="file" accept=".pdf,.docx,.eml,.txt,.md"><label>…or paste it</label><textarea name="text" rows="6"></textarea>
<p><button class="btn brand">Read the scope</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Scopes</h2></div><table><tr><th>Client</th><th>Version</th><th>File</th><th>Status</th><th>Campaign</th><th>Added</th></tr>
{% for r in rows %}<tr><td><a href="{{ url_for('scope_detail', sid=r.id) }}"><b>{{ r.client }}</b></a></td><td>v{{ r.version }}</td><td class="muted">{{ r.filename or 'pasted' }}</td><td><span class="badge {{ 'b-ok' if r.status == 'approved' else '' }}">{{ r.status }}</span></td><td>{{ r.campaign or '–' }}</td><td class="muted">{{ r.created_at[:10] }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No scopes yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""

TEMPLATES["scope.html"] = """{% extends 'base.html' %}{% block title %}Scope: {{ s.client }}{% endblock %}{% block body %}
<p><a href="{{ url_for('scope_list') }}">← All scopes</a></p>
<div class="card"><div class="card-h"><h2>{{ s.client }}: scope v{{ s.version }}</h2><span class="badge {{ 'b-ok' if s.status == 'approved' else '' }}" style="margin-left:8px">{{ s.status }}</span></div>
{% if changes %}<div class="card-b"><h3 style="margin-top:0">What changed since v{{ prev.version }}</h3><table><tr><th>Field</th><th>Was</th><th>Now</th></tr>{% for c in changes %}<tr><td>{{ c.field }}</td><td class="muted">{{ c.was or '–' }}</td><td>{{ c.now or '–' }}</td></tr>{% endfor %}</table>
<p class="muted">After approval, new leads get the new version; leads already in a sequence finish on the version they started with.</p></div>{% endif %}
{% if progress %}<div class="card-b"><h3 style="margin-top:0">Progress against the scope</h3>{% if progress.target %}<b>{{ progress.met }} of {{ progress.target }}</b> meetings or better since {{ (progress.since or '')[:10] }}.{% endif %}
{% if progress.campaign %} Campaign "{{ progress.campaign.name }}": sent today {{ progress.campaign.sent_today }}, {{ progress.campaign.stock.ready if progress.campaign.stock else 0 }} leads waiting.{% endif %}</div>{% endif %}</div>
<form method="post">""" + CSRF + """
<div class="card"><div class="card-h"><h2>Scope card</h2><span class="muted" style="margin-left:8px">each field with the sentence it came from</span></div><table><tr><th style="width:22%">Field</th><th style="width:38%">Value</th><th>Source</th></tr>
{% for k, label, hint in fields %}{% set f = s.card.get(k, {}) %}<tr><td><b>{{ label }}</b>{% if hint %}<br><span class="muted">{{ hint }}</span>{% endif %}</td><td><textarea name="v_{{ k }}" rows="2">{{ f.value }}</textarea></td>
<td class="muted">{% if f.source %}“{{ f.source }}”{% else %}–{% endif %}{% if f.warning %}<br><span class="badge b-bad">{{ f.warning }}</span>{% endif %}</td></tr>{% endfor %}</table></div>
{% if s.questions %}<div class="card"><div class="card-h"><h2>Questions for the client</h2></div><div class="card-b">{% for q in s.questions %}<div>• {{ q }}</div>{% endfor %}</div></div>{% endif %}
<div class="card"><div class="card-h"><h2>What it needs</h2></div><div class="card-b">
<div>Target: <b>{{ s.feas.target or 'not stated' }}</b> a month · conversion {{ '%.2f'|format(s.feas.rate * 100) }}% ({{ s.feas.basis }})</div>
<div>Leads needed: <b>about {{ s.feas.leads_month }} a month</b> ({{ s.feas.new_per_day }} new a working day, {{ s.feas.cost_per_lead }} emails each over the sequence)</div>
<div>Sending: about {{ s.feas.emails_per_day }} emails a day → <b>{{ s.feas.inboxes_needed }} inboxes</b> at {{ s.feas.per_inbox }} a day; this client has {{ s.feas.inboxes_have }}{% if s.feas.extra_inboxes %}: <b>{{ s.feas.extra_inboxes }} more on about {{ s.feas.domains_needed }} new domain(s)</b>, warmed up in Instantly for about {{ s.feas.warm_days }} days first{% endif %}.</div>
<div>AI writing: about ${{ s.feas.ai_cost_month }} a month.</div>
{% if s.feas.conflicts %}<h3>Conflicts</h3>{% for c in s.feas.conflicts %}<div class="err" style="margin:4px 0">{{ c }}</div>{% endfor %}{% endif %}</div></div>
<div class="card"><div class="card-h"><h2>Proposed setup</h2></div><div class="card-b grid2">
<div><label>Campaign name</label><input name="p_name" value="{{ s.proposal.name }}"></div><div><label>Autonomy to start with</label><select name="p_autonomy">{% for a in ['assisted','supervised','autonomous'] %}<option {{ 'selected' if a == s.proposal.autonomy }}>{{ a }}</option>{% endfor %}</select></div>
<div><label>Arrival window</label><input name="p_arrive" value="{{ s.proposal.arrive }}"></div><div><label>Sending domains</label><div>{{ s.proposal.domains|join(', ') or 'none yet for this client: add them on the BSO Bot page' }}</div></div>
<div style="grid-column:1/-1"><label>Campaign type</label><div>{{ s.proposal.type or '–' }}: <span class="muted">{{ s.proposal.type_note }}</span></div></div>
<div style="grid-column:1/-1"><label>Brief the bot writes from</label><pre style="white-space:pre-wrap;font:inherit">Offer: {{ s.proposal.brief.offer }}
Who: {{ s.proposal.brief.ideal_customer }}
Facts: {{ s.proposal.brief.proof_points|join('; ') }}
Tone: {{ s.proposal.brief.tone }}
Rules: {{ s.proposal.brief.rules }}</pre></div></div>
{% if s.samples %}<div class="card-b"><h3 style="margin-top:0">Sample emails</h3>{% for e in s.samples %}<pre style="white-space:pre-wrap;font:inherit;border:1px solid #eee;padding:8px">{{ e }}</pre>{% endfor %}</div>{% endif %}
<div class="card-b"><button class="btn" name="action" value="save">Save changes</button> <button class="btn" name="action" value="samples">Write sample emails</button>
<button class="btn brand" name="action" value="approve" onclick="return confirm('Send this setup to the bot? The campaign starts in {{ s.proposal.autonomy }} mode.')">✓ Approve and send to the bot</button></div></div></form>
<div class="card"><div class="card-h"><h2>Summary for the client</h2><span class="muted" style="margin-left:8px">copy and send it to confirm what was agreed</span></div><div class="card-b"><textarea rows="14" readonly>{{ summary }}</textarea></div></div>
{% endblock %}"""

for _k in list(TEMPLATES):
    TEMPLATES[_k] = TEMPLATES[_k].replace("{% block body %}", "{% block body %}" + GRID +
                                          "{% if request.args.get('msg') %}<div class=\"{{ 'err' if request.args.get('err') else 'note' }}\">"
                                          "{{ request.args.get('msg') }}</div>{% endif %}", 1)
