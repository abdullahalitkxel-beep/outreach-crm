"""BSO's layout follows who uses it.

- The BSO Bot gets a control room: Status (kill switch, today against capacity, the arrival-window queue, alerts and
  "why not more emails today?"), Campaigns, Review, Domains & inboxes, Lead intake, Activity, and Settings locked
  behind the bot's password.
- Supervisors (and everyone else on a team) get a sales workspace: My day, Conversations, Pipeline, Tasks and a short
  Settings. Cold sending, campaigns and inbox settings don't exist on their screen: cold email is the bot's job.
- Without a team (testing on one computer) everything shows, as before.
Also here: About & updates for everyone.
"""
import io
import json
import time
from datetime import date, datetime, timedelta

from flask import g, redirect, request, send_file, url_for

import autopilot
import bot
import crm
import team
from crm_templates import TEMPLATES
from crm_role_templates import ROLE_TEMPLATES, SETTINGS_EXTRA

TEMPLATES.update(ROLE_TEMPLATES)
TEMPLATES["settings.html"] = TEMPLATES["settings.html"].replace("{{SETTINGS_EXTRA}}", SETTINGS_EXTRA)

app = crm.app
UNLOCK = {"t": 0}


def role():
    return ((team.policy() or {}).get("role") or "") if team.joined() else ""


def is_owner():
    return role() == "owner"


def view_as():
    """The owner can act as the bot or as a supervisor (UAT): that role's layout and functions, with full access."""
    v = team.state().get("view_as") or "full"
    return v if v in ("full", "bot", "workspace") else "full"


def mode():
    if not team.joined():
        return "full"
    if is_owner():
        return view_as()
    return "bot" if team.is_bot() else "workspace"


BOT_NAV = [("botroom", "Status"), ("campaigns", "Campaigns"), ("review", "Review"), ("bot_domains", "Domains & inboxes"),
           ("bot_intake", "Lead intake"), ("bot_activity", "Activity"), ("settings", "Settings")]
WS_NAV = [("myday", "My day"), ("ws_conversations", "Conversations"), ("pipeline", "Pipeline"), ("tasks", "Tasks"), ("settings", "Settings")]
WS_ALLOWED = {"home", "lead", "leads", "export", "about", "myday", "ws_conversations", "pipeline", "prospects", "prospect_stage", "tasks", "task_toggle", "search",
              "comment_add", "comments_seen", "api_conversation", "api_email", "settings", "save_mailbox", "mailbox_check", "save_away",
              "save_idle", "team_sync", "updates_page", "open_data_folder", "add_note", "add_task_route", "prospect_reply",
              "lead_temperature", "lead_address", "lead_stage", "update_lead", "lead_email", "clear_cold_inboxes", "view_as_set"}
WS_TABS = [("handover", "My mailbox & away", "1"), ("team", "Sign-out & account", "2"), ("updates", "About & updates", "3")]
BOT_TABS = [("inbox", "Email inboxes", "1"), ("autopilot", "Bot: intake, limits & tests", "2"), ("safety", "Safety & automation", "3"),
            ("keys", "AI & Apollo keys", "4"), ("emails", "Your offer & emails", "5"), ("targeting", "Lead targeting", "6"),
            ("handover", "Handover", "7"), ("team", "Team", "8"), ("updates", "About & updates", "9")]
FULL_TABS = [("inbox", "Email inboxes", "1"), ("safety", "Safety & automation", "2"), ("keys", "AI & Apollo keys", "3"),
             ("emails", "Your offer & emails", "4"), ("targeting", "Lead targeting", "5"), ("schedule", "Sending schedule", "6"),
             ("handover", "Handover", "7"), ("team", "Team", "8"), ("autopilot", "Bot rules", "9"), ("updates", "About & updates", "10")]


def settings_tabs():
    return {"bot": BOT_TABS, "workspace": WS_TABS}.get(mode(), FULL_TABS)


