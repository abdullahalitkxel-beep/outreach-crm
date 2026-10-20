"""BSO Bot mode (stays signed in, status on the console, start with Windows) and leads by email.
   python tests/bot_mode_test.py"""
import io, json, os, re, sys, tempfile
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
tmp = Path(tempfile.mkdtemp(prefix="botmode-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "bot")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "bot").mkdir()
import bot, team, hub  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=bot_mode_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)

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

con = bot.db_connect("outreach.db")
cfg = bot.load_config(str(bot.DATA / "config.yaml"))
cfg["lead_intake"] = {"enabled": True, "inbox": "cole.reed@getneosofttech.com", "allowed_senders": ["omar@blacswanken.com"],
                      "add_to_sequence": True, "verified_only": True}
sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed"}
sent = []
bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None: sent.append(dict(to=to, subject=subject, body=body)) or "<x>"
bot.notify = lambda *a, **k: None

def mail(frm, files=(), verified=True, to="cole.reed@getneosofttech.com", subject="New leads"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, to, subject, f"<{len(sent)}-{frm}>"
    if verified:
        m["Authentication-Results"] = "mx.siteground.net; dkim=pass header.d=blacswanken.com; spf=pass; dmarc=pass (p=quarantine) header.from=blacswanken.com"
    m.set_content("Here are this week's leads.")
    for name, data, mt in files:
        m.add_attachment(data, maintype=mt.split("/")[0], subtype=mt.split("/")[1], filename=name)
    return m

csv_data = "Email,First Name,Company Name,Job Title,Website\nann@initech.com,Ann,Initech,CTO,initech.com\ninfo@initech.com,,Initech,,\nbob@hooli.com,Bob,Hooli,VP Engineering,hooli.com\n".encode()
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["email", "firstName", "companyName", "jobTitle"]); ws.append(["cara@umbrella.com", "Cara", "Umbrella", "CTO"]); ws.append(["ann@initech.com", "Ann", "Initech", "CTO"])
xbuf = io.BytesIO(); wb.save(xbuf)

bot.handle_message(cfg, con, sender, mail("Omar <omar@blacswanken.com>", [("week43.csv", csv_data, "text/csv"),
                                         ("more.xlsx", xbuf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]))
emails = sorted(r[0] for r in con.execute("SELECT email FROM leads"))
check(emails == ["ann@initech.com", "bob@hooli.com", "cara@umbrella.com"], "CSV and Excel attachments imported; shared inbox and duplicate skipped")
check(con.execute("SELECT COUNT(*) FROM leads WHERE status='active' AND step=0").fetchone()[0] == 3, "imported leads are in the email sequence")
r0 = sent[-1]
check(r0["to"] == "omar@blacswanken.com" and "week43.csv: 2 lead(s) added" in r0["body"] and "more.xlsx: 1 lead(s) added" in r0["body"]
      and "already in CRM" in r0["body"], "the sender gets a reply saying what was added and skipped")
src = json.loads(con.execute("SELECT extra FROM leads WHERE email='bob@hooli.com'").fetchone()[0]).get("source", "")
check("emailed by omar@blacswanken.com" in src, "each lead remembers which email it came from")

before = len(sent)
bot.handle_message(cfg, con, sender, mail("Omar <omar@blacswanken.com>", [("x.csv", b"Email\nzed@z.com\n", "text/csv")], verified=False))
check(not con.execute("SELECT 1 FROM leads WHERE email='zed@z.com'").fetchone() and "couldn't confirm" in sent[-1]["body"], "unverified sender (possible fake) refused, and told why")
bot.handle_message(cfg, con, sender, mail("Mallory <mallory@evil.com>", [("x.csv", b"Email\nzed@z.com\n", "text/csv")]))
check(not con.execute("SELECT 1 FROM leads WHERE email='zed@z.com'").fetchone() and not any(s["to"] == "mallory@evil.com" for s in sent),
      "files from anyone not on the list are ignored")
bot.handle_message(cfg, con, {"email": "wade.lane@getneosofttech.com", "name": "Wade"}, mail("omar@blacswanken.com", [("x.csv", b"Email\nzed@z.com\n", "text/csv")]))
check(not con.execute("SELECT 1 FROM leads WHERE email='zed@z.com'").fetchone(), "only the chosen intake inbox imports files")
bot.handle_message(cfg, con, sender, mail("omar@blacswanken.com"))
check("no lead file" in sent[-1]["body"], "an email without a file gets a helpful reply")

# ---- bot account: stays signed in; status reaches the console
hub.prepare(str(tmp / "hub"))
c = hub.app.test_client()
tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar", "email": "omar@blacswanken.com", "password": "owner-pass-123", "password2": "owner-pass-123"})
c.post("/users/new", data={"_csrf": tok("/users"), "name": "BSO Bot", "email": "bot@getneosofttech.com", "username": "bsobot", "role": "bot", "password": "bot-pass-12345"})
check("<td>bot</td>" in c.get("/users").get_data(as_text=True), "console can create a BSO Bot account")

team.save_state({"hub": "http://127.0.0.1:9/", "username": "bsobot", "policy": {"role": "bot", "name": "BSO Bot", "enabled": True}})
bot.SECRETS.set(team.TOKEN_KEY, "tok")
check(team.resume() and team.signed_in() == "bsobot" and team.SIGNED_IN.get("bot"), "bot stays signed in after a restart")
team.sign_out()
check(not team.resume(), "after someone signs the bot out on purpose, it asks for the password again")
st = team.state(); st.pop("no_resume"); st["policy"] = {"role": "supervisor"}; team.save_state(st)
check(not team.resume(), "human accounts never skip the password")

status = team.status(con)
check(status["leads_not_started"] == 3 and status["daily_capacity"] > 0 and "days_of_leads" in status, "bot reports leads left and days of leads")
uid = hub.sqlite3.connect(str(hub.DB_PATH)).execute("SELECT id FROM users WHERE username='bsobot'").fetchone()[0]
hc = hub.sqlite3.connect(str(hub.DB_PATH)); hc.row_factory = hub.sqlite3.Row
status.update(autorun=True, every_minutes=60, last_run="2026-01-01T00:00:00+00:00", last_run_status="failed", last_run_line="PROBLEM: login rejected", days_of_leads=1.5)
hub.ingest(hc, uid, {"events": [{"id": "s", "type": "__status", "data": status}]})
hc.execute("UPDATE users SET last_seen=? WHERE id=?", (hub.iso(), uid)); hc.commit()
page = c.get("/").get_data(as_text=True)
check("BSO Bot" in page and "Online" in page and ("Hasn&#39;t run since" in page or "Hasn't run since" in page), "console shows the bot card and warns when it hasn't run")
check("login rejected" in page and "days of new leads left" in page, "console warns about a failed run and running out of leads")

# ---- start with Windows (registry), only on Windows
import crm
if sys.platform == "win32":
    sys.frozen = True
    try:
        crm.set_startup(True)
        check(crm.startup_enabled(), "start with Windows switched on")
        crm.set_startup(False)
        check(not crm.startup_enabled(), "and off again")
        crm.keep_awake(True); crm.keep_awake(False)
        check(True, "keep-awake call works")
    finally:
        del sys.frozen
else:
    check(not crm.startup_supported(), "start with Windows is offered only on the Windows app")
print(f"\nAll {n} bot-mode checks passed.")
