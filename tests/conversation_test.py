"""Conversation following, forwards, personal addresses, temperature and playbooks, the touch ledger,
the lead-file fix and the campaign drill-down lists.     python tests/conversation_test.py
"""
import json, os, sys, tempfile
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="convo-test-"))
os.environ["OUTREACH_HOME"] = str(tmp)
os.environ["BSO_NO_SIGNIN"] = "1"
sys.path.insert(0, str(root))
import bot, convo, crm  # noqa: E402

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=conversation_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

INBOX = "inbox1@getneosofttech.com"
crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [{"email": INBOX, "name": "Cole Reed", "smtp_host": "h", "imap_host": "h", "password_env": "SENDER1_PASSWORD"}],
                    "lead_intake": {"enabled": True, "inbox": INBOX, "allowed_senders": ["omar@blacswanken.com"], "verified_only": False},
                    "handover": {"enabled": False}, "test_mode": {"enabled": False}})
cfg = crm.cfg()
con = bot.db_connect(cfg.get("database", "outreach.db"))
bot.sync_mode(con, cfg)
sent = []
bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None: sent.append(dict(to=to, subject=subject, body=body, headers=headers)) or f"<out{len(sent)}@x>"
bot.notify = lambda *a, **k: None
sender = {"email": INBOX, "name": "Cole Reed"}

def lead(email, first, company, campaign=1, status="active", step=1):
    con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject, campaign_id, next_send_at) "
                "VALUES (?,?,?,?,?,?,'2026-10-01','Quick question',?, '2026-10-01')", (email, first, company, json.dumps({"title": "CTO"}), status, step, campaign))
    lid = con.execute("SELECT id FROM leads WHERE email=?", (email,)).fetchone()[0]
    con.commit()
    return lid

def our_email(lid, mid, days_ago=2, step=0):
    l = dict(con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone())
    convo.record_sent(con, mid, l, "step", INBOX, l["email"], "Quick question", f"Hi {l['first_name']}, our email.", step=step, variant="A")
    con.execute("UPDATE sent_log SET at=? WHERE msg_id=?", (bot.iso(bot.now_utc() - timedelta(days=days_ago)), mid))
    con.commit()