def nav(default_nav):
    m = mode()
    if m == "full":
        return default_nav + ([("botroom", "BSO Bot", None)] if is_owner() else [])
    extra = {ep: n for ep, _, n in default_nav}
    if m == "bot":
        return [(ep, label, extra.get(ep)) for ep, label in BOT_NAV]
    hot = crm.one("SELECT COUNT(*) FROM tasks WHERE status='open' AND kind='respond'")
    fresh = crm.one("SELECT COUNT(*) FROM messages WHERE direction='in' AND seen=0")
    counts = {"myday": hot or None, "ws_conversations": fresh or None, "tasks": extra.get("tasks")}
    return [(ep, label, counts.get(ep)) for ep, label in WS_NAV]


def active_for(endpoint):
    return {"ws_conversations": "ws_conversations", "pipeline": "pipeline", "lead": "ws_conversations" if mode() == "workspace" else "leads",
            "bot_spot": "botroom", "bot_kill_local": "botroom", "bot_breaker_resume": "botroom", "save_backup": "botroom",
            "save_restore": "botroom"}.get(endpoint, endpoint)


@app.before_request
def role_guard():
    ep = request.endpoint
    if not ep or ep in crm.OPEN_ENDPOINTS or not team.signed_in():
        return None
    m = mode()
    if is_owner():                                   # the owner: every module, every action, in every view
        if ep == "home" and m == "bot":
            return redirect(url_for("botroom"))
        if ep == "home" and m == "workspace":
            return redirect(url_for("myday"))
        return None
    if m == "bot":
        if ep == "home":
            return redirect(url_for("botroom"))
        if (ep == "settings" or ep.startswith("save_") or ep.startswith("inbox_")) and ep not in ("save_idle",) and \
                time.time() - UNLOCK["t"] > 15 * 60:
            return redirect(url_for("settings_unlock", next=request.full_path.rstrip("?")))
    elif m == "workspace":
        if ep == "home":
            return redirect(url_for("myday"))
        if ep not in WS_ALLOWED:
            return redirect(url_for("myday", msg="That part of BSO belongs to the BSO Bot. Cold email runs there; this BSO is for your prospects."))
        if ep == "settings" and request.args.get("tab", "handover") not in {k for k, _, _ in WS_TABS}:
            return redirect(url_for("settings", tab="handover"))
    return None


@app.post("/view-as")
def view_as_set():
    if not is_owner():
        return redirect(url_for("home"))
    v = request.form.get("view", "full")
    st = team.state()
    st["view_as"] = v if v in ("full", "bot", "workspace") else "full"
    st["view_as_at"] = time.time()
    st.pop("device_sent", None)                       # tell the console at once: it routes handovers to an owner acting as supervisor
    team.save_state(st)
    try:
        import threading
        threading.Thread(target=lambda: team.sync(crm.db_connect(crm.cfg().get("database", "outreach.db"))), daemon=True).start()
    except Exception:
        pass
    return redirect(url_for("home"))


def bot_inboxes():
    """Inboxes the BSO Bot runs (reported by the Master Console)."""
    return {e.lower() for e in team.state().get("bot_inboxes") or []}


@app.post("/settings/inboxes/clear-cold")
def clear_cold_inboxes():
    """A supervisor's BSO: remove the old cold inboxes (and their passwords) from this computer; they belong on the bot."""
    senders = [x for x in (crm.cfg().get("senders") or []) if x.get("email")]
    for x in senders:
        crm.set_secret(x.get("password_env") or "", "")
    crm.write_settings({"senders": []})
    return redirect(url_for("settings", tab="handover", result=f"Removed {len(senders)} cold inbox(es) from this computer.", ok=1))


# ---------------------------------------------------------------- bot control room

def _cfg():
    return crm.cfg()


@app.route("/bot")
def botroom():
    c, con = _cfg(), crm.db()
    rp = autopilot.report(c, con)
    cap = sum(i["limit"] for i in rp["inboxes"])
    sent = sum(i["sent_today"] for i in rp["inboxes"])
    last = (crm.rows("SELECT * FROM jobs WHERE cmd='run' ORDER BY id DESC LIMIT 1") or [None])[0]
    return crm.render("botroom.html", rp=rp, cap=cap, sent=sent, last=last, active_on=autopilot.active(c), lands=lambda e: "",
                      backup_folder=(c.get("backup") or {}).get("folder", ""))


