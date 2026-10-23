"""Campaigns, up to 50 inboxes (bulk add), ramp-up, the deliverability guard and domain health.
   python tests/campaigns_test.py"""
import json, os, smtplib, sys, tempfile, types
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="campaigns-test-")
os.environ["BSO_NO_SIGNIN"] = "1"
import bot, crm  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=campaigns_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
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

inb = lambda i, **k: dict({"email": f"in{i}@getneosofttech.com", "name": f"Sender {i}", "smtp_host": "h", "smtp_port": 465, "imap_host": "h",
                           "password_env": f"SENDER{i}_PASSWORD", "daily_limit": 10}, **k)
crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [inb(1, ramp_from=date.today().isoformat(), daily_limit=30), inb(2), inb(3)],
                    "review": {"enabled": False}, "test_mode": {"enabled": False}, "delay_between_sends_seconds": [0, 0],
                    "personalization": {"enabled": False, "fallback_line": "Hello {company}."}})
for i in (1, 2, 3):
    bot.SECRETS.set(f"SENDER{i}_PASSWORD", "pw")
con = bot.db_connect("outreach.db")
w = crm.app.test_client()

# ---- campaigns
r = w.post("/campaigns/new", data={"name": "UK SaaS", "copy_from": "1"}, follow_redirects=True)
c2 = con.execute("SELECT * FROM campaigns WHERE name='UK SaaS'").fetchone()
check(c2 and c2["status"] == "paused" and len(json.loads(c2["sequence"])) == len(crm.cfg()["sequence"]), "new campaign starts paused with a copy of the emails")
form = {"action": "save", "name": "UK SaaS", "inboxes": ["in3@getneosofttech.com"], "offer": "UK-focused offer",
        "delay0": "0", "subject0": "UK hello {first_name}", "body0": "Hi {first_name},\n\nUK body.", "delay1": "3", "subject1": "", "body1": "Following up."}
w.post(f"/campaign/{c2['id']}", data=form)
w.post(f"/campaign/{c2['id']}", data={**form, "action": "add_step"})
seq = json.loads(con.execute("SELECT sequence FROM campaigns WHERE id=?", (c2["id"],)).fetchone()[0])
check(len(seq) == 3 and seq[0]["variants"][0]["subject"] == "UK hello {first_name}" and seq[1]["delay_days"] == 3, "campaign emails edited; a follow-up added")
w.post(f"/campaign/{c2['id']}", data={**form, "remove2": "on", "body2": "x", "delay2": "4", "subject2": ""})
check(len(json.loads(con.execute("SELECT sequence FROM campaigns WHERE id=?", (c2["id"],)).fetchone()[0])) == 2, "a follow-up can be removed")
w.post(f"/campaign/{c2['id']}", data={"action": "resume"})
r = w.post("/campaigns/new", data={"name": "Paused one", "copy_from": "1"}, follow_redirects=True)
c3 = con.execute("SELECT id FROM campaigns WHERE name='Paused one'").fetchone()[0]
for email_, cid in (("ann@acme.com", 1), ("bob@uk.co", c2["id"]), ("cat@later.com", c3)):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id) VALUES (?,?,?,'active',0,'2026-01-01','2026-01-01',?)",
                (email_, email_.split("@")[0].title(), "Co", cid))
con.commit()

sent = []
def fake_send(s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None, key=None):
    if s["email"] in fail_for:
        raise smtplib.SMTPAuthenticationError(535, b"bad login")
    sent.append(dict(frm=s["email"], to=to, subject=subject, body=body))
    return f"<{len(sent)}@x>"
fail_for = set()
bot.send_email = fake_send
bot.notify = lambda *a, **k: None
args = types.SimpleNamespace(dry_run=False, force=True, no_check=True, limit=50)
def run():
    try:
        bot.cmd_run(bot.load_config(crm.CONFIG_PATH), con, args)
    except SystemExit:
        pass
run()
by_to = {s["to"]: s for s in sent}
check("ann@acme.com" in by_to and "bob@uk.co" in by_to and "cat@later.com" not in by_to, "running campaigns send; the paused campaign waits")
check(by_to["bob@uk.co"]["subject"] == "UK hello Bob" and "UK body." in by_to["bob@uk.co"]["body"] and by_to["bob@uk.co"]["frm"] == "in3@getneosofttech.com",
      "each campaign uses its own emails and only its own inboxes")
