"""The supervisor's mailbox inside BSO, and nothing slipping through after a handover.
   python tests/mailbox_test.py"""
import json, os, sys, tempfile
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="mailbox-test-")
os.environ["BSO_NO_SIGNIN"] = "1"
import bot, mailbox, crm  # noqa: E402,E401

n = 0
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)
    n += 1

def raw(frm, to, subject, body, cc=None, mid=None, date="Tue, 20 Oct 2026 10:00:00 +0000", headers=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    if cc:
        m["Cc"] = cc
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject, body)))}@test>"
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    return bytes(m)

crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "prospect_mailbox": {"email": "ali@blacswanken.com", "name": "Ali Khan", "smtp_host": "smtp.gmail.com", "smtp_port": 465,
                                         "imap_host": "imap.gmail.com"},
                    "test_mode": {"enabled": False}})
bot.SECRETS.set(mailbox.PW_KEY, "app-password-1234")
con = bot.db_connect("outreach.db")
con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at, stage, handed_from, prospect_stage, first_subject) "
            "VALUES ('sarah@acme.com','Sarah','Acme','replied',0,'2026-10-20','qualified','Cole Reed','new','Quick question')")
con.commit()
lid = con.execute("SELECT id FROM leads WHERE email='sarah@acme.com'").fetchone()[0]

box = [("in", raw("Cole Reed <cole.reed@getneosofttech.com>", "sarah@acme.com", "Re: Quick question", "Hi Sarah, looping in Ali.",
                  cc="ali@blacswanken.com", mid="<intro@x>", date="Tue, 20 Oct 2026 09:00:00 +0000")),
       ("in", raw("Cole Reed <cole.reed@getneosofttech.com>", "ali@blacswanken.com", "Handover: Sarah", "briefing...",
                  headers={"X-BSO-Kind": "brief", "X-BSO-Prospect": "sarah@acme.com"})),
       ("in", raw("Sarah Lee <sarah@acme.com>", "ali@blacswanken.com", "Re: Quick question",
                  "Thursday 3pm works.\n\nOn Tue, Oct 20, 2026 at 9:00 AM Ali Khan <ali@blacswanken.com> wrote:\n> earlier text", mid="<s1@acme>",
                  date="Tue, 20 Oct 2026 11:00:00 +0000")),
       ("in", raw("Someone <x@other.com>", "ali@blacswanken.com", "Lunch?", "unrelated")),
       ("sent", raw("Ali Khan <ali@blacswanken.com>", "Sarah Lee <sarah@acme.com>", "Re: Quick question", "Great, invite sent.",
                    mid="<a1@gmail>", date="Tue, 20 Oct 2026 12:00:00 +0000"))]
mailbox.fetch = lambda c, since_days=45: list(box)

new, msg = mailbox.check(crm.cfg(), con)
rows = con.execute("SELECT direction, body, seen FROM messages WHERE lead_id=? ORDER BY at", (lid,)).fetchall()
check(new == 1 and [r["direction"] for r in rows] == ["out", "in", "out"], "collects intro (CC), prospect's reply and the reply sent from Gmail; skips the briefing and unrelated mail")
check(rows[1]["body"] == "Thursday 3pm works.", "quoted history is trimmed from replies")
check(mailbox.check(crm.cfg(), con)[0] == 0 and con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3, "checking again adds nothing twice")

w = crm.app.test_client()
page = w.get("/prospects").get_data(as_text=True)
check("1 new reply" in page and "Check mailbox now" in page, "My prospects flags the new reply")
check("My prospects <span class=\"pill\">1</span>" in w.get("/").get_data(as_text=True), "menu shows the count")
lp = w.get(f"/lead/{lid}").get_data(as_text=True)
check("Thursday 3pm works." in lp and "Great, invite sent." in lp and "Send reply" in lp, "prospect page shows the whole conversation and a reply box")
check("new reply" not in w.get("/prospects").get_data(as_text=True), "opening the prospect marks the reply as read")

sent = []
bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None: sent.append(
    dict(sender=s["email"], to=to, subject=subject, body=body, in_reply_to=in_reply_to, cc=cc, headers=headers)) or "<new@bso>"