@app.post("/bot/kill")
def bot_kill_local():
    on = request.form.get("on") == "1"
    autopilot.set_kill(crm.db(), on, "stopped on the bot computer")
    return redirect(url_for("botroom", msg="All sending stopped." if on else "Sending starts again at the next tick (within 3 minutes)."))


@app.post("/bot/breaker/<int:bid>")
def bot_breaker_resume(bid):
    import safety
    ok = safety.resume_breaker(crm.db(), bid, team.signed_in() or "this computer")
    return redirect(url_for("botroom", msg="Resumed. Watch its bounces closely for the next 50 emails." if ok else "That pause was already lifted."))


@app.post("/bot/backup")
def save_backup():
    import safety
    folder = request.form.get("folder", "").strip().strip('"')
    if "folder" in request.form:
        crm.write_settings({"backup": {"folder": folder}})
    msg = "Backup folder saved."
    if request.form.get("now"):
        r = safety.backup(crm.db(), folder=folder or (crm.cfg().get("backup") or {}).get("folder") or None)
        msg = f"Backup made: {r['detail']}" + (f". Off-laptop copy: {r['offsite_detail']}" if r.get("offsite_detail") else "")
    return redirect(url_for("botroom", msg=msg))


@app.post("/bot/restore")
def save_restore():
    import safety
    if not is_owner() and team.signed_in():
        return redirect(url_for("botroom", msg="Only the owner can restore a backup."))
    if request.form.get("confirm", "").strip().upper() != "RESTORE":
        return redirect(url_for("botroom", msg="Type RESTORE to confirm. Nothing was changed."))
    path = request.form.get("path", "").strip().strip('"')
    try:
        detail = safety.restore(path, now=False)
    except Exception as e:
        return redirect(url_for("botroom", msg=f"Not restored: {e}"))
    autopilot.set_kill(crm.db(), True, "a backup is waiting to be restored: quit BSO and open it again")
    return redirect(url_for("botroom", msg=f"Backup checked ({detail}). Sending is stopped. Quit BSO (tray icon → Quit) and open it again: "
                                            "the backup is put in place as it starts. Then press Start sending."))


@app.post("/bot/spot/<int:sid>")
def bot_spot(sid):
    con = crm.db()
    con.execute("UPDATE spot_checks SET status=?, note=? WHERE id=?", (request.form.get("status", "ok"), request.form.get("note", ""), sid))
    con.commit()
    return redirect(url_for("botroom", msg="Recorded."))


@app.route("/bot/domains", methods=["GET", "POST"])
def bot_domains():
    c, con = _cfg(), crm.db()
    msg, err = None, None
    if request.method == "POST":
        f = request.form
        try:
            if f.get("action") == "client":
                autopilot.add_client(con, f.get("name"), f.get("line"))
                msg = "Client saved."
            elif f.get("action") == "domain":
                cl = con.execute("SELECT id FROM clients WHERE name=?", (f.get("client"),)).fetchone()
                if not cl:
                    raise ValueError("Add the client first.")
                autopilot.assign_domain(con, f.get("domain"), cl["id"], int(f.get("cap") or 0))
                msg = "Domain saved."
            elif f.get("action") == "campaign":
                cid = int(f.get("cid"))
                cl = con.execute("SELECT id FROM clients WHERE name=?", (f.get("client"),)).fetchone()
                con.execute("UPDATE campaigns SET client_id=?, weight=?, autonomy=?, arrive=?, info_mode=?, info_text=? WHERE id=?",
                            (cl["id"] if cl else None, max(1, int(f.get("weight") or 1)), f.get("autonomy") or "assisted",
                             f"{f.get('arrive_from') or '08:45'}-{f.get('arrive_to') or '10:30'}", f.get("info_mode") or "off",
                             f.get("info_text", "").replace("\r\n", "\n"), cid))
                autopilot.set_campaign_domains(con, cid, f.getlist("domains"))
                msg = "Campaign saved."
            elif f.get("action") == "readiness":
                import ops
                s = next(x for x in bot.load_senders(c) if x["email"].lower() == f.get("email", "").lower())
                ok, detail = ops.readiness(c, con, s)
                msg = f"{s['email']}: {detail}"
            con.commit()
        except (ValueError, StopIteration) as e:
            err = str(e) or "Not found."
    rp = autopilot.report(c, con)
    camps = [dict(r, domains=autopilot.campaign_domains(r), client=(autopilot.client_of(con, r["id"]) or {}).get("name", ""),
                  window="-".join(t.strftime("%H:%M") for t in autopilot.window_of(c, r)))
             for r in con.execute("SELECT * FROM campaigns ORDER BY id").fetchall()]
    return crm.render("botdomains.html", rp=rp, camps=camps, msg2=msg, err2=err)