check(by_to["ann@acme.com"]["frm"] in ("in1@getneosofttech.com", "in2@getneosofttech.com") and "UK" not in by_to["ann@acme.com"]["subject"], "the Main campaign keeps its own emails")
page = w.get("/campaigns").get_data(as_text=True)
check("UK SaaS" in page and "Paused" in page and "Running" in page, "Campaigns page lists campaigns with their status")

# ---- moving a lead and deleting a campaign
lid = con.execute("SELECT id FROM leads WHERE email='cat@later.com'").fetchone()[0]
w.post(f"/lead/{lid}/campaign", data={"campaign_id": c2["id"]})
check(con.execute("SELECT campaign_id FROM leads WHERE id=?", (lid,)).fetchone()[0] == c2["id"], "a lead can be moved to another campaign")
w.post(f"/campaign/{c3}", data={"action": "delete"})
check(not con.execute("SELECT 1 FROM campaigns WHERE id=?", (c3,)).fetchone(), "a campaign can be deleted (its leads go to Main)")

# ---- emailed lead file into a campaign named in the subject
cfg = bot.load_config(crm.CONFIG_PATH)
cfg["lead_intake"] = {"enabled": True, "inbox": "in1@getneosofttech.com", "allowed_senders": ["omar@blacswanken.com"], "verified_only": False}
m = EmailMessage()
m["From"], m["Subject"], m["Message-ID"] = "omar@blacswanken.com", "New leads, campaign: uk saas", "<intake1>"
m.set_content("see file")
m.add_attachment(b"Email,First Name\ndee@uk.co,Dee\n", maintype="text", subtype="csv", filename="uk.csv")
bot.handle_message(cfg, con, {"email": "in1@getneosofttech.com", "name": "S1"}, m)
check(con.execute("SELECT campaign_id FROM leads WHERE email='dee@uk.co'").fetchone()[0] == c2["id"] and 'campaign "UK SaaS"' in sent[-1]["body"],
      "'campaign: name' in the subject puts emailed leads into that campaign")

# ---- ramp-up
s1 = crm.cfg()["senders"][0]
check(bot.ramp_limit(crm.cfg(), s1) == 5 and bot.ramp_limit(crm.cfg(), s1, date.today() + timedelta(days=21)) == 20
      and bot.ramp_limit(crm.cfg(), s1, date.today() + timedelta(days=70)) == 30, "new inbox ramps 5/day, +5 a week, up to its limit")

# ---- deliverability guard
for i in range(20):
    bot.log_event(con, 1, "sent", "step 1", "in2@getneosofttech.com")
for i in range(2):
    bot.log_event(con, 1, "bounce", "", "in2@getneosofttech.com")
con.commit()
notes = bot.guard_check(bot.load_config(crm.CONFIG_PATH), con, bot.load_senders(bot.load_config(crm.CONFIG_PATH)))
check(not notes and "in2@getneosofttech.com" not in bot.guard_paused(con), "2 bounces in 20 emails is too few to judge (it waits for 50)")
for i in range(30):
    bot.log_event(con, 1, "sent", "step 1", "in2@getneosofttech.com")
bot.log_event(con, 1, "bounce", "", "in2@getneosofttech.com")
con.commit()
notes = bot.guard_check(bot.load_config(crm.CONFIG_PATH), con, bot.load_senders(bot.load_config(crm.CONFIG_PATH)))
check(any("in2@" in x for x in notes) and "in2@getneosofttech.com" in bot.guard_paused(con), "an inbox with 6% bounces over 50 emails is paused automatically")
con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id) VALUES ('eve@x.com','Eve','X','active',0,'2026-01-01','2026-01-01',1)")
con.commit()
before = len(sent)
run()
check(all(s["frm"] != "in2@getneosofttech.com" for s in sent[before:]), "a paused inbox sends nothing")
check("paused" in w.get("/settings?tab=inbox").get_data(as_text=True), "Settings shows the paused inbox")
w.post("/settings/inboxes/resume", data={"email": "in2@getneosofttech.com"})
check("in2@getneosofttech.com" not in bot.guard_paused(con) and not bot.guard_check(bot.load_config(crm.CONFIG_PATH), con, bot.load_senders(bot.load_config(crm.CONFIG_PATH))),
      "resuming counts bounces again from now, so it isn't paused straight back")