r = w.post(f"/lead/{lid}/reply", data={"body": "Hi Sarah, confirming Thursday 3pm.", "cc": ""})
s0 = sent[-1]
check(s0["sender"] == "ali@blacswanken.com" and s0["to"] == "sarah@acme.com" and s0["in_reply_to"] == "<a1@gmail>" and s0["subject"] == "Re: Quick question",
      "reply sent from Ali's mailbox, in the same thread")
lead = con.execute("SELECT prospect_stage FROM leads WHERE id=?", (lid,)).fetchone()
check(lead["prospect_stage"] == "contacted" and "confirming Thursday" in w.get(f"/lead/{lid}").get_data(as_text=True),
      "first reply moves New → Contacted and appears in the conversation")

crm.write_settings({"test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
w.post(f"/lead/{lid}/reply", data={"body": "test"})
check(sent[-1]["to"] == "omar@blacswanken.com" and sent[-1]["subject"].startswith("[TEST for sarah@acme.com]"), "test mode: replies go to the test address")
crm.write_settings({"test_mode": {"enabled": False}})

# ---- the sender's side: a handed-over prospect writes to the original inbox again
con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at, handed_to) VALUES ('tom@globex.com','Tom','Globex','replied',1,'2026-10-20','sara@blacswanken.com')")
con.commit()
m = EmailMessage()
m["From"], m["Subject"], m["Message-ID"] = "Tom <tom@globex.com>", "Re: Quick question", "<t2@globex>"
m.set_content("One more question: do you work with Django?")
cfg = crm.cfg()
sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed"}
bot.handle_message(cfg, con, sender, m)
f = sent[-1]
check(f["to"] == "sara@blacswanken.com" and "Django" in f["body"] and f["headers"]["X-BSO-Kind"] == "forward" and f["headers"]["Reply-To"] == "tom@globex.com",
      "late reply to the original inbox is forwarded to the supervisor at once")

# ...and lands in the supervisor's conversation for that prospect
con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at, handed_from, prospect_stage) VALUES ('tom2@globex.com','Tom','Globex','replied',0,'2026-10-20','Cole Reed','new')")
con.commit()
box[:] = [("in", raw("Cole Reed <cole.reed@getneosofttech.com>", "ali@blacswanken.com", "Fwd: Re: Quick question", "Tom wrote...\n\n--- Their message ---\nDo you work with Django?",
                     headers={"X-BSO-Kind": "forward", "X-BSO-Prospect": "tom2@globex.com", "Reply-To": "tom2@globex.com"}))]
mailbox.check(crm.cfg(), con)
fw = con.execute("SELECT m.direction, m.body FROM messages m JOIN leads l ON l.id=m.lead_id WHERE l.email='tom2@globex.com'").fetchone()
check(fw and fw["direction"] == "in" and fw["body"].strip() == "Do you work with Django?", "forwarded reply appears as the prospect's message in the conversation")

r = w.post("/settings/mailbox", data={"email": "ali@blacswanken.com", "name": "Ali Khan", "smtp_host": "127.0.0.1", "smtp_port": "1", "imap_host": "127.0.0.1"},
           follow_redirects=True)
check(r.status_code == 200 and b"Couldn" in r.data, "mailbox settings save and report a connection problem clearly")
check(w.get("/settings?tab=handover").status_code == 200, "Handover settings page renders")
class FakeIMAP:
    def __init__(self, lines): self.lines = lines
    def list(self): return "OK", [l.encode() for l in self.lines]
gmail = ['(\\HasNoChildren) "/" "INBOX"', '(\\HasChildren \\Noselect) "/" "[Gmail]"', '(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']
siteground = ['(\\HasNoChildren) "." INBOX', '(\\HasNoChildren \\UnMarked \\Sent) "." INBOX.Sent', '(\\HasNoChildren) "." INBOX.Drafts']
old_outlook = ['(\\HasNoChildren) "/" "Inbox"', '(\\HasNoChildren) "/" "Sent Items"']
check(mailbox._sent_folder(FakeIMAP(gmail)) == '"[Gmail]/Sent Mail"' and mailbox._sent_folder(FakeIMAP(siteground)) == "INBOX.Sent"
      and mailbox._sent_folder(FakeIMAP(old_outlook)) == '"Sent Items"', "finds the Sent folder on Gmail, SiteGround and Outlook layouts")
print(f"\nAll {n} mailbox checks passed.")
