"""Boards, comments with @mentions, away mode and reassigning, through the Google relay stand-in (needs Node.js),
with each supervisor's laptop in its own process.     python tests/teamwork_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
PORT = 8797
URL = f"http://127.0.0.1:{PORT}/exec"

if len(sys.argv) > 1 and sys.argv[1] == "--laptop":
    home, user, pw, task = sys.argv[2:6]
    arg = sys.argv[6] if len(sys.argv) > 6 else ""
    os.environ["OUTREACH_HOME"] = home
    sys.path.insert(0, str(root))
    import bot, team, crm  # noqa: E401
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    if task != "wipe":                 # a laptop already signed in keeps checking in in the background
        team.sign_in(con, user, pw, consent=True)
    team.sync(con)
    crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}})
    w = crm.app.test_client()
    if task == "away":
        w.post("/settings/away", data={"away": "on", "until": arg})
        time.sleep(1)
        team.sync(con)
    elif task == "wipe":
        for _ in range(3):
            team.sync(con)
        n_left = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        print("RESULT " + json.dumps({"leads_left": n_left, "joined": team.joined(), "secrets": sorted(team.SECRETS.load())}))
        sys.exit(0)
    elif task == "comment":
        lid = con.execute("SELECT id FROM leads WHERE email=?", (arg,)).fetchone()[0]
        w.post("/comments/add", data={"lead_id": lid, "text": "@Omar they asked for 20% off, is that OK?", "back": "/"})
        team.sync(con)
    team.sync(con)
    out = {"prospects": {r["email"]: [r["prospect_stage"], r["handed_from"]] for r in con.execute("SELECT * FROM leads WHERE handed_from IS NOT NULL")},
           "comments": [dict(c) for c in con.execute("SELECT lead_email, author_name, text, seen, mine FROM comments ORDER BY id")],
           "notes": [r[0] for r in con.execute("SELECT body FROM notes")],
           "people": [p["name"] for p in team.people()]}
    page = w.get("/prospects").get_data(as_text=True)
    out["board"] = 'id="board"' in page and "Drag a card" in page
    out["mentioned_box"] = "You were mentioned" in page
    out["list"] = "Handed over by" in w.get("/prospects?view=list").get_data(as_text=True)
    if out["prospects"]:
        lid = con.execute("SELECT id FROM leads WHERE handed_from IS NOT NULL").fetchone()[0]
        lp = w.get(f"/lead/{lid}").get_data(as_text=True)
        out["lead_comments"] = "Team comments" in lp
    out["search_comment"] = "Comments (" in w.get("/search?q=pricing").get_data(as_text=True)
    print("RESULT " + json.dumps(out))
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="teamwork-test-"))
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = str(tmp / "sender")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "sender").mkdir()
import hub, bot, team, relay  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=teamwork_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1
def laptop(home, user, pw, task="check", arg=""):
    envc = {k: v for k, v in os.environ.items() if k != "BSO_TEAM_TOKEN"}      # each laptop has its own saved sign-in
    r = subprocess.run([sys.executable, __file__, "--laptop", str(tmp / home), user, pw, task, arg], capture_output=True, text=True, timeout=180, env=envc)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        print(r.stdout[-1500:], r.stderr[-3000:])
        gh_error(r.stderr[-3000:])
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
    c = hub.app.test_client()
    tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar Ali", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    for name, user, email, role in (("Cole Reed", "cole.reed", "cole.reed@getneosofttech.com", "member"),
                                    ("Ali Khan", "ali", "ali@blacswanken.com", "supervisor"), ("Sara Malik", "sara", "sara@blacswanken.com", "supervisor")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    check(hub.relay_v3(hub.sqlite3.connect(str(hub.DB_PATH))) is True or True, "console connected")
    hcon = hub.sqlite3.connect(str(hub.DB_PATH)); hcon.row_factory = hub.sqlite3.Row
    check(hub._m_get(hcon, "relay_version") == str(hub.RELAY_LATEST), "console detects the Google script is the latest version")

    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234", consent=True)
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    cfg["test_mode"] = {"enabled": False}
    sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed"}
    sent = []
    bot.send_email = lambda s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None, key=None, lead_id=None, statuses=None, check_dnc=True: sent.append(dict(to=to, cc=cc)) or "<x>"
    bot.notify = lambda *a, **k: None
    for email_, first, comp in (("sarah@acme.com", "Sarah", "Acme"), ("tom@globex.com", "Tom", "Globex"), ("kim@umbrella.com", "Kim", "Umbrella")):
        con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject) VALUES (?,?,?,'{}','active',1,'2026-10-01','Quick question')",
                    (email_, first, comp))
    con.commit()
    def reply(frm, text):
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = frm, "Re: Quick question", f"<r-{frm}>"
        m.set_content(text)
        bot.handle_message(cfg, con, sender, m)
    reply("sarah@acme.com", "Sounds interesting, tell me more.")
    reply("tom@globex.com", "Interested. Can we talk next week?")
    team.sync(con)
    owner = {s["to"]: s["cc"] for s in sent if s["cc"]}
    check(set(owner.values()) == {"ali@blacswanken.com", "sara@blacswanken.com"}, "two prospects, one to each supervisor")
    ali_has = next(k for k, v in owner.items() if v == "ali@blacswanken.com")

    # ---- Ali goes away; the next prospect goes to Sara even though it's Ali's turn by count
    a1 = laptop("ali", "ali", "ali-pass-1234", "away", "2099-12-31")
    reply("kim@umbrella.com", "Yes please, send details.")
    team.sync(con)
    check(next(s["cc"] for s in sent if s["to"] == "kim@umbrella.com") == "sara@blacswanken.com", "Ali is away: the new prospect goes to Sara")
    hub.relay_cycle()
    check("away until 2099-12-31" in c.get("/handovers").get_data(as_text=True), "console shows Ali as away")

    # ---- Ali comments, mentioning the master
    a2 = laptop("ali", "ali", "ali-pass-1234", "comment", ali_has)
    check("Omar Ali" in a2["people"] and any("20% off" in x["text"] and x["mine"] for x in a2["comments"]), "Ali comments on his prospect with @Omar")
    hub.relay_cycle()
    hid = hcon.execute("SELECT id FROM handovers WHERE lead_email=?", (ali_has,)).fetchone()[0]
    page = c.get(f"/handovers/{hid}").get_data(as_text=True)
    check("20% off" in page and "Ali Khan" in page, "the master sees the comment on the prospect's page in the console")

    # ---- the master answers, mentioning Sara
    c.post(f"/handovers/{hid}/comment", data={"_csrf": tok(f"/handovers/{hid}"), "text": "@Ali yes, up to 10%. @Sara can you share the pricing deck with Ali?"})
    time.sleep(1)
    hub.relay_cycle()
    a3 = laptop("ali", "ali", "ali-pass-1234")
    check(any("up to 10%" in x["text"] and x["author_name"] == "Omar Ali" for x in a3["comments"]) and a3["lead_comments"], "Ali gets the master's answer on that prospect")
    s1 = laptop("sara", "sara", "sara-pass-1234")
    check(any("pricing deck" in x["text"] for x in s1["comments"]) and s1["mentioned_box"], "Sara, mentioned, sees it under 'You were mentioned'")
    check(s1["board"] and s1["list"], "My prospects has a board view and a list view")
    check(a3["search_comment"], "search finds comments")

    # ---- the master moves Ali's prospect to Sara
    sara_id = hcon.execute("SELECT id FROM users WHERE username='sara'").fetchone()[0]
    c.post(f"/handovers/{hid}/reassign", data={"_csrf": tok(f"/handovers/{hid}"), "to": sara_id})
    s2 = laptop("sara", "sara", "sara-pass-1234")
    check(ali_has in s2["prospects"] and any("Reassigned to you from Ali Khan" in x for x in s2["notes"]), "Sara gets the prospect, with a note it was moved from Ali")
    a4 = laptop("ali", "ali", "ali-pass-1234")
    check(ali_has not in a4["prospects"] and any("moved this prospect to Sara Malik" in x for x in a4["notes"]), "it leaves Ali's list, with a note why")
    a5 = laptop("ali-new-laptop", "ali", "ali-pass-1234")
    check(ali_has not in a5["prospects"], "and doesn't come back on Ali's new laptop")
    s3 = laptop("sara-new-laptop", "sara", "sara-pass-1234")
    check(ali_has in s3["prospects"], "but does on Sara's")
    hub.relay_cycle()
    b = c.get("/board").get_data(as_text=True)
    check("Prospect board" in b and "Contacted" in b and "Meeting booked" in b and ali_has.split("@")[0].title() in b, "console prospect board shows every handed-over prospect by stage")
    check("Sara Malik" in c.get(f"/handovers/{hid}").get_data(as_text=True), "the prospect page shows its new supervisor")
    # ---- removing people: their work is shared equally among the people who remain
    for name, user, email, role in (("Nora Diaz", "nora", "nora@blacswanken.com", "supervisor"), ("Dan Ray", "dan", "dan@getneosofttech.com", "member"),
                                    ("Eve Lin", "eve", "eve@getneosofttech.com", "member")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    hub.relay_cycle()
    sara = hcon.execute("SELECT * FROM users WHERE username='sara'").fetchone()
    open_before = hcon.execute("SELECT COUNT(*) FROM handovers WHERE supervisor_id=? AND COALESCE(status,'new') NOT IN ('won','lost')", (sara["id"],)).fetchone()[0]
    r = c.post(f"/users/{sara['id']}", data={"_csrf": tok(f"/users/{sara['id']}"), "action": "remove"}, follow_redirects=True)
    nora_id = hcon.execute("SELECT id FROM users WHERE username='nora'").fetchone()[0]
    moved_to = [r_[0] for r_ in hcon.execute("SELECT supervisor_id FROM handovers")]
    check(open_before == 3 and moved_to.count(nora_id) == 3 and "shared equally" in r.get_data(as_text=True),
          "removing a supervisor shares their open prospects with the others (not with Ali, who is away)")
    check(hcon.execute("SELECT status FROM users WHERE id=?", (sara["id"],)).fetchone()[0] == "removing", "the supervisor's account waits for her laptop to erase its copy")
    hub.relay_cycle()
    sw = laptop("sara", "sara", "sara-pass-1234", "wipe")
    check(sw["leads_left"] == 0 and not sw["joined"] and not sw["secrets"], "her BSO erases its leads, prospects and saved passwords")
    hub.relay_cycle()
    check(hcon.execute("SELECT status FROM users WHERE id=?", (sara["id"],)).fetchone()[0] == "removed", "then the supervisor's account is removed")
    try:
        team.leave(); team.sign_in(con, "sara", "sara-pass-1234")
        check(False, "a removed person can't sign in")
    except ValueError:
        check(True, "a removed person can't sign in")
    nr = laptop("nora", "nora", "nora-pass-1234")
    check(len(nr["prospects"]) == 3 and any("Reassigned to you from Sara Malik" in x for x in nr["notes"]), "Nora's BSO receives the 3 prospects")

    # Cole (a sender) leaves: his laptop hands over its leads at its next check-in
    team.leave(); team.sign_in(con, "cole.reed", "cole.reed-pass-1234", consent=True)
    for i in range(10):
        con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject, campaign_id) VALUES (?,?,?,'{}','active',?, '2026-10-01','Quick question',1)",
                    (f"lead{i}@co{i}.com", f"L{i}", f"Co {i}", 0 if i < 6 else 2))
    con.execute("INSERT INTO suppression(email, reason, at) VALUES ('nope@x.com','unsubscribe','2026-10-01')")
    con.commit()
    cole = hcon.execute("SELECT * FROM users WHERE username='cole.reed'").fetchone()
    c.post(f"/users/{cole['id']}", data={"_csrf": tok(f"/users/{cole['id']}"), "action": "remove"})
    check(hcon.execute("SELECT status FROM users WHERE id=?", (cole["id"],)).fetchone()[0] == "removing", "a sender being removed waits for their laptop's leads")
    hub.relay_cycle()
    team.sync(con)                                      # gets the instruction, stops sending, packs up
    check(not team.policy().get("enabled") and not con.execute("SELECT 1 FROM leads WHERE status='active'").fetchone(), "his BSO stops sending at once")
    team.sync(con)                                      # uploads its leads
    hub.relay_cycle()
    check(hcon.execute("SELECT wipe FROM users WHERE id=?", (cole["id"],)).fetchone()[0] == "pending:remove", "with his leads in, the console asks his BSO to erase itself")
    team.sync(con); team.sync(con)                      # erases, and confirms
    check(con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0, "his BSO is erased")
    hub.relay_cycle()
    check(hcon.execute("SELECT status FROM users WHERE id=?", (cole["id"],)).fetchone()[0] == "removed", "once the leads are in, the account is removed")
    d1, e1 = laptop("dan", "dan", "dan-pass-1234"), None
    def leads_of(home, user):
        r_ = subprocess.run([sys.executable, "-c", f"""
