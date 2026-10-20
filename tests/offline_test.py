"""Nobody depends on anybody being online: the master console is closed after setup, the sender and the
supervisors are never online at the same time, and a supervisor moves to a brand-new laptop.
Runs relay/Code.gs in the local Google stand-in (needs Node.js).     python tests/offline_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
PORT = 8795
URL = f"http://127.0.0.1:{PORT}/exec"

if len(sys.argv) > 1 and sys.argv[1] == "--laptop":
    # ---- one person's laptop, started fresh each time ("comes online"), then closed ----------------
    home, user, pw, task = sys.argv[2:6]
    os.environ["OUTREACH_HOME"] = home
    sys.path.insert(0, str(root))
    import bot, team, crm  # noqa: E401
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, user, pw, consent=True)        # signing in is all it takes
    out = {}
    if task == "stage":
        lid = con.execute("SELECT id FROM leads WHERE email='sarah@acme.com'").fetchone()[0]
        crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}})
        crm.app.test_client().post(f"/prospects/{lid}/stage", data={"stage": "meeting"})
        team.sync(con)
    out["prospects"] = {r["email"]: r["prospect_stage"] for r in con.execute("SELECT email, prospect_stage FROM leads WHERE handed_from IS NOT NULL")}
    out["notes"] = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    out["tasks"] = [r[0] for r in con.execute("SELECT subject FROM tasks WHERE status='open'")]
    print("RESULT " + json.dumps(out))
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="offline-test-"))
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

def laptop(home, user, pw, task="check"):
    r = subprocess.run([sys.executable, __file__, "--laptop", str(tmp / home), user, pw, task], capture_output=True, text=True, timeout=180)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        print(r.stdout[-1500:], r.stderr[-3000:])
        check(False, f"{user}'s laptop ran")
    return json.loads(line[7:])

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
    # ---- the master sets the team up once, then closes the console for the rest of the test
    c = hub.app.test_client()
    tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    for name, user, email, role in (("Cole Reed", "cole.reed", "cole.reed@getneosofttech.com", "member"),
                                    ("Ali Khan", "ali", "ali@blacswanken.com", "supervisor"),
                                    ("Sara Malik", "sara", "sara@blacswanken.com", "supervisor")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    r = c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    check(b"Connected" in r.data, "master connects the Google relay")
    check(b"Update your Google script" not in c.get("/settings").data, "console sees the relay is the latest version")
    MASTER_OFFLINE = True          # from here on: no relay_cycle() until the very end

    # ---- the sender: online alone
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234", consent=True)
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    cfg["test_mode"] = {"enabled": False}
    sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed", "password": "x", "smtp_host": "h", "imap_host": "h"}
    sent = []
    bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None: sent.append(dict(to=to, cc=cc, subject=subject, body=body)) or "<x>"
    bot.notify = lambda *a, **k: None
    for i, (email, first, comp) in enumerate((("sarah@acme.com", "Sarah", "Acme"), ("tom@globex.com", "Tom", "Globex"), ("ann@initech.com", "Ann", "Initech")), 1):
        con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject) VALUES (?,?,?,?,'active',1,'2026-10-01','Quick question')",
                    (email, first, comp, json.dumps({"title": "CTO"})))
    con.commit()
    def reply(frm, text):
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = frm, "Re: Quick question", f"<r-{frm}>"
        m.set_content(text)
        bot.handle_message(cfg, con, sender, m)
    reply("sarah@acme.com", "Sounds interesting, tell me more.")
    reply("tom@globex.com", "Can we set up a call next week?")
    ccs = [s["cc"] for s in sent if s["cc"]]
    check(sorted(ccs) == ["ali@blacswanken.com", "sara@blacswanken.com"], "supervisors take turns, decided by Google while the master is offline")
    check(team.sync(con)[0], "sender's report goes to Google; sender closes the laptop")
    reply("tom@globex.com", "Can we set up a call next week?")       # the same prospect again
    check(len([s for s in sent if s["cc"]]) == 2, "a prospect is never handed over twice")

    # ---- Ali comes online alone, much later
    a1 = laptop("ali-laptop", "ali", "ali-pass-1234", "stage")
    check(a1["prospects"] == {"sarah@acme.com": "meeting"}, "Ali signs in: his prospect is there (master and sender offline); he sets Meeting booked")
    check(any("Contact Sarah" in t for t in a1["tasks"]), "Ali got his 'contact' task")

    # ---- Ali signs in on a brand-new laptop
    a2 = laptop("ali-new-laptop", "ali", "ali-pass-1234")
    check(a2["prospects"] == {"sarah@acme.com": "meeting"}, "new laptop: Ali's prospect comes back with its stage")
    check(not any("Contact Sarah" in t for t in a2["tasks"]), "no stale 'contact' task for a prospect already in a meeting")
    a3 = laptop("ali-new-laptop", "ali", "ali-pass-1234")
    check(a3["prospects"] == {"sarah@acme.com": "meeting"} and a3["notes"] == a2["notes"], "signing in again changes nothing (no duplicates)")

    # ---- Sara comes online alone
    s1 = laptop("sara-laptop", "sara", "sara-pass-1234")
    check(s1["prospects"] == {"tom@globex.com": "new"}, "Sara signs in: Tom is waiting for her")

    # ---- the master opens the console at the end and everything is there
    hub.relay_cycle()
    page = c.get("/handovers").get_data(as_text=True)
    sarah_row = page.split("sarah@acme.com")[0].rsplit("<tr>", 1)[-1] + page.split("sarah@acme.com")[1].split("</tr>")[0]
    check("Ali Khan" in sarah_row and "meeting" in sarah_row and "waiting for their BSO" not in sarah_row,
          "master comes online later: sees Sarah with Ali, delivered, Meeting booked")
    tom_row = page.split("tom@globex.com")[1].split("</tr>")[0]
    check("Sara Malik" in tom_row and "waiting for their BSO" not in tom_row, "and Tom with Sara, delivered")
    print(f"\nAll {n} offline checks passed.")
finally:
    node.terminate()