@app.route("/bot/intake", methods=["GET", "POST"])
def bot_intake():
    c, con = _cfg(), crm.db()
    if request.method == "POST":
        f = request.form
        b = dict(c.get("intake_box") or {})
        b.update(email=f.get("email", "").strip().lower(), name=f.get("name", "BSO Bot").strip() or "BSO Bot",
                 imap_host=f.get("imap_host", "").strip(), imap_port=int(f.get("imap_port") or 993),
                 smtp_host=f.get("smtp_host", "").strip(), smtp_port=int(f.get("smtp_port") or 465),
                 owner=f.get("owner", "").strip().lower() or autopilot.DEFAULTS["owner"], password_env="INTAKE_PASSWORD",
                 verified_only=f.get("verified_only") == "on")
        if f.get("password"):
            bot.SECRETS.set("INTAKE_PASSWORD", f["password"])
        crm.write_settings({"intake_box": b})
        msg = "Lead intake mailbox saved."
        try:
            import ops
            lines = ops.check_intake(crm.cfg(), con)
            msg += " " + (" ".join(lines) if lines else "Connected; no new email.")
        except Exception as e:
            msg += f" Couldn't connect: {e}"
        return redirect(url_for("bot_intake", msg=msg))
    b = dict(c.get("intake_box") or {})
    rows = crm.rows("SELECT * FROM intake_log ORDER BY id DESC LIMIT 50")
    import ops
    return crm.render("botintake.html", b=b, pw=bool(bot.env("INTAKE_PASSWORD")), rows=rows, stock=ops.stock_rows(c, con),
                      cols=ops.TEMPLATE_COLUMNS)


@app.get("/bot/template.xlsx")
def bot_template():
    import ops
    return send_file(io.BytesIO(ops.template_xlsx(crm.db(), crm.cfg())), as_attachment=True, download_name="BSO-lead-template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/bot/activity")
def bot_activity():
    items = crm.rows("SELECT * FROM auto_log ORDER BY id DESC LIMIT 300")
    alerts = crm.rows("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")
    return crm.render("botactivity.html", items=items, alerts=alerts)


@app.route("/settings/unlock", methods=["GET", "POST"])
def settings_unlock():
    err = None
    nxt = request.values.get("next") or url_for("settings")
    if request.method == "POST":
        st = team.state()
        if team._verify_offline(st, st.get("username", ""), request.form.get("password", "")):
            UNLOCK["t"] = time.time()
            return redirect(nxt if nxt.startswith("/") else url_for("settings"))
        err = "That isn't the bot's password."
    return crm.render("unlock.html", err=err, nxt=nxt)