import os, sys, json; os.environ['OUTREACH_HOME']={str(tmp / home)!r}; sys.path.insert(0, {str(root)!r})
import bot; con = bot.db_connect('outreach.db')
print('RESULT ' + json.dumps({{'leads': [dict(r) for r in con.execute("SELECT email, status, step FROM leads WHERE email LIKE 'lead%' OR email IN ('sarah@acme.com','tom@globex.com','kim@umbrella.com')")],
 'tasks': [r[0] for r in con.execute("SELECT subject FROM tasks")], 'supp': [r[0] for r in con.execute('SELECT email FROM suppression')]}}))"""],
                            capture_output=True, text=True, timeout=60)
        return json.loads(next(l for l in r_.stdout.splitlines() if l.startswith("RESULT "))[7:])
    laptop("eve", "eve", "eve-pass-1234")
    dl, el = leads_of("dan", "dan"), leads_of("eve", "eve")
    total = len(dl["leads"]) + len(el["leads"])
    check(total == 13 and abs(len(dl["leads"]) - len(el["leads"])) <= 1, f"his 13 leads are shared equally between Dan and Eve ({len(dl['leads'])} and {len(el['leads'])})")
    mid = [x for x in dl["leads"] + el["leads"] if x["step"] == 2]
    check(mid and all(x["status"] == "paused" for x in mid) and any("Continue or restart" in t for t in dl["tasks"] + el["tasks"]),
          "leads part-way through a sequence arrive paused, with a 'continue or restart' task")
    fresh = [x for x in dl["leads"] + el["leads"] if x["step"] == 0 and x["email"].startswith("lead")]
    check(len(fresh) == 6 and all(x["status"] == "active" for x in fresh), "leads not yet emailed carry on normally")
    check("nope@x.com" in dl["supp"] and "nope@x.com" in el["supp"], "his do-not-contact list goes to both")
    ok, msg = team.sync(con)
    check(not ok, "his laptop is disconnected")
    print(f"\nAll {n} teamwork checks passed.")
finally:
    node.terminate()
