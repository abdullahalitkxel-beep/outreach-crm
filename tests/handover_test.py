"""End-to-end test of handing interested prospects to lead gen supervisors.
A sender's laptop, a supervisor's laptop (separate process), the master console and the Google relay
stand-in (needs Node.js).     python tests/handover_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="handover-test-"))
PORT = 8794
URL = f"http://127.0.0.1:{PORT}/exec"

if len(sys.argv) > 1 and sys.argv[1] == "--supervisor":
    # ---- the supervisor's own laptop -------------------------------------------------------------
    os.environ["OUTREACH_HOME"] = sys.argv[2]
    sys.path.insert(0, str(root))
    import bot, team, crm  # noqa: E401
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, sys.argv[3], sys.argv[4], consent=True)
    team.sync(con)
    team.sync(con)                      # first sync gets the handover; second acknowledges it
    out = {"prospects": [dict(r) for r in con.execute("SELECT email, first_name, company, handed_from, prospect_stage, status FROM leads WHERE handed_from IS NOT NULL")],
           "notes": [r[0] for r in con.execute("SELECT body FROM notes")],
           "tasks": [r[0] for r in con.execute("SELECT subject FROM tasks WHERE status='open'")]}
    crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}})
    w = crm.app.test_client()
    page = w.get("/prospects?view=list").get_data(as_text=True)
    out["page_ok"] = "My prospects" in page and "sarah@acme.com" in page
    if out["prospects"]:
        lid = con.execute("SELECT id FROM leads WHERE email='sarah@acme.com'").fetchone()[0]
        w.post(f"/prospects/{lid}/stage", data={"stage": "meeting"})
        out["stage_after"] = con.execute("SELECT prospect_stage FROM leads WHERE id=?", (lid,)).fetchone()[0]
        out["lead_page"] = "Handed over to you" in w.get(f"/lead/{lid}").get_data(as_text=True)
    out["sync_after_stage"] = team.sync(con)[0]
    print("RESULT " + json.dumps(out))
    sys.exit(0)

# ---- master console + the sender's laptop (this process) ------------------------------------------
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = str(tmp / "sender")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "sender").mkdir()
import hub, bot, team, relay  # noqa: E402,E401

n = 0
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)
    n += 1

hub.prepare(str(tmp / "hub"))
key = hub.relay_key(create=True)
node = subprocess.Popen(["node", str(root / "relay" / "test_double.js"), str(PORT), hashlib.sha256(key.encode()).hexdigest()])
try:
    for _ in range(50):
        try:
            relay.call(URL, {"action": "ping"}, timeout=2)
            break
        except relay.RelayError:
            time.sleep(0.2)
    c = hub.app.test_client()
    tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    for name, user, email, role in (("Cole Reed", "cole.reed", "cole.reed@getneosofttech.com", "member"),
                                    ("Ali Khan", "ali", "ali@blacswanken.com", "supervisor"),
                                    ("Sara Malik", "sara", "sara@blacswanken.com", "supervisor")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    _u = c.get("/users").get_data(as_text=True)
    check(_u.count("<td>supervisor</td>") >= 2, "two Lead Gen Supervisors created")
    c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})

    # sender's laptop
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234", consent=True)
    team.sync(con)
    check([s["name"] for s in team.supervisors()] == ["Ali Khan", "Sara Malik"], "sender's laptop knows the supervisors")
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    cfg["test_mode"] = {"enabled": False}
    sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed", "password": "x", "smtp_host": "h", "imap_host": "h"}
    sent = []
    bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None, key=None: sent.append(
        dict(to=to, subject=subject, body=body, in_reply_to=in_reply_to, cc=cc)) or "<id@x>"
    bot.notify = lambda *a, **k: None
    for i, (email, first, comp) in enumerate((("sarah@acme.com", "Sarah", "Acme Inc"), ("tom@globex.com", "Tom", "Globex"),
                                              ("ann@initech.com", "Ann", "Initech")), 1):
        con.execute("INSERT INTO leads(email, first_name, company, website, extra, status, step, created_at, first_subject, first_msg_id) "
                    "VALUES (?,?,?,?,?,'active',1,'2026-10-01',?,?)",
                    (email, first, comp, comp.split()[0].lower() + ".com", json.dumps({"title": "CTO", "last_name": "Lee"}), "Quick question", f"<m{i}@x>"))
        lid = con.execute("SELECT id FROM leads WHERE email=?", (email,)).fetchone()[0]
        con.execute("INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at, sent_at) VALUES (?,0,?,?,?,'sent','2026-10-01','2026-10-01')",
                    (lid, sender["email"], "Quick question", f"Hi {first}, our first email."))
    con.commit()

    def reply(frm, text, subject="Re: Quick question"):
        m = EmailMessage()
        addr = re.search(r"[\w.+-]+@[\w.-]+", frm).group(0)
        m["From"], m["Subject"], m["Message-ID"] = frm, subject, f"<r-{addr}>"
        m.set_content(text)
        bot.handle_message(cfg, con, sender, m)

    reply("Sarah Lee <sarah@acme.com>", "Sounds interesting. Can we set up a quick call next week?")
    intro = next((s for s in sent if s["to"] == "sarah@acme.com"), None)
    check(intro and intro["cc"] == "ali@blacswanken.com" and intro["in_reply_to"] == "<r-sarah@acme.com>" and intro["subject"] == "Re: Quick question",
          "warm intro sent in the same thread, supervisor in CC")
    check("Ali Khan" in intro["body"] and "Hi Sarah" in intro["body"] and "Cole Reed" in intro["body"], "intro names the supervisor and is signed by the sender")
    brief = next((s for s in sent if s["to"] == "ali@blacswanken.com"), None)
    check(brief and "can we set up a quick call" in brief["body"].lower() and "our first email" in brief["body"] and "sarah@acme.com" in brief["body"],
          "supervisor gets a briefing with the reply and the conversation")
    lead = con.execute("SELECT * FROM leads WHERE email='sarah@acme.com'").fetchone()
    check(lead["handed_to"] == "ali@blacswanken.com" and lead["status"] == "replied" and lead["stage"] == "qualified", "lead marked handed over; sequence stopped")
    check(not con.execute("SELECT 1 FROM tasks WHERE subject LIKE 'Respond to%'").fetchone(), "no 'respond' task left for the sender")

    reply("tom@globex.com", "How much does a dedicated developer cost per month?")
    check(any(s["to"] == "tom@globex.com" and s["cc"] == "sara@blacswanken.com" for s in sent), "next prospect goes to the other supervisor (taking turns)")
    reply("ann@initech.com", "Not interested, thanks.")
    check(not any(s["to"] == "ann@initech.com" for s in sent), "not-interested replies are not handed over")

    # test mode: everything to the test address
    cfg["test_mode"] = {"enabled": True, "redirect_to": "omar@blacswanken.com"}
    con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at) VALUES ('kim@umbrella.com','Kim','Umbrella','active',1,'2026-10-01')")
    con.commit()
    before = len(sent)
    reply("kim@umbrella.com", "Interested, tell me more.")
    new = sent[before:]
    check(new and all(s["to"] == "omar@blacswanken.com" and not s["cc"] for s in new) and "CC" in new[0]["subject"], "test mode: intro and briefing go only to the test address")

    # up to the master console
    ok, msg = team.sync(con)
    check(ok, "sender's laptop reports the handovers")
    hub.relay_cycle()
    page = c.get("/handovers").get_data(as_text=True)
    check("Sarah Lee" in page and "Ali Khan" in page and "Sara Malik" in page and "meeting request" in page, "Handovers page lists them with supervisor and reply type")
    check("Prospects handed over" in c.get("/").get_data(as_text=True), "overview shows handovers")

    # the supervisor's laptop receives it
    r = subprocess.run([sys.executable, __file__, "--supervisor", str(tmp / "ali"), "ali", "ali-pass-1234"], capture_output=True, text=True, timeout=120)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    check(line is not None, "supervisor laptop ran" + ("" if line else ": " + r.stdout[-800:] + r.stderr[-1500:]))
    res = json.loads(line[7:])
    p = {x["email"]: x for x in res["prospects"]}
    check(set(p) == {"sarah@acme.com"} and p["sarah@acme.com"]["handed_from"] == "Cole Reed" and p["sarah@acme.com"]["status"] == "replied",
          "Ali's prospects appear in his BSO, handed over by Cole Reed (Tom went to Sara; the test-mode one stays on the sender's computer)")
    check(sum("Sounds interesting. Can we set up" in x for x in res["notes"]) == 1 and sum("Contact Sarah" in t for t in res["tasks"]) == 1,
          "each prospect arrives once, even if it's delivered twice")
    check(any("Can we set up a quick call" in x and "our first email" in x for x in res["notes"]), "supervisor sees the reply and the whole conversation")
    check(any("Contact Sarah" in t for t in res["tasks"]), "supervisor gets a 'contact' task")
    check(res["page_ok"] and res["lead_page"], "My prospects page and the prospect page work")
    check(res["stage_after"] == "meeting" and res["sync_after_stage"], "supervisor sets the stage to Meeting booked and it's reported")
    hub.relay_cycle()
    page = c.get("/handovers").get_data(as_text=True)
    check("meeting" in page and "waiting for their BSO" not in page.split("Sarah Lee")[1].split("</tr>")[0], "console shows the supervisor's stage and that it was delivered")
    print(f"\nAll {n} handover checks passed.")
finally:
    node.terminate()
