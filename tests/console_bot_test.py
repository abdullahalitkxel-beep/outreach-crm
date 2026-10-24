"""The Master Console's BSO Bot page (the bot's control room mirrored, changes sent back as sealed commands, kill switch),
devices with approval of a second laptop, update control, the "bot silent" alert, the Client Scope room, and BSO's
layout by role (bot control room, supervisor workspace). A hub and the bot laptop talk over real HTTP.
   python tests/console_bot_test.py"""
import json, os, re, sys, tempfile, threading
from datetime import timedelta
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
tmp = Path(tempfile.mkdtemp(prefix="console-bot-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "bot")
os.environ["HUB_HOME"] = str(tmp / "hub")
os.environ["COMPUTERNAME"] = "BOT-LAPTOP"
(tmp / "bot").mkdir()
import hub, bot, team, autopilot  # noqa: E402,E401
from werkzeug.serving import make_server  # noqa: E402

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=console_bot_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1
def _crash(t, v, tb):
    import traceback
    gh_error("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)
sys.excepthook = _crash
txt = lambda r: r.get_data(as_text=True).replace("&#39;", "'").replace("&#34;", '"').replace("&amp;", "&")

team.built_in_hub = lambda: ""
hub.prepare(str(tmp / "hub"))
srv = make_server("127.0.0.1", 8793, hub.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:8793"
c = hub.app.test_client()
tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
c.post("/setup", data={"_csrf": tok("/setup"), "org": "BlacSwanken", "name": "Omar", "email": "omar@blacswanken.com",
                       "password": "owner-pass-123", "password2": "owner-pass-123"})
c.post("/users/new", data={"_csrf": tok("/users"), "name": "BSO Bot", "email": "bot@blacswanken.com", "username": "bso.bot", "role": "bot",
                           "password": "bot-password-123", "approval": "master"})
c.post("/users/new", data={"_csrf": tok("/users"), "name": "Ali", "email": "ali@blacswanken.com", "username": "ali", "role": "supervisor",
                           "password": "ali-password-123"})

# ---------------------------------------------------------------- the bot laptop signs in
import crm  # noqa: E402
crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [{"email": "sam@neo-mail.com", "name": "Sam", "smtp_host": "h", "smtp_port": 465, "imap_host": "h", "password_env": "PW1"}],
                    "test_mode": {"enabled": False}})
bot.SECRETS.set("PW1", "pw")
con = bot.db_connect("outreach.db")
team.sign_in(con, "bso.bot", "bot-password-123", consent=True, hub=URL)
check(team.joined() and team.is_bot(), "the bot laptop joins as the BSO Bot")
check(autopilot.active(bot.load_config(crm.CONFIG_PATH)), "the bot rules switch on by themselves on the bot account")
cl = autopilot.add_client(con, "NeoSOFT", "tech services")
autopilot.assign_domain(con, "neo-mail.com", cl)
con.execute("UPDATE campaigns SET name='Neo UK', client_id=? WHERE id=1", (cl,))
autopilot.set_campaign_domains(con, 1, ["neo-mail.com"])
con.commit()
for _ in range(3):
    ok, msg = team.sync(con)
check(ok, f"the bot checks in ({msg})")
botuid = hub.db_direct = None
hc = hub.sqlite3.connect(str(hub.DB_PATH))
hc.row_factory = hub.sqlite3.Row
b = hc.execute("SELECT * FROM users WHERE role='bot'").fetchone()
check(b["bot_report"] and json.loads(b["bot_report"])["campaigns"][0]["name"] == "Neo UK", "the bot's full report reaches the console")
check(b["device_info"] and json.loads(b["device_info"])["device"] == "BOT-LAPTOP", "the bot reports its laptop for the device list")
page = txt(c.get("/bot"))
check("Neo UK" in page and "Why not more emails today?" in page and "neo-mail.com" in page, "the console's BSO Bot page mirrors the control room")

# ---------------------------------------------------------------- changes from the console
c.post("/bot/campaign", data={"_csrf": tok("/bot"), "id": "1", "autonomy": "supervised", "arrive_from": "09:00", "arrive_to": "10:00",
                              "info_mode": "template", "info_text": "Hi {first_name}, here is more.", "domains_sent": "1", "domains": ["neo-mail.com"]})
r = c.post("/bot/domain", data={"_csrf": tok("/bot"), "domain": "neo-mail.com", "client": "Other Co"}, follow_redirects=True)
check("never shared between clients" in txt(r), "the console refuses to give a domain to a second client")
c.post("/bot/kill", data={"_csrf": tok("/bot"), "on": "1"})
team.sync(con)
team.sync(con)
row = con.execute("SELECT * FROM campaigns WHERE id=1").fetchone()
check(row["autonomy"] == "supervised" and row["arrive"] == "09:00-10:00" and row["info_mode"] == "template", "campaign changes made in the console apply on the bot")
check(autopilot.killed(con)[0], "Stop all sending in the console stops the bot")
team.sync(con)
check("applied" in txt(c.get("/bot")), "the console shows the changes as applied")
c.post("/bot/kill", data={"_csrf": tok("/bot"), "on": "0"})
team.sync(con)
team.sync(con)
check(not autopilot.killed(con)[0], "Start sending in the console starts it again")

# ---------------------------------------------------------------- updates control and the bot-silent alert
c.post("/devices/updates", data={"_csrf": tok("/devices"), "hold": "on", "min_version": "1.0.99"})
team.sync(con)
check(team.state().get("updates", {}).get("hold") and team.state()["updates"]["min_version"] == "1.0.99", "update hold and minimum version reach every BSO")
mails = []
hub.send_mail = lambda to, subj, body: mails.append((to, subj))
hc.execute("UPDATE users SET last_seen=? WHERE role='bot'", (hub.iso(hub.now() - timedelta(hours=3)),))
hc.commit()
import hub_bot  # noqa: E402
hub_bot.watch(hc)
hub_bot.watch(hc)
check(len(mails) == 1 and "silent" in mails[0][1] and mails[0][0] == "omar@blacswanken.com", "the owner is emailed once when the bot goes silent for 2 hours")

# ---------------------------------------------------------------- devices: a second laptop waits for approval
os.environ["COMPUTERNAME"] = "PERSONAL-PC"
team.sign_in(con, "bso.bot", "bot-password-123", consent=True, hub=URL)
ok, msg = team.sync(con)
allowed, why, _ = team.gate(con)
check(not ok and "approval" in msg and not allowed, "a second laptop for the same person waits for the owner's approval")
page = txt(c.get("/devices"))
check("PERSONAL-PC" in page and "Approve" in page, "the device list shows it with an Approve button")
did = hc.execute("SELECT id FROM devices WHERE name='PERSONAL-PC' AND revoked=0").fetchone()[0]
c.post(f"/devices/{did}", data={"_csrf": tok("/devices"), "action": "approve"})
ok, msg = team.sync(con)
check(ok and team.gate(con)[0], "once approved it works")

# ---------------------------------------------------------------- the Client Scope room
SCOPE = """Acme Homes outreach scope, Q1.
We want to book meetings with heads of property and operations directors at estate agencies with 20-200 staff in the United Kingdom and Germany.
Our offer: a property management platform that cuts admin time.
Proof: 140 agencies use it; average 6 hours saved per week per agent.
Tone: warm and professional. Never mention competitor Rightmove.
Target 10 meetings a month. Start 2026-11-01, end 2027-01-31.
Please also connect with them on LinkedIn after the first email."""
card = {"campaign_type": {"value": "meeting booking", "source": "We want to book meetings with heads of property"},
        "titles": {"value": "heads of property, operations directors", "source": "heads of property and operations directors"},
        "industries": {"value": "estate agencies", "source": "at estate agencies with 20-200 staff"},
        "company_size": {"value": "20-200 staff", "source": "with 20-200 staff"}, "countries": {"value": "United Kingdom, Germany", "source": "in the United Kingdom and Germany"},
        "offer": {"value": "a property management platform that cuts admin time", "source": "Our offer: a property management platform that cuts admin time."},
        "proof_points": {"value": "140 agencies use it; average 6 hours saved per week per agent", "source": "Proof: 140 agencies use it; average 6 hours saved per week per agent."},
        "tone": {"value": "warm and professional", "source": "Tone: warm and professional."}, "avoid": {"value": "mentioning Rightmove", "source": "Never mention competitor Rightmove."},
        "volume": {"value": "", "source": ""}, "start": {"value": "2026-11-01", "source": "Start 2026-11-01"}, "end": {"value": "2027-01-31", "source": "end 2027-01-31"},
        "success": {"value": "10 meetings a month", "source": "Target 10 meetings a month."}, "handover": {"value": "", "source": ""},
        "line": {"value": "real estate", "source": "a sentence that is not in the document"}, "questions": ["Who takes interested prospects?"]}
bot.llm = lambda *a, **k: json.dumps(card)
r = c.post("/clients/scope", data={"_csrf": tok("/clients"), "client": "Acme Homes", "text": SCOPE}, follow_redirects=True)
page = txt(r)
check("Scope card" in page and "Our offer: a property management platform" in page, "the scope is read into a card, each field with its source sentence")
check("wasn't found in the document" in page, "a field whose quoted source isn't in the document is flagged")
check("Who takes interested prospects?" in page and "Volume: not stated" in page, "missing answers become questions for the client")
check("LinkedIn" in page and "consent" in page and "figures" in page, "conflicts flagged: LinkedIn steps, consent rules for the UK/Germany, figures to back up")
check("Leads needed" in page and "about 2000 a month" in page, "it works out the leads and inboxes the target needs")
sid = hc.execute("SELECT id FROM scopes WHERE client='Acme Homes'").fetchone()[0]
check("Summary for the client" in page and "Out of scope: LinkedIn" in page, "a summary to send the client for confirmation")
c.post(f"/scopes/{sid}", data={"_csrf": tok(f"/scopes/{sid}"), "action": "approve", "p_name": "Acme UK meetings", "p_autonomy": "assisted", "p_arrive": "08:45-10:30"})
team.sync(con)
team.sync(con)
camp = con.execute("SELECT c.*, cl.name client, cl.line FROM campaigns c JOIN clients cl ON cl.id=c.client_id WHERE c.name='Acme UK meetings'").fetchone()
check(camp and camp["client"] == "Acme Homes" and camp["line"] == "real estate" and "property management" in json.loads(camp["brief"])["offer"],
      "approving the scope sets the client and campaign up on the bot, with the brief")
SCOPE2 = SCOPE.replace("10 meetings", "15 meetings")
card["success"] = {"value": "15 meetings a month", "source": "Target 15 meetings a month."}
r = c.post("/clients/scope", data={"_csrf": tok("/clients"), "client": "Acme Homes", "text": SCOPE2}, follow_redirects=True)
check("What changed since v1" in txt(r) and "15 meetings a month" in txt(r), "a new version of the scope shows what changed, field by field")

# ---------------------------------------------------------------- BSO's layout by role
team.SIGNED_IN.update(user="bso.bot", name="BSO Bot", bot=True)
w = crm.app.test_client()
r = w.get("/")
check(r.status_code == 302 and r.headers["Location"].endswith("/bot"), "the bot opens on its control room")
page = txt(w.get("/bot"))
check('id="nav-bot_domains"' in page and 'id="nav-bot_intake"' in page and 'id="nav-opportunities"' not in page, "the bot's menu is the control room's")
r = w.get("/settings")
check(r.status_code == 302 and "/settings/unlock" in r.headers["Location"], "the bot's settings are locked")
w.post("/settings/unlock", data={"password": "wrong", "next": "/settings"})
check(w.get("/settings").status_code == 302, "a wrong password doesn't unlock them")
w.post("/settings/unlock", data={"password": "bot-password-123", "next": "/settings"})
check(w.get("/settings").status_code == 200, "the bot's password unlocks settings for 15 minutes")
check(w.get("/bot/template.xlsx").status_code == 200 and w.get("/bot/intake").status_code == 200 and w.get("/bot/domains").status_code == 200,
      "template download, Lead intake and Domains & inboxes open")
check("Spam-placement test addresses" in txt(w.get("/settings?tab=autopilot")) and "About &amp; updates" in w.get("/settings?tab=updates").get_data(as_text=True),
      "the bot's own settings: intake, limits, test addresses, updates")
team.is_bot = lambda: False
team.policy = lambda: {"role": "supervisor", "name": "Ali"}
r = w.get("/")
check(r.status_code == 302 and r.headers["Location"].endswith("/myday"), "a supervisor opens on My day")
page = txt(w.get("/myday"))
check('id="nav-ws_conversations"' in page and 'id="nav-pipeline"' in page and 'id="nav-campaigns"' not in page and 'id="nav-review"' not in page,
      "the supervisor's menu has no cold-email parts")
check(w.get("/campaigns").status_code == 302 and w.get("/review").status_code == 302, "campaign and review pages aren't reachable for supervisors")
r = w.get("/settings?tab=inbox")
check(r.status_code == 302 and "tab=handover" in r.headers["Location"], "no cold-inbox settings on a supervisor's BSO")
check(w.get("/conversations").status_code == 200 and w.get("/pipeline").status_code == 200, "Conversations and Pipeline open")
check(w.get("/settings?tab=handover").status_code == 200 and w.get("/settings?tab=updates").status_code == 200, "a supervisor's short settings open")
page = txt(w.get("/settings"))
check("Handover to a lead gen supervisor" in page and "Email inboxes</h2>" not in page, "a supervisor's Settings opens on their own tab, not on cold inboxes")
import watcher  # noqa: E402
check(watcher._cold_not_here(), "a supervisor's BSO doesn't watch or read cold inboxes")
check("Old cold inboxes on this computer" in page, "a supervisor is offered to remove old cold inboxes from their computer")

# ---------------------------------------------------------------- the owner: every module, and Act as for UAT
team.policy = lambda: {"role": "owner", "name": "Omar"}
st = team.state(); st["view_as"] = "full"; team.save_state(st)
check(not watcher._cold_not_here(), "the owner's BSO keeps its inboxes")
for path in ("/campaigns", "/review", "/leads", "/bot", "/bot/domains", "/bot/intake", "/myday", "/pipeline", "/settings?tab=inbox", "/settings?tab=autopilot", "/setup"):
    check(w.get(path).status_code == 200, f"owner opens {path}")
page = txt(w.get("/leads"))
check('id="nav-campaigns"' in page and "Act as" in page and "Owner: full access" in page, "the owner gets the full menu and the Act as switch")
w.post("/view-as", data={"view": "bot"})
check(team.acting_as() == "bot" and autopilot.active(bot.load_config(crm.CONFIG_PATH)), "owner acting as the bot: the bot's rules run on this computer")
r = w.get("/")
check(r.status_code == 302 and r.headers["Location"].endswith("/bot") and w.get("/settings").status_code == 200 and w.get("/campaigns").status_code == 200,
      "…with the bot's layout and full access (no settings lock)")
page = txt(w.get("/bot"))
check("Owner acting as" in page and 'id="nav-bot_domains"' in page, "a strip shows the owner is acting as the bot")
w.post("/view-as", data={"view": "workspace"})
check(team.acting_as() == "supervisor" and not autopilot.active(bot.load_config(crm.CONFIG_PATH)), "owner acting as a supervisor")
r = w.get("/")
check(r.headers["Location"].endswith("/myday") and w.get("/campaigns").status_code == 200, "…with the supervisor's layout and still every module")
hc.execute("UPDATE users SET device_info=? WHERE role='owner'", (json.dumps(team.device_info()),))
hc.commit()
sups = [x["email"] for x in hub.supervisor_config(hc)["supervisors"]]
check("omar@blacswanken.com" in sups, "the console hands prospects to the owner while acting as a supervisor")
w.post("/view-as", data={"view": "full"})
check(team.acting_as() == "owner", "back to Owner")
page = txt(w.get("/"))
check('id="userchip"' in page and "Good to see you, " in page and 'id="nav-botroom"' in page,
      "Home greets the signed-in person by name, a user chip shows who is signed in, and the owner has a BSO Bot link")
print(f"\nAll {n} console and layout checks passed.")
os._exit(0)
