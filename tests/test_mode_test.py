"""Test mode is kept apart from live: what a test does to leads is undone when you go live.     python tests/test_mode_test.py"""
import os, sys, tempfile
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="testmode-test-"))
os.environ["OUTREACH_HOME"] = str(tmp)
os.environ["BSO_NO_SIGNIN"] = "1"
sys.path.insert(0, str(root))
import bot, crm, team  # noqa: E402

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=test_mode_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
cfg = crm.cfg()
con = bot.db_connect(cfg.get("database", "outreach.db"))
for e_, f_ in (("sarah@acme.com", "Sarah"), ("tom@globex.com", "Tom"), ("kim@umbrella.com", "Kim")):
    con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject, stage) VALUES (?,?,'Co','{}','active',1,'2026-10-01','Quick question','contacted')", (e_, f_))
con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject, stage) VALUES ('old@test.com','Old','Co','{}','active',1,'2026-09-01','Hi','contacted')")
con.execute("INSERT INTO events(lead_id, at, type, detail, sender) VALUES ((SELECT id FROM leads WHERE email='old@test.com'), '2026-09-02', 'sent', 'step 1 (test)', 'x@y.com')")
con.commit()
bot.sync_mode(con, cfg)              # first start of this version, test mode on: earlier test sends are adopted
bot.send_email = lambda *a, **k: "<x>"
sender = {"email": "inbox1@getneosofttech.com", "name": "Cole Reed"}
lid = lambda e: con.execute("SELECT id FROM leads WHERE email=?", (e,)).fetchone()[0]
row = lambda e: dict(con.execute("SELECT * FROM leads WHERE email=?", (e,)).fetchone())

def test_reply(lead, text, mid):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "Omar <omar@blacswanken.com>", f"Re: [TEST for {lead}] Quick question", mid
    m.set_content(text)
    bot.handle_message(cfg, con, sender, m)
    con.commit()

# ---- testing
test_reply("sarah@acme.com", "Sounds interesting, tell me more.", "<t1>")
test_reply("tom@globex.com", "Please unsubscribe me.", "<t2>")
con.execute("UPDATE leads SET step=2, next_send_at='2026-10-30' WHERE email='kim@umbrella.com'")     # a test follow-up went out
bot.log_event(con, lid("kim@umbrella.com"), "sent", "step 2 (test)", sender["email"])
con.execute("UPDATE leads SET company='Umbrella Corp' WHERE email='kim@umbrella.com'")             # you fixed the company name meanwhile
con.commit()
check(row("sarah@acme.com")["status"] == "replied" and row("tom@globex.com")["status"] == "unsubscribed", "in test mode, test replies show on the leads as usual")
check(con.execute("SELECT 1 FROM suppression WHERE email='tom@globex.com'").fetchone(), "including do-not-contact")
c = crm.app.test_client()
page = c.get("/settings?tab=safety").get_data(as_text=True)
check("Test mode has changed <b>4</b> lead(s)" in page, "Settings shows how many leads the test has changed")
ev = [e["type"] for e in team._collect_events(con, 0)]
check(not ev, "test activity is never reported to the team")

# ---- go live
r = c.post("/settings/safety", data={"redirect_to": "omar@blacswanken.com", "review": "on"}, follow_redirects=True)
check("Test results cleared: 4 lead(s)" in r.get_data(as_text=True), "switching test mode off clears the test results")
s, t, k = row("sarah@acme.com"), row("tom@globex.com"), row("kim@umbrella.com")
check(all(x["status"] == "active" and x["step"] == 1 and x["stage"] == "contacted" for x in (s, t, k)), "every lead is back as it was before testing")
check(k["company"] == "Umbrella Corp", "but edits you made yourself are kept")
o = row("old@test.com")
check(o["step"] == 0 and o["stage"] == "new" and not o["first_msg_id"], "a lead tested before this version starts live from email 1")
check(not con.execute("SELECT 1 FROM suppression WHERE email='tom@globex.com'").fetchone(), "the test unsubscribe is gone")
check(not con.execute("SELECT COUNT(*) FROM events").fetchone()[0] and not con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
      "test events and tasks are gone")

# ---- live
cfg = crm.cfg()
m = EmailMessage()
m["From"], m["Subject"], m["Message-ID"] = "Sarah <sarah@acme.com>", "Re: Quick question", "<l1>"
m.set_content("Interested. Can we talk Tuesday?")
bot.handle_message(cfg, con, sender, m)
con.commit()
check(row("sarah@acme.com")["status"] == "replied", "live replies close the lead for real")
check(not bot.test_counts(con)["leads"], "and aren't recorded as test results")
crm.write_settings({"test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
bot.sync_mode(con, crm.cfg())
crm.write_settings({"test_mode": {"enabled": False, "redirect_to": "omar@blacswanken.com"}})
bot.sync_mode(con, crm.cfg())
check(row("sarah@acme.com")["status"] == "replied" and con.execute("SELECT COUNT(*) FROM events WHERE type='reply'").fetchone()[0] == 1,
      "turning test mode on and off again never undoes live results")
print(f"\nAll {n} test-mode checks passed.")
