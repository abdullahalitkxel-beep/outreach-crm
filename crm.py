#!/usr/bin/env python3
"""
BSO: a Salesforce-inspired CRM on the same database as bot.py.

  python crm.py                                       # open http://127.0.0.1:5000
  CRM_PASSWORD=secret python crm.py --host 0.0.0.0    # on a server (password required)
"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, abort, g, redirect, request, url_for
from jinja2 import DictLoader
import brand
from bot import find_avoid, template_avoid_hits

from bot import (DATA, HUMAN_STAGES, env, LEAD_OPEN_STAGES, OPP_OPEN_STAGES, OPP_STAGES, ROOT, STAGES, settings_path,
                 add_lead, add_task, cancel_drafts, db_connect, import_rows, iso, load_config,
                 log_event, now_utc, read_rows, regenerate_draft, set_stage, suppress)
from crm_templates import TEMPLATES

CONFIG_PATH = os.getenv("BOT_CONFIG", str(DATA / "config.yaml"))
app = Flask(__name__)
app.secret_key = os.getenv("CRM_SECRET", "change-me")
app.jinja_loader = DictLoader(TEMPLATES)
app.jinja_env.globals.update(logo=brand.logo_svg, logo_css=brand.MARK_CSS)

STATUS_LABELS = {
    "active": "In sequence", "paused": "Paused", "done": "Sequence finished", "replied": "Replied",
    "not_interested": "Not interested", "unsubscribed": "Unsubscribed", "bounced": "Bounced",
}
JOB_LABELS = {"run": "Run bot (draft + send approved + check replies)", "check": "Check replies",
              "apollo-preview": "Preview Apollo searches (free)", "apollo-pull": "Find leads in Apollo"}

_cfg_cache = {"mtime": None, "cfg": None}


def cfg():
    """Config, reloaded automatically when config.yaml or settings.yaml changes."""
    sp = settings_path(CONFIG_PATH)
    m = (os.path.getmtime(CONFIG_PATH), os.path.getmtime(sp) if sp.exists() else 0)
    if m != _cfg_cache["mtime"]:
        _cfg_cache.update(mtime=m, cfg=load_config(CONFIG_PATH))
    return _cfg_cache["cfg"]


def db():
    if "con" not in g:
        g.con = db_connect(cfg().get("database", "outreach.db"))
    return g.con


@app.teardown_appcontext
def _close(_exc):
    con = g.pop("con", None)
    if con:
        con.close()


def rows(sql, *params):
    return [dict(r) for r in db().execute(sql, params)]


def one(sql, *params):
    r = db().execute(sql, params).fetchone()
    return (r[0] if r and r[0] is not None else 0) if r is not None else 0


@app.before_request
def require_password():
    pw = os.getenv("CRM_PASSWORD")
    if pw:
        auth = request.authorization
        if not auth or auth.password != pw:
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="CRM"'})


LAST_SEEN = None  # last time an app window pinged /heartbeat


@app.post("/heartbeat")
def heartbeat():
    global LAST_SEEN
    import time as _t
    LAST_SEEN = _t.time()
    return ("", 204)


def job_running():
    con = db_connect(cfg().get("database", "outreach.db"))
    try:
        return con.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone() is not None
    finally:
        con.close()


OPEN_ENDPOINTS = {"welcome", "accept_terms", "legal_doc", "about", "heartbeat", "quit_app", "static", "app_icon"}


@app.route("/app-icon.png")
def app_icon():
    from flask import send_file
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return send_file(here / "assets" / "icon.png", mimetype="image/png", max_age=86400)


def terms_accepted():
    acc = (cfg().get("legal_acceptance") or {})
    return str(acc.get("version")) == str((cfg().get("legal") or {}).get("version", "1.0"))


@app.before_request
def require_terms():
    if request.endpoint and request.endpoint not in OPEN_ENDPOINTS and not terms_accepted():
        return redirect(url_for("welcome"))


def today():
    return date.today().isoformat()


def money(v):
    return f"{cfg().get('currency', '$')}{(v or 0):,.0f}"


def test_redirect():
    tm = cfg().get("test_mode") or {}
    return tm.get("redirect_to") if tm.get("enabled") else None


def legal_docs_version():
    import legal
    return legal.app_version()


def render(name, **ctx):
    tm = cfg().get("test_mode") or {}
    review_count = one("SELECT COUNT(*) FROM outbox WHERE status='pending'")
    overdue = one("SELECT COUNT(*) FROM tasks WHERE status='open' AND due_date<=?", today())
    nav = [("home", "Home", None), ("leads", "Leads", None), ("review", "Review", review_count or None),
           ("accounts", "Accounts", None), ("opportunities", "Opportunities", None),
           ("tasks", "Tasks", overdue or None), ("reports", "Reports", None), ("setup", "Getting Started", None), ("settings", "Settings", None)]
    active = {"lead": "leads", "new_lead": "leads", "import_page": "leads", "account": "accounts",
              "opp": "opportunities", "job": "setup", "jobs": "setup"}.get(request.endpoint, request.endpoint)
    return app.jinja_env.get_template(name).render(
        request=request, url_for=url_for, money=money, today=today(), status_labels=STATUS_LABELS,
        stages=STAGES, nav_items=nav, active=active, review_count=review_count, job_labels=JOB_LABELS,
        test_redirect=test_redirect(), test_mode_broken=tm.get("enabled") and not tm.get("redirect_to"),
        currency=cfg().get("currency", "$"), app_version=legal_docs_version(), publisher=cfg().get("publisher", "BlacSwanken"),
        qs=lambda **kw: url_for(request.endpoint, **{**(request.view_args or {}), **request.args.to_dict(), **kw}),
        **ctx)


def back(default, **kw):
    nxt = request.form.get("next")
    if nxt and nxt.startswith("/"):
        sep = "&" if "?" in nxt else "?"
        return redirect(nxt + (sep + "msg=" + kw["msg"] if kw.get("msg") else ""))
    return redirect(url_for(default, **kw))


# ---------------------------------------------------------------- lead helpers

def lead_view(r):
    """Flatten a leads row + its JSON extras for templates."""
    d = dict(r)
    x = json.loads(d.get("extra") or "{}")
    d["x"] = x
    d["name"] = " ".join(p for p in (d.get("first_name"), x.get("last_name")) if p) or d["email"]
    d["title"] = x.get("title", "")
    d["fit"] = x.get("fit", "")
    d["source"] = x.get("source", "")
    return d


def get_lead(lead_id):
    r = db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not r:
        abort(404)
    return r


def stop_sequence(con, lead_id, why):
    cur = con.execute("UPDATE leads SET status='paused' WHERE id=? AND status='active'", (lead_id,))
    cancel_drafts(con, lead_id)
    if cur.rowcount:
        log_event(con, lead_id, "sequence", f"paused ({why})")


def timeline_for(lead_id):
    items = []
    icons = {"note": ("note", "N"), "call": ("call", "☎"), "meeting": ("meeting", "M"), "email": ("email", "✉")}
    for n in rows("SELECT * FROM notes WHERE lead_id=?", lead_id):
        icon, letter = icons.get(n.get("kind") or "note", icons["note"])
        title = {"note": "Note", "call": "Call logged", "meeting": "Meeting logged", "email": "Reply received"}.get(n.get("kind") or "note", "Note")
        items.append({"at": n["at"], "icon": icon, "letter": letter, "title": title, "body": n["body"]})
    for e in rows("SELECT * FROM events WHERE lead_id=?", lead_id):
        items.append({"at": e["at"], **event_text(e)})
    for t in rows("SELECT * FROM tasks WHERE lead_id=? AND status='done'", lead_id):
        items.append({"at": t["done_at"] or t["created_at"], "icon": "task", "letter": "✓",
                      "title": f"Task completed: {t['subject']}", "body": ""})
    return sorted(items, key=lambda i: i["at"] or "", reverse=True)


def event_text(e):
    t, d = e["type"], e["detail"] or ""
    m = {
        "sent": ("email", "✉", "Test email sent to your inbox (" + d.replace(" (test)", "") + ")" if "(test)" in d else f"Email sent ({d})"),
        "drafted": ("email", "✎", f"Draft written ({d}), awaiting review"),
        "approved": ("email", "✓", f"Draft approved ({d})"),
        "rejected": ("sys", "×", f"Draft rejected ({d})"),
        "reply": ("email", "↩", f"Reply detected: {d.replace('_', ' ')}"),
        "bounce": ("sys", "!", "Email bounced"),
        "stage": ("sys", "→", f"Stage changed to {d.title()}"),
        "sequence": ("sys", "⏸", f"Email sequence {d}"),
        "error": ("sys", "!", f"Send error: {d}"),
        "converted": ("sys", "★", d),
        "opp_stage": ("sys", "→", f"Opportunity moved to {d}"),
    }.get(t, ("sys", "•", f"{t} {d}"))
    return {"icon": m[0], "letter": m[1], "title": m[2], "body": "", "text": m[2]}


# ---------------------------------------------------------------- home

@app.route("/")
def home():
    contacted = one("SELECT COUNT(DISTINCT lead_id) FROM events WHERE type='sent'")
    replied = one("SELECT COUNT(DISTINCT lead_id) FROM events WHERE type='reply' AND detail!='out_of_office'")
    open_list = "','".join(OPP_OPEN_STAGES)
    k = {"leads": one("SELECT COUNT(*) FROM leads"), "contacted": contacted,
         "reply_rate": f"{replied / contacted:.0%}" if contacted else "–",
         "open_opps": one(f"SELECT COUNT(*) FROM opportunities WHERE stage IN ('{open_list}')"),
         "pipeline": one(f"SELECT SUM(amount) FROM opportunities WHERE stage IN ('{open_list}')"),
         "won": one("SELECT SUM(amount) FROM opportunities WHERE stage='Closed Won'")}
    tasks = rows("SELECT t.*, l.first_name AS lead_name FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id "
                 "WHERE t.status='open' ORDER BY t.due_date LIMIT 8")
    hot = [lead_view(r) for r in rows("SELECT * FROM leads WHERE stage='replied' ORDER BY id DESC LIMIT 8")]
    activity = []
    for e in rows("SELECT e.*, l.first_name, l.email FROM events e JOIN leads l ON l.id=e.lead_id "
                  "WHERE e.type IN ('sent','reply','bounce','converted','approved') ORDER BY e.id DESC LIMIT 12"):
        activity.append({**event_text(e), "at": e["at"], "lead_id": e["lead_id"], "name": e["first_name"] or e["email"]})
    last_job = (rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 1") or [None])[0]
    steps = setup_steps()
    return render("home.html", k=k, tasks=tasks, hot=hot, activity=activity, last_job=last_job,
                  setup_done=sum(s["ok"] for s in steps), setup_total=len(steps))


# ---------------------------------------------------------------- leads

SORTS = {"name": "first_name", "company": "company", "stage": "stage", "status": "status",
         "created": "id", "title": "json_extract(extra,'$.title')", "fit": "json_extract(extra,'$.fit')",
         "source": "json_extract(extra,'$.source')"}


@app.route("/leads")
def leads():
    a = request.args
    where, params = [], []
    if a.get("q"):
        where.append("(email LIKE ? OR first_name LIKE ? OR company LIKE ? OR extra LIKE ?)")
        params += [f"%{a['q']}%"] * 4
    for field in ("stage", "status"):
        if a.get(field):
            where.append(f"{field}=?")
            params.append(a[field])
    if a.get("fit"):
        where.append("json_extract(extra,'$.fit')=?")
        params.append(a["fit"])
    if a.get("source"):
        where.append("json_extract(extra,'$.source')=?")
        params.append(a["source"])
    sort = a.get("sort", "created") if a.get("sort") in SORTS else "created"
    direction = "asc" if a.get("dir") == "asc" else "desc"
    sql = "SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "")
    data = [lead_view(r) for r in rows(sql + f" ORDER BY {SORTS[sort]} {direction} LIMIT 500", *params)]
    view = a.get("view", "list")
    cols = {s: [l for l in data if l["stage"] == s] for s in STAGES}
    sources = [r["s"] for r in rows("SELECT DISTINCT json_extract(extra,'$.source') s FROM leads WHERE s IS NOT NULL ORDER BY s")]
    view_name = {"replied": "Replied leads", "new": "New leads"}.get(a.get("stage"), "All leads")
    return render("leads.html", rows=data, cols=cols, view=view, sort=sort, dir=direction,
                  sources=sources, view_name=view_name)


@app.post("/leads/bulk")
def leads_bulk():
    ids = [int(i) for i in request.form.getlist("ids")]
    action = request.form.get("action", "")
    if not ids or not action:
        return redirect(url_for("leads", msg="Select at least one lead and an action.", err=1))
    con = db()
    for lid in ids:
        l = get_lead(lid)
        if action.startswith("stage:") and action[6:] in STAGES:
            set_stage(con, lid, action[6:], auto=False)
            if action[6:] in HUMAN_STAGES:
                stop_sequence(con, lid, "stage changed")
        elif action == "pause":
            stop_sequence(con, lid, "bulk action")
        elif action == "resume" and l["status"] in ("paused", "done"):
            con.execute("UPDATE leads SET status='active', next_send_at=COALESCE(next_send_at, ?) WHERE id=?",
                        (iso(now_utc()), lid))
            log_event(con, lid, "sequence", "resumed")
        elif action == "suppress":
            con.execute("UPDATE leads SET status='unsubscribed' WHERE id=?", (lid,))
            cancel_drafts(con, lid)
            suppress(con, l["email"], "manual")
            set_stage(con, lid, "unqualified", auto=False)
        elif action == "delete":
            erase_lead(con, lid, keep_blocked=True)
    con.commit()
    return redirect(url_for("leads", msg=f"Updated {len(ids)} lead(s)."))


@app.route("/lead/<int:lead_id>")
def lead(lead_id):
    l = lead_view(get_lead(lead_id))
    st = l["stage"]
    if st == "converted":
        closed_label, closed_cls = "✓ Converted", "won"
    elif st == "unqualified":
        closed_label, closed_cls = "Unqualified", "lost"
    else:
        closed_label, closed_cls = "Converted", ""
    next_stage = None
    if st in LEAD_OPEN_STAGES and LEAD_OPEN_STAGES.index(st) < len(LEAD_OPEN_STAGES) - 1:
        next_stage = LEAD_OPEN_STAGES[LEAD_OPEN_STAGES.index(st) + 1]
    return render(
        "lead.html", l=l, x=l["x"], n_steps=len(cfg()["sequence"]), lead_open=LEAD_OPEN_STAGES,
        closed_label=closed_label, closed_cls=closed_cls, next_stage=next_stage,
        account=(rows("SELECT * FROM accounts WHERE id=?", l["account_id"]) or [None])[0] if l.get("account_id") else None,
        emails=rows("SELECT * FROM outbox WHERE lead_id=? AND status!='cancelled' ORDER BY id", lead_id),
        pending_draft=(rows("SELECT id FROM outbox WHERE lead_id=? AND status='pending'", lead_id) or [None])[0],
        opps=rows("SELECT * FROM opportunities WHERE lead_id=?", lead_id),
        open_tasks=rows("SELECT * FROM tasks WHERE lead_id=? AND status='open' ORDER BY due_date", lead_id),
        timeline=timeline_for(lead_id), opp_open=OPP_OPEN_STAGES,
        default_close=(date.today() + timedelta(days=30)).isoformat())


@app.post("/lead/<int:lead_id>/update")
def update_lead(lead_id):
    l = get_lead(lead_id)
    f = request.form
    extra = json.loads(l["extra"] or "{}")
    for key, val in f.items():
        if key.startswith("x_"):
            k = key[2:]
            if val.strip():
                extra[k] = val.strip()
            else:
                extra.pop(k, None)
    if "title" in extra:
        from bot import fit_grade
        extra["fit"] = fit_grade(extra["title"])
    db().execute("UPDATE leads SET first_name=?, company=?, website=?, extra=? WHERE id=?",
                 (f.get("first_name"), f.get("company"), f.get("website"), json.dumps(extra), lead_id))
    db().commit()
    return redirect(url_for("lead", lead_id=lead_id, msg="Saved."))


@app.post("/lead/<int:lead_id>/stage")
def lead_stage(lead_id):
    get_lead(lead_id)
    stage = request.form.get("stage")
    if stage not in STAGES or stage == "converted":
        return redirect(url_for("lead", lead_id=lead_id, _anchor="close"))
    con = db()
    set_stage(con, lead_id, stage, auto=False)
    if stage in HUMAN_STAGES:
        stop_sequence(con, lead_id, "stage changed")
    con.commit()
    msg = f"Stage set to {stage.title()}." + (" Automated emails stopped." if stage in HUMAN_STAGES else "")
    return redirect(url_for("lead", lead_id=lead_id, msg=msg))


@app.post("/lead/<int:lead_id>/convert")
def convert_lead(lead_id):
    l = lead_view(get_lead(lead_id))
    f = request.form
    con = db()
    name = f.get("account", "").strip() or l["company"] or l["email"].split("@")[1]
    domain = l.get("website") or l["email"].split("@")[1]
    acc = con.execute("SELECT id FROM accounts WHERE lower(domain)=lower(?) OR lower(name)=lower(?)",
                      (domain, name)).fetchone()
    if acc:
        account_id = acc["id"]
    else:
        account_id = con.execute(
            "INSERT INTO accounts(name, domain, industry, country, created_at) VALUES (?,?,?,?,?)",
            (name, domain, l["x"].get("industry"), l["x"].get("country"), iso(now_utc()))).lastrowid
    try:
        amount = float(f.get("amount") or 0)
    except ValueError:
        amount = 0
    stage = f.get("stage") if f.get("stage") in OPP_OPEN_STAGES else OPP_OPEN_STAGES[0]
    opp_id = con.execute(
        "INSERT INTO opportunities(name, account_id, lead_id, stage, amount, close_date, next_step, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (f.get("opp_name") or f"{name} – engineering team", account_id, lead_id, stage, amount,
         f.get("close_date") or None, f.get("next_step") or None, iso(now_utc()))).lastrowid
    con.execute("UPDATE leads SET account_id=? WHERE id=?", (account_id, lead_id))
    set_stage(con, lead_id, "converted", auto=False)
    stop_sequence(con, lead_id, "converted")
    log_event(con, lead_id, "converted", f"Converted to opportunity “{f.get('opp_name') or name}”")
    if f.get("next_step"):
        add_task(con, f["next_step"], lead_id=lead_id, opp_id=opp_id)
    con.commit()
    return redirect(url_for("opp", opp_id=opp_id, msg="Lead converted. Account and opportunity created."))


@app.post("/lead/<int:lead_id>/note")
def add_note(lead_id):
    get_lead(lead_id)
    body = request.form.get("body", "").strip()
    kind = request.form.get("kind") if request.form.get("kind") in ("note", "call", "meeting") else "note"
    if body:
        db().execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?,?,?,?)", (lead_id, body, iso(now_utc()), kind))
        db().commit()
    return back("lead", lead_id=lead_id, msg={"note": "Note saved.", "call": "Call logged.", "meeting": "Meeting logged."}[kind])


@app.post("/lead/<int:lead_id>/task")
def add_task_route(lead_id):
    get_lead(lead_id)
    subject = request.form.get("subject", "").strip()
    if subject:
        opp_id = request.form.get("opp_id") or None
        add_task(db(), subject, request.form.get("due") or None, lead_id=lead_id, opp_id=opp_id)
        db().commit()
    return back("lead", lead_id=lead_id, msg="Task created.")


@app.post("/lead/<int:lead_id>/sequence")
def sequence_action(lead_id):
    l = get_lead(lead_id)
    con = db()
    action = request.form.get("action")
    if action == "pause":
        stop_sequence(con, lead_id, "by you")
        msg = "Emails paused."
    elif action == "resume":
        if l["step"] >= len(cfg()["sequence"]):
            return redirect(url_for("lead", lead_id=lead_id, msg="All emails in the sequence were already sent."))
        con.execute("UPDATE leads SET status='active', next_send_at=COALESCE(next_send_at, ?) WHERE id=?",
                    (iso(now_utc()), lead_id))
        log_event(con, lead_id, "sequence", "resumed")
        msg = "Emails resumed."
    elif action == "suppress":
        con.execute("UPDATE leads SET status='unsubscribed' WHERE id=?", (lead_id,))
        cancel_drafts(con, lead_id)
        suppress(con, l["email"], "manual")
        set_stage(con, lead_id, "unqualified", auto=False)
        msg = "Marked do-not-contact."
    else:
        abort(400)
    con.commit()
    return redirect(url_for("lead", lead_id=lead_id, msg=msg))


@app.route("/new", methods=["GET", "POST"])
def new_lead():
    if request.method == "GET":
        return render("new.html")
    f = request.form
    why = []
    row = {k: f.get(k, "") for k in ("email", "first_name", "last_name", "title", "company", "website", "linkedin", "country")}
    row["source"] = "Manual"
    lead_id = add_lead(db(), row, "active" if f.get("sequence") else "paused", why)
    if not lead_id:
        return redirect(url_for("new_lead", msg=f"Not added: {why[0] if why else 'invalid'}.", err=1))
    db().commit()
    return redirect(url_for("lead", lead_id=lead_id, msg="Lead created."))


@app.route("/import", methods=["GET", "POST"])
def import_page():
    if request.method == "GET":
        return render("import.html", result=None)
    file = request.files.get("file")
    if not file or not file.filename:
        return redirect(url_for("import_page", msg="Choose a file first.", err=1))
    data = file.read()
    if file.filename.lower().endswith((".xlsx", ".xlsm")):
        src = io.BytesIO(data)
    else:
        src = io.StringIO(data.decode("utf-8-sig", "replace"))
    try:
        added, skipped = import_rows(db(), read_rows(src, file.filename),
                                     "active" if request.form.get("sequence") else "paused", source=file.filename)
    except Exception as e:
        return redirect(url_for("import_page", msg=f"Couldn't read that file: {e}", err=1))
    return render("import.html", result={"added": added, "skipped": skipped})


@app.route("/export.csv")
def export():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["email", "first_name", "last_name", "title", "company", "website", "fit", "stage",
                "email_status", "step", "source", "created_at"])
    for r in rows("SELECT * FROM leads ORDER BY id"):
        l = lead_view(r)
        w.writerow([l["email"], l["first_name"], l["x"].get("last_name", ""), l["title"], l["company"],
                    l["website"], l["fit"], l["stage"], l["status"], l["step"], l["source"], l["created_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


# ---------------------------------------------------------------- accounts & opportunities

@app.route("/accounts")
def accounts():
    open_list = "','".join(OPP_OPEN_STAGES)
    data = rows(f"""SELECT a.*,
        (SELECT COUNT(*) FROM leads l WHERE l.account_id=a.id) contacts,
        (SELECT COUNT(*) FROM opportunities o WHERE o.account_id=a.id AND o.stage IN ('{open_list}')) open_opps,
        (SELECT SUM(amount) FROM opportunities o WHERE o.account_id=a.id AND o.stage IN ('{open_list}')) pipeline,
        (SELECT SUM(amount) FROM opportunities o WHERE o.account_id=a.id AND o.stage='Closed Won') won
        FROM accounts a ORDER BY a.name""")
    return render("accounts.html", rows=data)


@app.route("/account/<int:account_id>")
def account(account_id):
    a = (rows("SELECT * FROM accounts WHERE id=?", account_id) or [None])[0]
    if not a:
        abort(404)
    opps = rows("SELECT * FROM opportunities WHERE account_id=? ORDER BY id DESC", account_id)
    pipeline = sum(o["amount"] or 0 for o in opps if o["stage"] in OPP_OPEN_STAGES)
    contacts = [lead_view(r) for r in rows("SELECT * FROM leads WHERE account_id=?", account_id)]
    return render("account.html", a=a, opps=opps, contacts=contacts, pipeline=pipeline)


@app.post("/account/<int:account_id>/update")
def update_account(account_id):
    f = request.form
    db().execute("UPDATE accounts SET name=?, domain=?, industry=?, country=? WHERE id=?",
                 (f.get("name"), f.get("domain"), f.get("industry"), f.get("country"), account_id))
    db().commit()
    return redirect(url_for("account", account_id=account_id, msg="Saved."))


@app.route("/opportunities")
def opportunities():
    data = rows("SELECT o.*, a.name account FROM opportunities o LEFT JOIN accounts a ON a.id=o.account_id "
                "ORDER BY o.close_date IS NULL, o.close_date")
    all_stages = list(OPP_STAGES)
    cols = {s: [o for o in data if o["stage"] == s] for s in all_stages}
    sums = {s: sum(o["amount"] or 0 for o in cols[s]) for s in all_stages}
    open_ = [o for o in data if o["stage"] in OPP_OPEN_STAGES]
    k = {"open": len(open_), "pipeline": sum(o["amount"] or 0 for o in open_),
         "weighted": sum((o["amount"] or 0) * OPP_STAGES[o["stage"]] / 100 for o in open_),
         "won": sums["Closed Won"]}
    return render("opps.html", rows=data, cols=cols, sums=sums, all_stages=all_stages, k=k,
                  view=request.args.get("view", "kanban"), probs=OPP_STAGES, opp_open=OPP_OPEN_STAGES)


def get_opp(opp_id):
    o = (rows("SELECT * FROM opportunities WHERE id=?", opp_id) or [None])[0]
    if not o:
        abort(404)
    return o


@app.route("/opp/<int:opp_id>")
def opp(opp_id):
    o = get_opp(opp_id)
    st = o["stage"]
    closed_label, closed_cls = {"Closed Won": ("🏆 Closed Won", "won"),
                                "Closed Lost": ("Closed Lost", "lost")}.get(st, ("Closed", ""))
    next_stage = None
    if st in OPP_OPEN_STAGES and OPP_OPEN_STAGES.index(st) < len(OPP_OPEN_STAGES) - 1:
        next_stage = OPP_OPEN_STAGES[OPP_OPEN_STAGES.index(st) + 1]
    lead = lead_view(get_lead(o["lead_id"])) if o["lead_id"] else None
    acc = (rows("SELECT name FROM accounts WHERE id=?", o["account_id"]) or [{"name": ""}])[0]["name"] if o["account_id"] else ""
    return render("opp.html", o=o, lead=lead, lead_name=lead["name"] if lead else "", account_name=acc,
                  opp_open=OPP_OPEN_STAGES, probs=OPP_STAGES, closed_label=closed_label, closed_cls=closed_cls,
                  next_stage=next_stage, timeline=timeline_for(o["lead_id"]) if lead else [],
                  open_tasks=rows("SELECT * FROM tasks WHERE (opp_id=? OR lead_id=?) AND status='open' ORDER BY due_date",
                                  opp_id, o["lead_id"] or -1))


@app.post("/opp/<int:opp_id>/update")
def update_opp(opp_id):
    get_opp(opp_id)
    f = request.form
    try:
        amount = float(f.get("amount") or 0)
    except ValueError:
        amount = 0
    db().execute("UPDATE opportunities SET name=?, amount=?, close_date=?, next_step=? WHERE id=?",
                 (f.get("name"), amount, f.get("close_date") or None, f.get("next_step") or None, opp_id))
    db().commit()
    return redirect(url_for("opp", opp_id=opp_id, msg="Saved."))


@app.post("/opp/<int:opp_id>/stage")
def opp_stage(opp_id):
    o = get_opp(opp_id)
    stage = request.form.get("stage")
    if stage not in OPP_STAGES:
        abort(400)
    closed = stage.startswith("Closed")
    db().execute("UPDATE opportunities SET stage=?, closed_at=? WHERE id=?",
                 (stage, iso(now_utc()) if closed else None, opp_id))
    if o["lead_id"]:
        log_event(db(), o["lead_id"], "opp_stage", stage)
    db().commit()
    msg = "🏆 Deal won. Nice work!" if stage == "Closed Won" else f"Stage set to {stage}."
    return redirect(url_for("opp", opp_id=opp_id, msg=msg))


# ---------------------------------------------------------------- tasks

@app.route("/tasks")
def tasks():
    show = request.args.get("show", "open") if request.args.get("show") in ("open", "done", "overdue") else "open"
    where = {"open": "t.status='open'", "done": "t.status='done'", "overdue": "t.status='open' AND t.due_date<?"}[show]
    params = (today(),) if show == "overdue" else ()
    order = "t.done_at DESC" if show == "done" else "t.due_date"
    data = rows(f"SELECT t.*, l.first_name lead_name, o.name opp_name FROM tasks t "
                f"LEFT JOIN leads l ON l.id=t.lead_id LEFT JOIN opportunities o ON o.id=t.opp_id "
                f"WHERE {where} ORDER BY {order} LIMIT 300", *params)
    return render("tasks.html", rows=data, show=show)


@app.post("/task/<int:task_id>/toggle")
def task_toggle(task_id):
    t = (rows("SELECT * FROM tasks WHERE id=?", task_id) or [None])[0]
    if not t:
        abort(404)
    if t["status"] == "open":
        db().execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (iso(now_utc()), task_id))
        msg = "Task completed."
    else:
        db().execute("UPDATE tasks SET status='open', done_at=NULL WHERE id=?", (task_id,))
        msg = "Task reopened."
    db().commit()
    return back("tasks", msg=msg)


# ---------------------------------------------------------------- reports

@app.route("/reports")
def reports():
    funnel = [(s, one("SELECT COUNT(*) FROM leads WHERE stage=?", s)) for s in STAGES]
    funnel_max = max([n for _, n in funnel] + [1])
    sent = {r["s"]: r["n"] for r in rows(
        "SELECT replace(detail,' (test)','') s, COUNT(*) n FROM events WHERE type='sent' GROUP BY s")}
    replied_after = {r["step"]: r["n"] for r in rows(
        "SELECT step, COUNT(*) n FROM leads WHERE id IN (SELECT lead_id FROM events WHERE type='reply' "
        "AND detail!='out_of_office') GROUP BY step")}
    steps = []
    for i in range(len(cfg()["sequence"])):
        n_sent = sent.get(f"step {i + 1}", 0)
        n_rep = replied_after.get(i + 1, 0)
        steps.append({"label": "First email" if i == 0 else f"Follow-up {i}", "sent": n_sent, "replies": n_rep,
                      "rate": f"{n_rep / n_sent:.0%}" if n_sent else "–"})
    # which template variant gets replies: a reply counts for the last variant that lead received
    perf = {}
    last_variant = {}
    for e in rows("SELECT lead_id, detail, variant FROM events WHERE type='sent' ORDER BY id"):
        step = int(e["detail"].split()[1]) if e["detail"].startswith("step ") else 0
        key = (step, e["variant"] or "(single template)")
        perf.setdefault(key, {"sent": 0, "replies": 0, "positive": 0})["sent"] += 1
        last_variant[e["lead_id"]] = key
    for r in rows("SELECT lead_id, detail FROM events WHERE type='reply' AND detail!='out_of_office' GROUP BY lead_id"):
        if r["lead_id"] in last_variant:
            perf[last_variant[r["lead_id"]]]["replies"] += 1
            if r["detail"] in ("interested", "referral"):
                perf[last_variant[r["lead_id"]]]["positive"] += 1
    templates = [{"step": "First email" if k[0] == 1 else f"Follow-up {k[0] - 1}", "name": k[1], **v,
                  "rate": f"{v['replies'] / v['sent']:.0%}" if v["sent"] else "–"} for k, v in sorted(perf.items())]
    reply_types = [(r["detail"], r["n"]) for r in rows(
        "SELECT detail, COUNT(*) n FROM events WHERE type='reply' GROUP BY detail ORDER BY n DESC")]
    sources = rows("""SELECT COALESCE(json_extract(l.extra,'$.source'),'Manual') src, COUNT(*) n,
        SUM(EXISTS(SELECT 1 FROM events e WHERE e.lead_id=l.id AND e.type='sent')) contacted,
        SUM(EXISTS(SELECT 1 FROM events e WHERE e.lead_id=l.id AND e.type='reply' AND e.detail!='out_of_office')) replied,
        SUM(l.stage='converted') converted FROM leads l GROUP BY src ORDER BY n DESC""")
    for s in sources:
        s["rate"] = f"{s['replied'] / s['contacted']:.0%}" if s["contacted"] else "–"
    pipe = []
    for stage, prob in OPP_STAGES.items():
        amt = one("SELECT SUM(amount) FROM opportunities WHERE stage=?", stage)
        pipe.append({"stage": stage, "prob": prob, "n": one("SELECT COUNT(*) FROM opportunities WHERE stage=?", stage),
                     "amount": amt, "weighted": amt * prob / 100})
    open_rows = [p for p in pipe if p["stage"] in OPP_OPEN_STAGES]
    won = one("SELECT COUNT(*) FROM opportunities WHERE stage='Closed Won'")
    lost = one("SELECT COUNT(*) FROM opportunities WHERE stage='Closed Lost'")
    return render("reports.html", templates=templates, funnel=funnel, funnel_max=funnel_max, steps=steps, reply_types=reply_types,
                  sources=sources, pipe=pipe, pipe_open=sum(p["amount"] for p in open_rows),
                  pipe_weighted=sum(p["weighted"] for p in open_rows),
                  win_rate=f"{won / (won + lost):.0%}" if won + lost else "–")


# ---------------------------------------------------------------- review queue

@app.route("/review")
def review():
    data = []
    for r in rows("SELECT o.*, l.first_name, l.email, l.company, l.website, l.extra FROM outbox o "
                  "JOIN leads l ON l.id=o.lead_id WHERE o.status='pending' ORDER BY o.step DESC, o.id"):
        r["x"] = json.loads(r.pop("extra") or "{}")
        r["name"] = " ".join(p for p in (r["first_name"], r["x"].get("last_name")) if p) or r["email"]
        r["avoid"] = find_avoid(cfg(), r["subject"], r["body"])
        data.append(r)
    return render("review.html", drafts=data, approved=one("SELECT COUNT(*) FROM outbox WHERE status='approved'"),
                  ai_on=bool(env("ANTHROPIC_API_KEY")), lead_count=one("SELECT COUNT(*) FROM leads"))


@app.post("/review/<int:draft_id>")
def review_action(draft_id):
    con = db()
    d = con.execute("SELECT * FROM outbox WHERE id=? AND status='pending'", (draft_id,)).fetchone()
    if not d:
        return redirect(url_for("review", msg="That draft was already handled."))
    action = request.form.get("action")
    subject = request.form.get("subject", d["subject"]).strip() or d["subject"]
    body = request.form.get("body", d["body"]).replace("\r\n", "\n").strip()
    if d["step"] > 0:
        subject = d["subject"]  # follow-ups keep "Re: …" so they thread
    now = iso(now_utc())
    anchor = None
    if action in ("save", "approve"):
        con.execute("UPDATE outbox SET subject=?, body=? WHERE id=?", (subject, body, draft_id))
        if action == "approve":
            con.execute("UPDATE outbox SET status='approved', decided_at=? WHERE id=?", (now, draft_id))
            log_event(con, d["lead_id"], "approved", f"step {d['step'] + 1}")
            msg = "Approved. It sends on the next bot run."
        else:
            msg, anchor = "Edits saved.", f"d{draft_id}"
    elif action == "regenerate":
        con.commit()
        regenerate_draft(cfg(), con, draft_id)
        msg, anchor = "New opener written. Have a look.", f"d{draft_id}"
    elif action == "reject":
        con.execute("UPDATE outbox SET status='rejected', decided_at=? WHERE id=?", (now, draft_id))
        con.execute("UPDATE leads SET status='paused' WHERE id=?", (d["lead_id"],))
        log_event(con, d["lead_id"], "rejected", f"step {d['step'] + 1}")
        msg = "Rejected. No emails will go to that lead."
    else:
        abort(400)
    con.commit()
    return redirect(url_for("review", msg=msg, _anchor=anchor))


@app.post("/review/approve-all")
def approve_all():
    con = db()
    ids = [r["id"] for r in rows("SELECT id, lead_id, step FROM outbox WHERE status='pending'")]
    for r in rows("SELECT lead_id, step FROM outbox WHERE status='pending'"):
        log_event(con, r["lead_id"], "approved", f"step {r['step'] + 1}")
    con.execute("UPDATE outbox SET status='approved', decided_at=? WHERE status='pending'", (iso(now_utc()),))
    con.commit()
    return redirect(url_for("review", msg=f"Approved {len(ids)} draft(s). They send on the next bot run."))


# ---------------------------------------------------------------- search

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    leads_ = [{"id": l["id"], "label": l["name"], "sub": " · ".join(p for p in (l["title"], l["company"], l["email"]) if p)}
              for l in map(lead_view, rows("SELECT * FROM leads WHERE email LIKE ? OR first_name LIKE ? OR company LIKE ? OR extra LIKE ? LIMIT 50", like, like, like, like))] if q else []
    accs = [{"id": a["id"], "label": a["name"], "sub": a["domain"] or ""}
            for a in rows("SELECT * FROM accounts WHERE name LIKE ? OR domain LIKE ? LIMIT 50", like, like)] if q else []
    opps = [{"id": o["id"], "label": o["name"], "sub": f"{o['stage']} · {money(o['amount'])}"}
            for o in rows("SELECT * FROM opportunities WHERE name LIKE ? LIMIT 50", like)] if q else []
    groups = [("Leads", "t-lead", "L", leads_, "lead", "lead_id"), ("Accounts", "t-acc", "A", accs, "account", "account_id"),
              ("Opportunities", "t-opp", "O", opps, "opp", "opp_id")]
    return render("search.html", q=q, groups=groups)


# ---------------------------------------------------------------- bot runner (no terminal needed)

def bot_argv(cmd, max_leads=None, force=False):
    """Command line for a bot run. In the packaged app the .exe runs itself with --bot."""
    base = [sys.executable, "--bot"] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve().parent / "bot.py")]
    argv = base + ["--config", CONFIG_PATH, cmd]
    if cmd == "run":
        argv += ["--limit", "50"] + (["--force"] if force else [])
    elif cmd == "apollo-pull":
        argv += ["--max", str(max_leads or 10)]
    return argv


def _run_job(job_id, argv):
    """Run a bot command as a separate process, streaming its output (via a log file) into the jobs table."""
    import time as _t
    con = db_connect(load_config(CONFIG_PATH).get("database", "outreach.db"))
    logdir = DATA / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / f"run-{job_id}.log"
    log.write_text("", encoding="utf-8")
    status = "failed"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows
        p = subprocess.Popen(argv, cwd=DATA, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags,
                             env={**os.environ, "PYTHONUNBUFFERED": "1", "OUTREACH_JOB_LOG": str(log)})
        while True:
            done = p.poll() is not None
            con.execute("UPDATE jobs SET output=? WHERE id=?",
                        (log.read_text(encoding="utf-8", errors="replace")[-20000:], job_id))
            con.commit()
            if done:
                break
            _t.sleep(0.7)
        status = "ok" if p.returncode == 0 else "failed"
    except Exception as e:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n{e}")
    out = log.read_text(encoding="utf-8", errors="replace")[-20000:]
    con.execute("UPDATE jobs SET status=?, output=?, finished_at=? WHERE id=?",
                (status, out or "(no output)", iso(now_utc()), job_id))
    con.commit()
    con.close()


def start_job(con, cmd, max_leads=None):
    """Start a bot run in the background. Returns (job_id, already_running)."""
    running = con.execute("SELECT id FROM jobs WHERE status='running'").fetchone()
    if running:
        return running["id"], True
    c = cfg()
    tm = c.get("test_mode") or {}
    argv = bot_argv(cmd, max_leads, force=bool(tm.get("enabled") and tm.get("redirect_to")))
    job_id = con.execute("INSERT INTO jobs(cmd, args, status, output, started_at) VALUES (?,?,?,?,?)",
                         (cmd, " ".join(argv[-4:]), "running", "", iso(now_utc()))).lastrowid
    con.commit()
    threading.Thread(target=_run_job, args=(job_id, argv), daemon=True).start()
    return job_id, False


@app.post("/jobs/start")
def job_start():
    cmd = request.form.get("cmd")
    if cmd not in JOB_LABELS:
        abort(400)
    try:
        n = max(1, min(int(request.form.get("max") or 10), 100))
    except ValueError:
        n = 10
    job_id, busy = start_job(db(), cmd, n)
    return redirect(url_for("job", job_id=job_id, msg="A run is already in progress." if busy else None))


def _autorun_loop():
    """While the app is open, run the bot on a timer if switched on in Settings."""
    import time as _t
    last = 0.0
    while True:
        _t.sleep(30)
        try:
            c = load_config(CONFIG_PATH)
            minutes = int((c.get("autorun") or {}).get("every_minutes") or 0)
            if (c.get("autorun") or {}).get("enabled") and minutes and _t.time() - last >= minutes * 60:
                con = db_connect(c.get("database", "outreach.db"))
                with app.app_context():
                    start_job(con, "run")
                con.close()
                last = _t.time()
        except Exception as e:  # never let the timer die
            print("autorun:", e)


@app.route("/jobs/<int:job_id>")
def job(job_id):
    j = (rows("SELECT * FROM jobs WHERE id=?", job_id) or [None])[0]
    if not j:
        abort(404)
    after = {"run": [("review", "Open review queue"), ("home", "Home")],
             "check": [("home", "Home"), ("tasks", "Tasks")],
             "apollo-preview": [("setup", "Back to Getting Started")],
             "apollo-pull": [("leads", "View leads"), ("setup", "Back to Getting Started")]}[j["cmd"]]
    problems = [l[len("PROBLEM:"):].strip() for l in (j["output"] or "").splitlines() if l.startswith("PROBLEM:")]
    if j["status"] == "failed" and not problems:
        last = [l for l in (j["output"] or "").splitlines() if l.strip()]
        problems = [last[-1][:300]] if last else ["The run stopped unexpectedly."]
    return render("job.html", j=j, after_links=after, problems=problems)


@app.route("/jobs")
def jobs():
    return render("jobs.html", rows=rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 100"))


# ---------------------------------------------------------------- legal, about, privacy tools

import legal as legal_docs


@app.route("/welcome")
def welcome():
    return render("welcome.html", accepted=terms_accepted(), lg=legal_docs.values(cfg()))


@app.post("/legal/accept")
def accept_terms():
    if not request.form.get("agree"):
        return redirect(url_for("welcome", msg="Please tick the box to accept the terms.", err=1))
    write_settings({"legal_acceptance": {"version": str((cfg().get("legal") or {}).get("version", "1.0")),
                                         "at": iso(now_utc()), "app_version": legal_docs.app_version()}})
    return redirect(url_for("setup", msg="Welcome! Let's get you set up."))


@app.route("/legal/<doc>")
def legal_doc(doc):
    if doc == "third-party":
        return render("legal.html", title="Third-Party Notices", body=None, text=legal_docs.third_party_text())
    if doc not in legal_docs.DOCS:
        abort(404)
    return render("legal.html", title=legal_docs.DOCS[doc][0],
                  body=legal_docs.to_html(legal_docs.source(doc, cfg())), text=None)


@app.route("/about")
def about():
    from secretstore import ENCRYPTED
    acc = cfg().get("legal_acceptance") or {}
    return render("about.html", version=legal_docs.app_version(),
                  lg=legal_docs.values(cfg()), data_dir=str(DATA), encrypted=ENCRYPTED, accepted_at=acc.get("at"))


@app.post("/about/open-data-folder")
def open_data_folder():
    if sys.platform == "win32":
        os.startfile(str(DATA))  # noqa
    return redirect(url_for("about"))


def erase_lead(con, lead_id, keep_blocked):
    l = get_lead(lead_id)
    if keep_blocked:
        suppress(con, l["email"], "deleted on request")
    for table, col in (("notes", "lead_id"), ("events", "lead_id"), ("outbox", "lead_id"), ("tasks", "lead_id")):
        con.execute(f"DELETE FROM {table} WHERE {col}=?", (lead_id,))
    con.execute("UPDATE opportunities SET lead_id=NULL WHERE lead_id=?", (lead_id,))
    con.execute("DELETE FROM leads WHERE id=?", (lead_id,))


@app.post("/lead/<int:lead_id>/delete")
def delete_lead(lead_id):
    erase_lead(db(), lead_id, bool(request.form.get("keep_blocked")))
    db().commit()
    return redirect(url_for("leads", msg="Lead and all their history deleted."))


# ---------------------------------------------------------------- sample data (try the app without real contacts)

SAMPLE_LEADS = [
    {"email": "maya.chen@example.com", "first_name": "Maya", "last_name": "Chen", "title": "CTO", "company": "Northwind Health", "website": "example.com", "industry": "Healthcare", "country": "United States"},
    {"email": "omar.haddad@example.org", "first_name": "Omar", "last_name": "Haddad", "title": "VP Engineering", "company": "Bluefin Payments", "website": "example.org", "industry": "Financial Services", "country": "United Arab Emirates"},
    {"email": "lena.fischer@example.net", "first_name": "Lena", "last_name": "Fischer", "title": "Head of Product", "company": "Cartwise Retail", "website": "example.net", "industry": "Retail", "country": "United Kingdom"},
    {"email": "sam.okafor@example.com", "first_name": "Sam", "last_name": "Okafor", "title": "Founder & CEO", "company": "Routely Logistics", "website": "example.com", "industry": "Logistics", "country": "United States"},
    {"email": "priya.raman@example.org", "first_name": "Priya", "last_name": "Raman", "title": "Director of Engineering", "company": "Keystone Realty", "website": "example.org", "industry": "Real Estate", "country": "United States"},
    {"email": "tom.berg@example.net", "first_name": "Tom", "last_name": "Berg", "title": "Office Manager", "company": "Example Clubs", "website": "example.net", "industry": "Leisure", "country": "United Kingdom"},
]
SAMPLE_SOURCE = "Sample data"


@app.post("/sample/load")
def sample_load():
    added, _ = import_rows(db(), [dict(r) for r in SAMPLE_LEADS], "active", source=SAMPLE_SOURCE)
    return redirect(url_for("leads", msg=f"Added {added} sample leads (fictional people at example.com addresses). Remove them any time from Getting Started."))


@app.post("/sample/remove")
def sample_remove():
    ids = [r["id"] for r in rows("SELECT id FROM leads WHERE json_extract(extra,'$.source')=?", SAMPLE_SOURCE)]
    for lid in ids:
        erase_lead(db(), lid, keep_blocked=False)
    db().commit()
    return redirect(url_for("setup", msg=f"Removed {len(ids)} sample leads."))


# ---------------------------------------------------------------- settings (no file editing needed)

import imaplib
import smtplib

import yaml

PROVIDERS = {
    "gmail": {"label": "Gmail / Google Workspace", "smtp_host": "smtp.gmail.com", "smtp_port": 587,
              "imap_host": "imap.gmail.com", "help": "Use an App password: Google Account → Security → 2-Step Verification → App passwords. Also turn on IMAP in Gmail settings → Forwarding and POP/IMAP."},
    "outlook": {"label": "Outlook / Microsoft 365", "smtp_host": "smtp.office365.com", "smtp_port": 587,
                "imap_host": "outlook.office365.com", "help": "Your Microsoft 365 admin may need to turn on “Authenticated SMTP” for this mailbox. Use an app password if MFA is on."},
    "zoho": {"label": "Zoho Mail", "smtp_host": "smtp.zoho.com", "smtp_port": 587, "imap_host": "imap.zoho.com",
             "help": "Turn on IMAP access in Zoho Mail settings and create an application-specific password."},
    "other": {"label": "Other (enter server details)", "smtp_host": "", "smtp_port": 587, "imap_host": "",
              "help": "Your email provider's help pages list the SMTP (sending) and IMAP (receiving) server names."},
}
TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London",
             "Europe/Berlin", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Australia/Sydney"]
REGIONS = ["United States", "United Kingdom", "United Arab Emirates", "Canada", "Germany", "Netherlands",
           "Saudi Arabia", "Singapore", "Australia"]
SIZES = [("1,50", "1–50"), ("51,200", "51–200"), ("201,500", "201–500"), ("501,1000", "501–1,000"),
         ("1001,5000", "1,001–5,000"), ("5001,10000", "5,001–10,000"), ("10001,1000000", "10,000+")]
SENIORITIES = [("c_suite", "C-suite"), ("vp", "VP"), ("head", "Head of"), ("director", "Director"),
               ("founder", "Founder"), ("owner", "Owner"), ("manager", "Manager")]


def read_settings():
    p = settings_path(CONFIG_PATH)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def write_settings(update):
    data = read_settings()
    data.update(update)
    with open(settings_path(CONFIG_PATH), "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def set_secret(key, value):
    """Save a secret encrypted (Windows DPAPI) and apply it immediately (no restart needed)."""
    from bot import SECRETS
    SECRETS.set(key, value)


def mask(v):
    return ("•" * 8 + v[-4:]) if v and len(v) > 6 else ("set" if v else "")


def guess_provider(s):
    host = (s.get("smtp_host") or "").lower()
    for k, p in PROVIDERS.items():
        if k != "other" and p["smtp_host"] == host:
            return k
    return "other" if host and "example" not in host else "gmail"


def test_inbox(s, password):
    """Try logging in to SMTP and IMAP. Returns (ok, message)."""
    if not (s.get("email") and s.get("smtp_host") and password):
        return False, "Fill in the email address, servers and app password first."
    user = s.get("username") or s["email"]
    try:
        port = int(s.get("smtp_port") or 587)
        if port == 465:
            srv = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=15)
        else:
            srv = smtplib.SMTP(s["smtp_host"], port, timeout=15)
            srv.starttls()
        srv.login(user, password)
        srv.quit()
    except smtplib.SMTPAuthenticationError:
        return False, "Sending server rejected the login. Check the address and app password (a normal password usually won't work)."
    except Exception as e:
        return False, f"Couldn't reach the sending server ({s['smtp_host']}): {e}"
    try:
        im = imaplib.IMAP4_SSL(s["imap_host"], int(s.get("imap_port") or 993), timeout=15)
        im.login(user, password)
        im.logout()
    except imaplib.IMAP4.error:
        return False, "Sending works, but the receiving (IMAP) login failed. Make sure IMAP is turned on for this mailbox."
    except Exception as e:
        return False, f"Sending works, but couldn't reach the receiving server ({s.get('imap_host')}): {e}"
    return True, "Connected. Sending and receiving both work."


def test_anthropic(key):
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(model=cfg()["models"]["personalize"], max_tokens=5,
                                   messages=[{"role": "user", "content": "Reply with OK"}])
        return True, "AI key works."
    except ImportError:
        return False, "The AI library isn't installed."
    except Exception as e:
        return False, f"AI key didn't work: {str(e)[:200]}"


def test_apollo(key):
    try:
        import apollo
        data = apollo.Apollo(key).search({"person_titles": ["CTO"]}, page=1, per_page=1)
        return True, f"Apollo key works ({apollo._total(data):,} CTOs in the database)."
    except Exception as e:
        return False, f"Apollo key didn't work: {str(e)[:200]}"


@app.route("/settings")
def settings():
    c = cfg()
    senders = list(c.get("senders") or [])
    real_n = sum(1 for x in senders if not any(p in (x.get("email") or "") for p in PLACEHOLDERS))
    while len(senders) < min(10, max(3, real_n + 1)):
        senders.append({})
    inboxes = []
    for i, s in enumerate(senders[:10], 1):
        placeholder = any(p in (s.get("email") or "") for p in PLACEHOLDERS)
        inboxes.append({"n": i, "s": {} if placeholder else s, "provider": guess_provider(s),
                        "pw": mask(env(s.get("password_env") or f"SENDER{i}_PASSWORD"))})
    ac = c.get("apollo") or {}
    cf = ac.get("common_filters") or {}
    seq = []
    for st in c.get("sequence") or []:
        vs = st.get("variants") or [{"name": "Variant A", "enabled": True, "subject": st.get("subject", ""), "body": st.get("body", "")}]
        seq.append({"delay_days": st.get("delay_days", 3), "variants": vs})
    return render("settings.html", c=c, inboxes=inboxes, seq=seq, providers=PROVIDERS, timezones=TIMEZONES,
                  regions=REGIONS, sizes=SIZES, seniorities=SENIORITIES, cf=cf, ac=ac,
                  keys={"ANTHROPIC_API_KEY": mask(env("ANTHROPIC_API_KEY")), "APOLLO_API_KEY": mask(env("APOLLO_API_KEY")),
                        "SLACK_WEBHOOK_URL": mask(env("SLACK_WEBHOOK_URL"))},
                  tab=request.args.get("tab", "inbox"), result=request.args.get("result"),
                  ok=request.args.get("ok"), avoid_hits=template_avoid_hits(c), avoid_n=len(c.get("avoid_phrases") or []))


def _inbox_from_form(f, n):
    prov = PROVIDERS.get(f.get(f"provider{n}"), PROVIDERS["other"])
    email_ = f.get(f"email{n}", "").strip()
    return {
        "email": email_, "name": f.get(f"name{n}", "").strip(),
        "smtp_host": (f.get(f"smtp_host{n}") or prov["smtp_host"]).strip(),
        "smtp_port": int(f.get(f"smtp_port{n}") or prov["smtp_port"] or 587),
        "imap_host": (f.get(f"imap_host{n}") or prov["imap_host"]).strip(),
        "password_env": f"SENDER{n}_PASSWORD",
        "daily_limit": max(1, min(int(f.get(f"daily_limit{n}") or 10), 100)),
    }


@app.post("/settings/inbox")
def save_inbox():
    f = request.form
    senders = []
    for n in range(1, 11):
        if f.get(f"provider{n}") is None:
            continue
        s = _inbox_from_form(f, n)
        if f.get(f"password{n}", "").strip():
            set_secret(s["password_env"], f[f"password{n}"].strip())
        if s["email"]:
            if not s["name"]:
                return redirect(url_for("settings", tab="inbox", result=f"Inbox {n}: add the name prospects will see."))
            senders.append(s)
    if not senders:
        return redirect(url_for("settings", tab="inbox", result="Add at least one inbox."))
    test_n = f.get("test")
    write_settings({"senders": senders})
    if test_n:
        s = _inbox_from_form(f, int(test_n))
        ok, msg = test_inbox(s, env(s["password_env"]))
        return redirect(url_for("settings", tab="inbox", result=f"Inbox {test_n}: {msg}", ok=1 if ok else None))
    return redirect(url_for("settings", tab="inbox", result="Inboxes saved.", ok=1))


@app.post("/settings/safety")
def save_safety():
    f = request.form
    tm = bool(f.get("test_mode"))
    redirect_to = f.get("redirect_to", "").strip()
    if tm and "@" not in redirect_to:
        return redirect(url_for("settings", tab="safety", result="Test mode needs your own email address to send tests to."))
    write_settings({"test_mode": {"enabled": tm, "redirect_to": redirect_to},
                    "review": {"enabled": bool(f.get("review"))},
                    "autorun": {"enabled": bool(f.get("autorun")), "every_minutes": int(f.get("every_minutes") or 60)}})
    msg = "Saved." + ("" if tm else " Test mode is OFF: approved emails now go to real leads.")
    return redirect(url_for("settings", tab="safety", result=msg, ok=1))


@app.post("/settings/keys")
def save_keys():
    f = request.form
    for k in ("ANTHROPIC_API_KEY", "APOLLO_API_KEY", "SLACK_WEBHOOK_URL"):
        if f.get(k, "").strip():
            set_secret(k, f[k].strip())
    write_settings({"ai_reply_sorting": {"enabled": bool(f.get("ai_reply_sorting"))}})
    test = f.get("test")
    if test == "ai":
        ok, msg = test_anthropic(env("ANTHROPIC_API_KEY")) if env("ANTHROPIC_API_KEY") else (False, "Paste your AI key first.")
    elif test == "apollo":
        ok, msg = test_apollo(env("APOLLO_API_KEY")) if env("APOLLO_API_KEY") else (False, "Paste your Apollo key first.")
    else:
        ok, msg = True, "Keys saved."
    return redirect(url_for("settings", tab="keys", result=msg, ok=1 if ok else None))


@app.post("/settings/emails")
def save_emails():
    f = request.form
    steps = []
    for i in range(5):
        variants = []
        for j in range(10):
            body = f.get(f"s{i}v{j}_body", "").replace("\r\n", "\n").strip()
            if not body:
                continue
            v = {"name": f.get(f"s{i}v{j}_name", "").strip() or f"Variant {chr(65 + len(variants))}",
                 "enabled": bool(f.get(f"s{i}v{j}_on")), "body": body + "\n"}
            if not steps:
                v["subject"] = f.get(f"s{i}v{j}_subject", "").strip() or "Quick question, {first_name}"
            variants.append(v)
        if not variants:
            continue
        if not any(v["enabled"] for v in variants):
            variants[0]["enabled"] = True
        delay = 0 if not steps else max(1, int(f.get(f"s{i}_delay") or 3))
        steps.append({"delay_days": delay, "variants": variants})
    if not steps:
        return redirect(url_for("settings", tab="emails", result="The first email needs at least one template."))
    facts = {k: f.get(f"fact_{k}", "").strip() for k in ("team_size", "proof_point", "trial_offer", "lead_time", "stacks")}
    write_settings({"offer": f.get("offer", "").strip(), "sender_address": f.get("sender_address", "").strip(),
                    "personalization": {"enabled": bool(f.get("ai_opener")), "fallback_line": f.get("fallback_line", "").strip()},
                    "facts": facts, "sequence": steps})
    n_var = sum(len(s["variants"]) for s in steps)
    return redirect(url_for("settings", tab="emails", result=f"Saved {len(steps)} emails with {n_var} templates.", ok=1))


@app.post("/settings/targeting")
def save_targeting():
    f = request.form
    titles = [t.strip() for t in f.get("titles", "").splitlines() if t.strip()]
    cur = dict((cfg().get("apollo") or {}).get("common_filters") or {})
    cur.update({"person_titles": titles, "person_seniorities": f.getlist("seniorities"),
                "organization_locations": f.getlist("regions"),
                "organization_num_employees_ranges": f.getlist("sizes")})
    all_names = [s["name"] for s in (cfg().get("apollo") or {}).get("searches", [])]
    on = set(f.getlist("searches"))
    data = read_settings()
    apollo_s = data.get("apollo") or {}
    apollo_s.update({"common_filters": cur, "disabled_searches": [n for n in all_names if n not in on],
                     "max_per_pull": max(1, min(int(f.get("max_per_pull") or 25), 100))})
    write_settings({"apollo": apollo_s})
    note = " Australia's Spam Act generally requires consent for cold email." if "Australia" in cur["organization_locations"] else ""
    return redirect(url_for("settings", tab="targeting", result="Targeting saved. Use “Preview Apollo searches” to check the matches." + note, ok=1))


@app.post("/settings/schedule")
def save_schedule():
    f = request.form
    start_h, end_h = int(f.get("start_hour") or 9), int(f.get("end_hour") or 17)
    if end_h <= start_h:
        return redirect(url_for("settings", tab="schedule", result="End hour must be after start hour."))
    days = [int(d) for d in f.getlist("days")] or [1, 2, 3, 4, 5]
    write_settings({"schedule": {"timezone": f.get("timezone", "America/New_York"), "days": days,
                                 "start_hour": start_h, "end_hour": end_h},
                    "delay_between_sends_seconds": [int(f.get("gap_min") or 60), max(int(f.get("gap_min") or 60), int(f.get("gap_max") or 180))]})
    return redirect(url_for("settings", tab="schedule", result="Schedule saved.", ok=1))


@app.post("/quit")
def quit_app():
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return "<p style='font:16px system-ui;padding:40px'>BSO has stopped. You can close this window.</p>"


# ---------------------------------------------------------------- getting started

PLACEHOLDERS = ("your-outreach-domain", "example.com", "yourdomain.com")


def setup_steps():
    c = cfg()
    tm = c.get("test_mode") or {}
    senders = c.get("senders") or []
    has_job = lambda cmd: one("SELECT COUNT(*) FROM jobs WHERE cmd=? AND status='ok'", cmd) > 0

    todo1 = []
    real = [x for x in senders if not any(p in (x.get("email") or "") for p in PLACEHOLDERS)]
    if not real:
        todo1.append("Connect the inbox your emails will be sent from")
    for i, x in enumerate(real, 1):
        if (x.get("name") or "").strip() in ("", "Your Name"):
            todo1.append(f"Inbox {i}: add the name prospects will see")
        if not env(x.get("password_env") or ""):
            todo1.append(f"Inbox {i}: paste its app password")
    if tm.get("enabled") and not tm.get("redirect_to"):
        todo1.append("Safety: add your own test address (so test emails come to you)")
    s1 = not todo1

    todo2 = [] if env("ANTHROPIC_API_KEY") else ["Paste your Anthropic API key (for AI-written openers and reply sorting)"]
    s2 = not todo2

    n_leads = one("SELECT COUNT(*) FROM leads")
    apollo_key = bool(env("APOLLO_API_KEY"))
    s4 = one("SELECT COUNT(*) FROM outbox") > 0
    s5 = one("SELECT COUNT(*) FROM outbox WHERE status IN ('approved','sent')") > 0
    s6 = one("SELECT COUNT(*) FROM events WHERE type='sent'") > 0
    s7 = one("SELECT COUNT(*) FROM events WHERE type='reply'") > 0
    s8 = not tm.get("enabled")

    steps = [
        dict(id="details", ok=s1, title="Connect your email inbox",
             text="Pick your email provider, enter your address and app password, and click <b>Save &amp; test connection</b>. "
                  "Then add your own email address under <b>Safety</b> so test emails come to you.",
             todo=todo1, link=url_for("settings", tab="inbox"), link_text="Open Settings → Email inboxes"),
        dict(id="keys", ok=s2, title="Add your AI key",
             text="Paste your Anthropic API key and click <b>Save &amp; test</b>. Optional: add your Apollo key to find leads automatically.",
             todo=todo2 + ([] if apollo_key else ["(Optional) Apollo API key to find new leads"]),
             link=url_for("settings", tab="keys"), link_text="Open Settings → Keys"),
        dict(id="leads", ok=n_leads > 0, title="Add leads",
             text=f"You have <b>{n_leads}</b> lead(s). Import your spreadsheet, or find new leads in Apollo "
                  "(previewing Apollo is free; finding leads uses about 1 Apollo credit per lead). "
                  "Just exploring? Add <b>sample leads</b>: fictional people at example.com addresses.",
             sample=True, has_sample=one("SELECT COUNT(*) FROM leads WHERE json_extract(extra,'$.source')=?", SAMPLE_SOURCE) > 0,
             link=url_for("import_page"), link_text="Import a spreadsheet",
             action="apollo-preview" if apollo_key else None, button="Preview Apollo searches (free)"),
        dict(id="draft", ok=s4, title="Let the bot draft your first emails",
             text="The bot writes a personalised draft for each lead that's due. Nothing is sent at this step. "
                  "Drafts wait for you in <b>Review</b>.",
             action="run", button="▶ Run bot now", blocked=(None if s1 and s2 else 1 if not s1 else 2)),
        dict(id="review", ok=s5, title="Review and approve",
             text="Read each draft, edit it if you like, and approve the ones you're happy with. Reject anyone who isn't a fit.",
             link=url_for("review"), link_text="Open review queue"),
        dict(id="test", ok=s6, title="Send yourself a test",
             text=(f"Test mode is on, so approved emails go to <b>{tm.get('redirect_to') or 'your test address'}</b>, never to the lead. "
                   if tm.get("enabled") else "") +
                  "Click the button, then check that inbox: did it land in <b>Inbox or Spam</b>? Does the formatting look right? "
                  "Follow-ups will arrive in the same thread on later runs.",
             action="run", button="▶ Send approved emails", blocked=(None if s5 else 5)),
        dict(id="replies", ok=s7, title="Check that reply detection works",
             text="Add a lead whose email is your own test address, approve and send its draft, then <b>reply</b> to it "
                  "from that inbox with something like \"Sounds interesting, let's talk\". Click the button: the lead should "
                  "move to <b>Replied</b>, its emails stop, and a follow-up task appears.",
             action="check", button="Check replies now", link=url_for("new_lead"), link_text="Add your own address as a lead"),
        dict(id="golive", ok=s8, title="Go live",
             text="Only when your inboxes have warmed up for 2–3 weeks and test emails land in the inbox, not spam: "
                  "in <b>Settings → Safety</b>, switch off <b>Test mode</b> and switch on <b>Run automatically</b>. "
                  "Keep <b>Review before sending</b> on for the first weeks. Start at 10 emails a day per inbox and raise it by about 5 each week.",
             todo=[], link=url_for("settings", tab="safety"), link_text="Open Settings → Safety"),
    ]
    return steps


@app.route("/setup")
def setup():
    steps = setup_steps()
    next_idx = next((i for i, s in enumerate(steps) if not s["ok"]), len(steps))
    return render("setup.html", steps=steps, done=sum(s["ok"] for s in steps), next_idx=next_idx)


# ----------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    a = p.parse_args()
    if a.host != "127.0.0.1" and not os.getenv("CRM_PASSWORD"):
        sys.exit("Set CRM_PASSWORD in .env before exposing the CRM beyond this machine.")
    print(f"\n  BSO is running → open http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}\n")
    threading.Thread(target=_autorun_loop, daemon=True).start()
    app.run(host=a.host, port=a.port)