def incoming(frm, subject, text, mid, in_reply_to=None, cc=None, extra_headers=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, INBOX, subject, mid
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    if cc:
        m["Cc"] = cc
    for k, v in (extra_headers or {}).items():
        m[k] = v
    m.set_content(text)
    r = bot.handle_message(cfg, con, sender, m)
    con.commit()
    return r

row = lambda e: dict(con.execute("SELECT * FROM leads WHERE email=?", (e,)).fetchone())

# ---- 1. the thread decides, not the sender
abc = lead("abc@acme.com", "Abc", "Acme")
lead("other@acme.com", "Other", "Acme")
our_email(abc, "<m-abc@x>")
r = incoming("Xyz Boss <xyz@acme.com>", "Re: Quick question", "Abc passed this to me. Can we set up a call on Tuesday to discuss pricing?", "<r1@acme>",
             in_reply_to="<m-abc@x>", cc="cfo@acme.com")
check("hot" in r and row("abc@acme.com")["status"] == "replied" and row("abc@acme.com")["temperature"] == "hot",
      "a reply from someone else in the thread counts for the lead, and is hot (call + pricing)")
rel = {x["email"]: x["role"] for x in con.execute("SELECT * FROM related_contacts WHERE lead_id=?", (abc,))}
check(rel.get("xyz@acme.com") == "replied" and rel.get("cfo@acme.com") == "cc", "the person who replied and the one copied in become related contacts")
check(row("other@acme.com")["status"] == "paused", "everyone else at the company stops getting cold emails")
check(any("Respond to Abc" in t[0] and t[1] == "high" for t in con.execute("SELECT subject, priority FROM tasks")), "hot reply without a supervisor: urgent task with a deadline")
check(convo.protected(con, "cfo@acme.com"), "people in an open deal are protected from cold campaigns")

# ---- 2. personal address in the thread
p = lead("p@globex.com", "Pat", "Globex")
our_email(p, "<m-p@x>")
incoming("Pat <pat.home@gmail.com>", "Re: Quick question", "Interested, tell me more please.", "<r2@g>", in_reply_to="<m-p@x>")
a = con.execute("SELECT * FROM lead_addresses WHERE email='pat.home@gmail.com'").fetchone()
check(a and a["lead_id"] == p and a["confirmed"] == 1, "a reply from a personal address in the thread is linked to the lead")
incoming("Pat <pat.home@gmail.com>", "Another thing", "Also, do you do QA testing?", "<r3@g>")
check(con.execute("SELECT COUNT(*) FROM received WHERE lead_id=?", (p,)).fetchone()[0] == 2, "later emails from that address are tracked, even in a new thread")

# ---- 3. forwarded inside the company, decision maker writes a fresh email
f = lead("abc@initech.com", "Ann", "Initech")
our_email(f, "<m-f@x>")
body = ("Hi Cole, I'm the VP Engineering. We'd like a quote for 3 developers starting next month.\n\n"
        "---------- Forwarded message ---------\nFrom: Def <def@initech.com>\nTo: Xyz <xyz@initech.com>\n\n"
        "---------- Forwarded message ---------\nFrom: Ann <abc@initech.com>\nTo: Def <def@initech.com>\n\n"
        "-----Original Message-----\nFrom: Cole Reed <inbox1@getneosofttech.com>\nTo: abc@initech.com\nSubject: Quick question\n\nHi Ann, our email.")
r = incoming("Xyz <xyz@initech.com>", "Fw: Quick question", body, "<r4@i>")
rec = con.execute("SELECT * FROM received WHERE msg_id='<r4@i>'").fetchone()
check(rec and rec["lead_id"] == f and rec["how"] == "quoted original", "a forwarded chain is matched through our quoted original")
check(json.loads(rec["path"]) == ["abc@initech.com", "def@initech.com", "xyz@initech.com"], "and the path it travelled is rebuilt: abc → def → xyz")
check(row("abc@initech.com")["temperature"] == "hot", "a quote request is hot")

# ---- 4. reference code survives everything
g = lead("g@umbrella.com", "Gil", "Umbrella")
code = convo.ref_code(con, g, "NS")
con.commit()
incoming("Someone <ceo@umbrella.co.uk>", "Partnership", f"Saw your note (Ref: {code}). Interested, send details.", "<r5@u>")
check(con.execute("SELECT lead_id FROM received WHERE msg_id='<r5@u>'").fetchone()[0] == g, f"the reference code ({code}) links an email from anywhere")

# ---- 5. same company, nothing else: linked but flagged
h = lead("h@hooli.com", "Hal", "Hooli")
our_email(h, "<m-h@x>")
incoming("New Person <np@hooli.com>", "Hello", "Who should I talk to about outsourcing?", "<r6@h>")
check(any("Confirm: np@hooli.com" in t[0] for t in con.execute("SELECT subject FROM tasks")), "someone new from the company: linked, with a 'please confirm' task")

# ---- 6. cold / closed / out of office
c1 = lead("cold@stark.com", "Tony", "Stark")
our_email(c1, "<m-c1@x>")
incoming("tony <cold@stark.com>", "Re: Quick question", "Thanks but not right now. Maybe after Q1.", "<r7@s>", in_reply_to="<m-c1@x>")
cr = row("cold@stark.com")
check(cr["status"] == "nurture" and cr["temperature"] == "cold" and cr["check_back"] and cr["check_back"][5:] == "04-01",
      f"'not right now, after Q1' is cold: nurture, check back on {cr['check_back']}")
check(not con.execute("SELECT 1 FROM suppression WHERE email='cold@stark.com'").fetchone(), "cold isn't do-not-contact")
c2 = lead("stop@wayne.com", "Bruce", "Wayne")
incoming("bruce <stop@wayne.com>", "Re: Quick question", "Please remove me from your list.", "<r8@w>")
check(row("stop@wayne.com")["temperature"] == "closed" and con.execute("SELECT 1 FROM suppression WHERE email='stop@wayne.com'").fetchone(), "closed goes on do-not-contact")
o = lead("ooo@oscorp.com", "Norman", "Oscorp")
r = incoming("norman <ooo@oscorp.com>", "Out of office", "I'm out of the office and back on 3 December.", "<r9@o>", extra_headers={"Auto-Submitted": "auto-replied"})
check("paused until" in r and row("ooo@oscorp.com")["next_send_at"][5:10] == "12-05", "out of office: emails wait until 2 days after they're back")

# check-back email when the date comes
con.execute("UPDATE leads SET check_back=? WHERE id=?", ((date.today() - timedelta(days=1)).isoformat(), c1))
con.commit()
d, s_ = bot.nurture_tick(cfg, con, [dict(sender, password="x")], None)
draft = con.execute("SELECT * FROM outbox WHERE lead_id=? AND kind='reconnect'", (c1,)).fetchone()
check(d == 1 and draft and draft["status"] == "pending", "on the check-back date a reconnect email is drafted, waiting for review")
con.execute("UPDATE outbox SET status='approved' WHERE id=?", (draft["id"],))
con.commit()
before = len(sent)
bot.nurture_tick(cfg, con, [dict(sender, password="x")], None)
check(len(sent) == before + 1 and sent[-1]["to"] == "cold@stark.com", "once approved, it's sent")

# ---- 7. lead files vs replies (the earlier mix-up)
t1 = lead("tess@soylent.com", "Tess", "Soylent")
our_email(t1, "<m-t1@x>")
before = len(sent)
incoming("Omar <omar@blacswanken.com>", "Re: Quick question", "Sounds interesting, tell me more.", "<r10@b>", in_reply_to="<m-t1@x>")
check(row("tess@soylent.com")["status"] == "replied" and len(sent) == before, "a reply from an allowed lead-file sender is handled as a reply, not as a lead file")
r = incoming("Omar <omar@blacswanken.com>", "lunch?", "Are we still on for lunch?", "<r11@b>")
check(r.startswith("ignored") and len(sent) == before, "a normal email without a file gets no 'no lead file' answer")
r = incoming("Omar <omar@blacswanken.com>", "New leads, campaign: Main", "see attached", "<r12@b>")
check(r.startswith("lead file") and "no lead file" in sent[-1]["body"], "an email that says it's leads but has no file still gets the reminder")

# ---- 8. test mode: reply from your own address in the thread
crm.write_settings({"test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
cfg = crm.cfg()
bot.sync_mode(con, cfg)
tt = lead("kim@umbrella2.com", "Kim", "Umbrella2")
convo.record_sent(con, "<m-tt@x>", row("kim@umbrella2.com"), "step", INBOX, "omar@blacswanken.com", "[TEST for kim@umbrella2.com] Quick question", "test", test=True)
con.commit()
incoming("Omar <omar@blacswanken.com>", "Re: Quick question", "Interested! Let's talk.", "<r13@b>", in_reply_to="<m-tt@x>")
check(row("kim@umbrella2.com")["status"] == "replied", "test mode: your reply in the test thread counts for the lead (no subject tag needed)")
crm.write_settings({"test_mode": {"enabled": False, "redirect_to": "omar@blacswanken.com"}})
cfg = crm.cfg()
bot.sync_mode(con, cfg)
check(row("kim@umbrella2.com")["status"] == "active" and not con.execute("SELECT 1 FROM received WHERE msg_id='<r13@b>'").fetchone(),
      "and going live undoes it")

# ---- 9. the touch ledger: coming back, and re-imports
cb = lead("back@vandelay.com", "Art", "Vandelay")
our_email(cb, "<m-cb@x>", days_ago=120)
incoming("Art <back@vandelay.com>", "Re: Quick question", "Sorry for the delay, are you still available for a project?", "<r14@v>", in_reply_to="<m-cb@x>")
note = " ".join(x[0] for x in con.execute("SELECT body FROM notes WHERE lead_id=?", (cb,)))
check(row("back@vandelay.com")["came_back"] and "Came back 12" in note and "First reached" in note and "Main campaign" in note,
      "a reply months later is linked to the first campaign email")
con.execute("DELETE FROM leads WHERE email='cold@stark.com'")
con.commit()
nid = bot.add_lead(con, {"email": "cold@stark.com", "first_name": "Tony", "company": "Stark"})
con.commit()
nr = row("cold@stark.com")
check(nid and nr["status"] == "paused" and json.loads(nr["extra"]).get("previously_contacted"), "re-importing someone we emailed before doesn't restart the cold sequence")
check(any("Previously contacted" in x[0] for x in con.execute("SELECT subject FROM tasks WHERE lead_id=?", (nid,))), "and offers a reconnect instead")

# ---- 10. inbox activity log and the screens
check(con.execute("SELECT COUNT(*) FROM inbox_log").fetchone()[0] >= 10, "every email BSO saw is in the inbox activity log")
w = crm.app.test_client()
page = w.get("/settings?tab=inbox").get_data(as_text=True)
check("Inbox activity" in page and "Re-check the last 14 days" in page and "hot" in page, "Settings shows the activity log with a re-check button")
camp = w.get("/campaigns").get_data(as_text=True)
check("/campaigns/1/list/sent" in camp and "/campaigns/1/list/hot" in camp, "every campaign number is clickable")
lst = w.get("/campaigns/1/list/sent").get_data(as_text=True)
check("Emails sent" in lst and "openMail('/api/email/" in lst, "the sent list has a View email button per email")
j = w.get(f"/api/conversation/{abc}").get_json()
check(any(i["dir"] == "out" and "our email" in i["body"] for i in j["items"]) and any(i["dir"] == "in" and "pricing" in i["body"] for i in j["items"]),
      "the pop-up shows the email we sent and the reply")
hot = w.get("/campaigns/1/list/hot").get_data(as_text=True)
check("abc@acme.com" in hot and "abc@initech.com" in hot, "the hot list shows the hot leads")
lp = w.get(f"/lead/{abc}").get_data(as_text=True)
check("Others in the conversation" in lp and "xyz@acme.com" in lp and "Every email sent to" in lp, "the lead page shows the people and the emails")
w.post(f"/lead/{abc}/temperature", data={"temperature": "warm"})
check(row("abc@acme.com")["temperature"] == "warm" and con.execute("SELECT by FROM temp_log ORDER BY id DESC").fetchone()[0] == "person",
      "a person can change the temperature, and the change is logged")
print(f"\nAll {n} conversation checks passed.")