fail_for = {"in3@getneosofttech.com"}
for _ in range(3):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id) VALUES (?, 'X','X','active',0,'2026-01-01','2026-01-01',?)",
                (f"x{_}{len(sent)}@uk.co", c2["id"]))
    con.commit()
    run()
check("in3@getneosofttech.com" in bot.guard_paused(con), "an inbox whose login fails 3 runs in a row is paused")

# ---- many inboxes
lines = "\n".join(f"bulk{i}@getneosofttech.com, Bulk {i}, pw{i}" for i in range(1, 13)) + "\nin1@getneosofttech.com, Dup, x\nnot-an-email"
r = w.post("/settings/inboxes/bulk", data={"lines": lines, "smtp_host": "mail.getneosofttech.com", "smtp_port": "465", "imap_host": "mail.getneosofttech.com",
                                           "daily_limit": "15", "ramp": "on"}, follow_redirects=True)
snd = crm.cfg()["senders"]
check(len(snd) == 15 and "Added 12 inbox" in r.get_data(as_text=True) and "updated 1" in r.get_data(as_text=True) and "expected email" in r.get_data(as_text=True),
      "12 inboxes added in one go; an existing one is updated; bad lines reported")
check(bot.env(snd[-1]["password_env"]) == "pw12" and snd[-1]["ramp_from"] == date.today().isoformat() and len({s["password_env"] for s in snd}) == 15,
      "each new inbox has its own encrypted password and ramps up")
page = w.get("/settings?tab=inbox").get_data(as_text=True)
check("Inbox 15: bulk12@getneosofttech.com" in page and 'name="email16"' in page, "Settings shows all 15 inboxes compactly, with room for more (up to 50)")
form = {}
for i, sv in enumerate(snd, 1):
    form.update({f"provider{i}": "other", f"email{i}": sv["email"], f"name{i}": sv["name"], f"smtp_host{i}": sv["smtp_host"], f"smtp_port{i}": "465",
                 f"imap_host{i}": sv["imap_host"], f"daily_limit{i}": str(sv["daily_limit"])})
    if sv.get("ramp_from"):
        form.update({f"ramp{i}": "on", f"ramp_from{i}": sv["ramp_from"]})
w.post("/settings/inbox", data=form)
check(len(crm.cfg()["senders"]) == 15 and crm.cfg()["senders"][14]["ramp_from"] == date.today().isoformat(), "saving the inbox page keeps all 15 and their ramp dates")
cp = w.get(f"/campaign/{c2['id']}").get_data(as_text=True)
check("bulk12@getneosofttech.com" in cp, "a campaign can pick from all the inboxes")

# ---- domain health
import health
records = {("getneosofttech.com", "TXT"): ["v=spf1 include:_spf.mailspamprotection.com ~all"], ("_dmarc.getneosofttech.com", "TXT"): [],
           ("default._domainkey.getneosofttech.com", "TXT"): ["v=DKIM1; k=rsa; p=MIGf..."], ("getneosofttech.com", "MX"): ["10 mx10.antispam.mailspamprotection.com."]}
health._txt = lambda name, kind="TXT": records.get((name, kind), [])
r = w.post("/settings/domains/check", follow_redirects=True).get_data(as_text=True)
res = json.loads(con.execute("SELECT result FROM domain_health WHERE domain='getneosofttech.com'").fetchone()[0])
check(res["spf"][0] and res["dkim"][0] and res["mx"][0] and not res["dmarc"][0] and res["score"] == 3, "domain check finds SPF, DKIM (selector default) and MX, and the missing DMARC")
check("_dmarc.getneosofttech.com" in r and "Problems on: getneosofttech.com" in r, "Settings shows the problem and exactly what record to add")
print(f"\nAll {n} campaign/inbox checks passed.")