@app.post("/settings/autopilot")
def save_autopilot():
    f = request.form
    ap = dict(crm.cfg().get("autopilot") or {})
    for k in ("floor", "ceiling", "start_limit", "recipient_domain_per_day", "company_people_30d", "cross_client_days", "first_email_gap_days",
              "stock_alert_days", "alert_hour", "digest_hour"):
        if f.get(k):
            ap[k] = int(f[k])
    ap["owner"] = f.get("owner", "").strip().lower() or autopilot.DEFAULTS["owner"]
    if f.get("window_from") and f.get("window_to"):
        ap["window"] = f"{f['window_from']}-{f['window_to']}"
    if mode() == "full":
        ap["enabled"] = f.get("enabled") == "on"
    seeds = []
    for i in (1, 2):
        e = f.get(f"seed{i}", "").strip().lower()
        if e:
            seeds.append({"email": e, "password_env": f"SEED{i}_PASSWORD"})
            if f.get(f"seed{i}_pw"):
                bot.SECRETS.set(f"SEED{i}_PASSWORD", f[f"seed{i}_pw"])
    crm.write_settings({"autopilot": ap, "seeds": seeds})
    return redirect(url_for("settings", tab="autopilot", result="Saved.", ok=1))


# ---------------------------------------------------------------- supervisor workspace

@app.route("/myday")
def myday():
    now = datetime.now().isoformat(timespec="minutes")
    hot = crm.rows("SELECT t.*, l.first_name, l.email, l.company, l.temperature FROM tasks t JOIN leads l ON l.id=t.lead_id "
                   "WHERE t.status='open' AND t.kind='respond' ORDER BY t.due_at")
    for h in hot:
        try:
            left = datetime.fromisoformat(h["due_at"]) - datetime.now()
            h["left"] = int(left.total_seconds() // 60)
        except Exception:
            h["left"] = None
    overdue = crm.rows("SELECT t.*, l.first_name, l.email FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id WHERE t.status='open' "
                       "AND COALESCE(t.kind,'') != 'respond' AND t.due_date < ? ORDER BY t.due_date", date.today().isoformat())
    today = crm.rows("SELECT t.*, l.first_name, l.email FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id WHERE t.status='open' "
                     "AND COALESCE(t.kind,'') != 'respond' AND t.due_date = ? ORDER BY t.id", date.today().isoformat())
    unread = crm.rows("SELECT m.*, l.first_name, l.company FROM messages m JOIN leads l ON l.id=m.lead_id WHERE m.direction='in' AND m.seen=0 "
                      "ORDER BY m.at DESC LIMIT 20")
    new_prospects = crm.rows("SELECT * FROM leads WHERE handed_from IS NOT NULL AND COALESCE(prospect_stage,'new')='new' ORDER BY id DESC LIMIT 20")
    return crm.render("myday.html", hot=hot, overdue=overdue, today_tasks=today, unread=unread, new_prospects=new_prospects, now=now)


@app.route("/conversations")
def ws_conversations():
    g.force_view = "list"
    return crm.prospects()


@app.route("/pipeline")
def pipeline():
    g.force_view = "board"
    return crm.prospects()


# ---------------------------------------------------------------- About & updates

@app.route("/updates", methods=["GET", "POST"])
def updates_page():
    import updater
    msg = None
    if request.method == "POST":
        act = request.form.get("action")
        if act == "check":
            info = updater.check(force=True)
            msg = info.get("message")
        elif act == "now":
            ok, msg = updater.install_now(reason="started by hand")
        elif act == "tonight":
            updater.set_tonight(True)
            msg = "BSO updates tonight between 01:00 and 05:00 (it must be running)."
        return redirect(url_for("settings", tab="updates", result=msg, ok=1))
    return redirect(url_for("settings", tab="updates"))


def ctx():
    """Extra values every page needs (update banner, owner's view, inboxes shared with the bot)."""
    senders = [x["email"].lower() for x in (crm.cfg().get("senders") or []) if x.get("email") and "example" not in x["email"]]
    shared = sorted(set(senders) & bot_inboxes()) if team.joined() and not team.is_bot() else []
    r = role()
    out = {"role_mode": mode(), "settings_tabs": settings_tabs(), "is_owner": is_owner(), "view_as": view_as() if is_owner() else None,
           "role_label": {"owner": "Owner", "bot": "BSO Bot", "supervisor": "Supervisor", "manager": "Manager", "member": "Supervisor"}.get(r, ""),
           "shared_inboxes": shared, "cold_here": len(senders) if mode() == "workspace" and not is_owner() else 0}
    try:
        import updater
        out["update"] = updater.banner()
    except Exception:
        out["update"] = None
    return out
