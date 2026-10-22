"""Full visibility in the Master Console: sealed reports through the Google relay stand-in, the archive of old data,
supervisor mailbox tracking (personal addresses, BCC outside, deletions), blocking in BSO, the incident log,
the company archive mailbox, retention and the new notice.     python tests/visibility_test.py   (needs Node.js)
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
PORT = 8801
URL = f"http://127.0.0.1:{PORT}/exec"

def raw(frm, to, subject, body, cc=None, bcc=None, mid=None, date="Wed, 21 Oct 2026 10:00:00 +0000", reply_to_id=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    if cc:
        m["Cc"] = cc
    if bcc:
        m["Bcc"] = bcc
    if reply_to_id:
        m["In-Reply-To"] = reply_to_id
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject, body)))}@test>"
    m.set_content(body)
    return bytes(m)

if len(sys.argv) > 1 and sys.argv[1] == "--supervisor":
    home, step = sys.argv[2], sys.argv[3]
    os.environ["OUTREACH_HOME"] = home
    sys.path.insert(0, str(root))
    import bot, team, mailbox, crm  # noqa: E401
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    if step == "1":
        team.sign_in(con, "ali", "ali-pass-1234", consent=True)
        crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                            "prospect_mailbox": {"email": "ali@blacswanken.com", "name": "Ali Khan", "smtp_host": "smtp.x", "smtp_port": 465, "imap_host": "imap.x"},
                            "test_mode": {"enabled": False}})
        bot.SECRETS.set(mailbox.PW_KEY, "pw")
    else:
        team.SIGNED_IN.update(user="ali", name="Ali Khan")
    team.sync(con)
    lid = con.execute("SELECT id FROM leads WHERE email='sarah@acme.com'").fetchone()
    out = {"has_prospect": bool(lid)}
    box = [("in", raw("Sarah <sarah.home@gmail.com>", "ali@blacswanken.com", "Re: Quick question", "Writing from my personal email: Thursday works.", mid="<p1@g>")),
           ("sent", raw("Ali Khan <ali@blacswanken.com>", "sarah@acme.com", "Re: Quick question", "Great, see you Thursday.", bcc="ali.private@gmail.com",
                        mid="<a1@b>", date="Wed, 21 Oct 2026 11:00:00 +0000")),
           ("sent", raw("Ali Khan <ali@blacswanken.com>", "sarah@acme.com", "Re: pricing", "Here's our price list.", mid="<a2@b>",
                        date="Wed, 21 Oct 2026 12:00:00 +0000"))]
    if step == "1" and lid:
        con.execute("INSERT OR IGNORE INTO lead_addresses(email, lead_id, how, confirmed, at) VALUES ('sarah.home@gmail.com', ?, 'added by hand', 1, '2026-10-21')", (lid[0],))
        con.commit()
        mailbox.fetch = lambda c, since_days=45: list(box)
        mailbox.check(crm.cfg(), con)
        con.execute("UPDATE messages SET at='2026-10-21T08:00:00+00:00'")
        con.commit()
        ok, msg = mailbox.send_reply(crm.cfg(), con, lid[0], "Forwarding to my friend", cc="friend@competitor.io")
        out["blocked"] = (not ok) and "outside" in msg
    if step == "2" and lid:
        mailbox.fetch = lambda c, since_days=45: [x for x in box if b"<a2@b>" not in x[1]]   # the price-list email was deleted
        mailbox.fetch_trash_ids = lambda c, since_days=45: set()
        mailbox.check(crm.cfg(), con)
    team.sync(con)
    team.sync(con)
    out["incidents"] = [r["kind"] for r in con.execute("SELECT kind FROM incidents ORDER BY id")]
    out["messages"] = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print("RESULT " + json.dumps(out))
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="vis-test-"))
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = str(tmp / "bot")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "bot").mkdir()
import hub, bot, team, relay, convo, crm, vis  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=visibility_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

def supervisor(step):
    envc = {k: v for k, v in os.environ.items() if not k.startswith(("BSO_", "SENDER", "PROSPECT"))}
    r = subprocess.run([sys.executable, __file__, "--supervisor", str(tmp / "ali"), step], capture_output=True, text=True, timeout=240, env=envc)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        print(r.stdout[-2000:], r.stderr[-3000:])
        check(False, "supervisor laptop ran")
    return json.loads(line[7:])

hub.prepare(str(tmp / "hub"))
key = hub.relay_key(create=True)
node = subprocess.Popen(["node", str(root / "relay" / "test_double.js"), str(PORT), hashlib.sha256(key.encode()).hexdigest()])
captured = []
real_call = relay.call
def spy(url, body, **k):
    captured.append(json.dumps(body))
    return real_call(url, body, **k)
relay.call = spy
try:
    for _ in range(50):
        try:
            real_call(URL, {"action": "ping"}, timeout=2)
            break
        except relay.RelayError:
            time.sleep(0.2)
    c = hub.app.test_client()
    tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar Ali", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    for name, user, email, role in (("BSO Bot", "bso.bot", "bot@getneosofttech.com", "bot"), ("Ali Khan", "ali", "ali@blacswanken.com", "supervisor")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    hcon = hub.sqlite3.connect(str(hub.DB_PATH)); hcon.row_factory = hub.sqlite3.Row
    check(hub.meta_get.__call__ if False else hcon.execute("SELECT value FROM meta WHERE key='vis_pub'").fetchone(), "the console has its key pair")

    # ---- the bot's BSO, with data from before this version
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject) VALUES ('old@legacy.com','Olga','Legacy','{}','done',3,'2026-09-01','Hello')")
    old = con.execute("SELECT id FROM leads WHERE email='old@legacy.com'").fetchone()[0]
    con.execute("INSERT INTO notes(lead_id, body, at, kind) VALUES (?, 'Old note: asked about QA in September', '2026-09-10', 'note')", (old,))
    con.execute("INSERT INTO events(lead_id, sender, type, detail, at) VALUES (?, 'inbox1@getneosofttech.com', 'sent', 'step 1', '2026-09-02')", (old,))
    con.commit()
    team.sign_in(con, "bso.bot", "bso.bot-pass-1234", consent=True)
    check(team.security().get("console_key"), "the console's public key reaches the BSO")
    team.sync(con)              # sends its key, sealed for the console
    hub.relay_cycle()
    team.sync(con)
    check(team.state().get("vis_key_ok"), "the console confirms it has this BSO's key")
    for _ in range(4):
        team.sync(con)
        hub.relay_cycle()
    arch = hcon.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
    check(arch >= 3 and team.state().get("vis_archive_done"), f"the data from before is archived in the console ({arch} records)")
    check("Old note: asked about QA" in c.get("/archive?q=QA").get_data(as_text=True), "and searchable there")

    # ---- live: an email out, a reply in
    crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                        "senders": [{"email": "inbox1@getneosofttech.com", "name": "Cole Reed", "smtp_host": "h", "imap_host": "h", "password_env": "SENDER1_PASSWORD"}],
                        "test_mode": {"enabled": False}, "handover": {"enabled": True}})
    cfg = crm.cfg()
    bot.sync_mode(con, cfg)
    con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject) VALUES ('sarah@acme.com','Sarah','Acme','{}','active',1,'2026-10-01','Quick question')")
    sid = con.execute("SELECT id FROM leads WHERE email='sarah@acme.com'").fetchone()[0]
    convo.record_sent(con, "<s-out@x>", dict(con.execute("SELECT * FROM leads WHERE id=?", (sid,)).fetchone()), "step", "inbox1@getneosofttech.com",
                      "sarah@acme.com", "Quick question", "Hi Sarah, SECRET-OUTREACH-TEXT about developers.", step=0, variant="A")
    con.commit()
    bot.send_email = lambda *a, **k: "<intro@x>"
    bot.notify = lambda *a, **k: None
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"], m["In-Reply-To"] = "Sarah <sarah@acme.com>", "Re: Quick question", "<s-in@acme>", "<s-out@x>"
    m.set_content("Can we set up a call on Tuesday? REPLY-TEXT-77")
    bot.handle_message(cfg, con, {"email": "inbox1@getneosofttech.com", "name": "Cole Reed"}, m)
    con.commit()
    captured.clear()
    team.sync(con)
    team.sync(con)
    hub.relay_cycle()
    blob = " ".join(captured)
    check("SECRET-OUTREACH-TEXT" not in blob and "REPLY-TEXT-77" not in blob and "__vis" in blob, "what travels through Google is sealed (no email text in it)")
    check(hcon.execute("SELECT COUNT(*) FROM vis_sent WHERE body LIKE '%SECRET-OUTREACH-TEXT%'").fetchone()[0] == 1, "the console has the exact email that was sent")
    check(hcon.execute("SELECT temperature FROM vis_received WHERE lead_email='sarah@acme.com'").fetchone()[0] == "hot", "and the reply, with its temperature")
    page = c.get("/conversation?email=sarah@acme.com").get_data(as_text=True)
    check("SECRET-OUTREACH-TEXT" in page and "REPLY-TEXT-77" in page and "matched by thread" in page, "Conversations shows the whole thread")
    conv = c.get("/conversations").get_data(as_text=True)
    check("BSO Bot" in conv and "/conversations?" in conv and "Main campaign" in conv, "campaign numbers per person, clickable")
    check("Sarah" in c.get("/conversations?what=hot").get_data(as_text=True), "the hot filter finds her")
    dash = c.get("/").get_data(as_text=True)
    check("waiting for an answer" in dash, "the overview lists hot leads waiting for an answer")

    # ---- the supervisor
    s1 = supervisor("1")
    check(s1["has_prospect"], "Ali's BSO received Sarah as a prospect")
    check(s1["blocked"], "copying a client email to an outside address is blocked in BSO")
    check(set(s1["incidents"]) >= {"bcc_outside", "blocked"}, "a BCC to his private Gmail is caught and logged: " + ", ".join(s1["incidents"]))
    check(s1["messages"] >= 3, "his emails with Sarah's personal address are tracked too")
    s2 = supervisor("2")
    check("deleted" in s2["incidents"], "deleting a client email is caught (the copy stays)")
    for _ in range(2):
        hub.relay_cycle()
    kinds = [r[0] for r in hcon.execute("SELECT kind FROM incidents")]
    check({"bcc_outside", "blocked", "deleted"} <= set(kinds), "all of it reaches the console's incident log")
    ok, bad = vis.verify_chain(hcon)
    check(ok, "the incident log's chain is intact")
    orig = hcon.execute("SELECT detail FROM incidents WHERE id=1").fetchone()[0]
    hcon.execute("UPDATE incidents SET detail='nothing happened' WHERE id=1")
    hcon.commit()
    check(vis.verify_chain(hcon)[0] is False, "changing an old entry shows")
    sec = c.get("/security").get_data(as_text=True)
    check("BCC to an outside address" in sec and "log altered" in sec, "the Security page lists incidents and flags the tampering")
    hcon.execute("UPDATE incidents SET detail=? WHERE id=1", (orig,))
    hcon.commit()
    inc_id = hcon.execute("SELECT id FROM incidents WHERE kind='blocked'").fetchone()[0]
    c.post("/security", data={"_csrf": tok("/security"), "action": "review", "incident_id": inc_id, "status": "fine", "note": "talked to Ali"})
    check(hcon.execute("SELECT status FROM incident_reviews WHERE incident_id=?", (inc_id,)).fetchone()[0] == "fine", "an incident can be reviewed (the entry itself stays)")
    csvt = c.get("/security/incidents.csv").get_data(as_text=True)
    check("bcc_outside" in csvt and "talked to Ali" in csvt, "the log exports to CSV")
    hconv = c.get("/conversation?email=sarah@acme.com").get_data(as_text=True)
    check("ali.private@gmail.com" in hconv and "Writing from my personal email" in hconv, "the console shows the supervisor's emails, BCC included")

    # ---- the company archive mailbox
    msgs = [raw("Ali Khan <ali@blacswanken.com>", "sarah@acme.com", "Discount", "I can do 30% off if you go with me privately.", bcc="ali.private@gmail.com",
                mid="<phone-1@b>", date="Wed, 21 Oct 2026 07:00:00 +0000")]
    found = vis.check_archive_box(hcon, {}, "", {"ali@blacswanken.com": hcon.execute("SELECT id FROM users WHERE username='ali'").fetchone()[0]},
                                  hub.own_domains(hcon), [], fetcher=lambda: msgs)
    kinds2 = [r[0] for r in hcon.execute("SELECT kind FROM incidents WHERE how='company archive mailbox'")]
    check(found == 2 and "outside_bso" in kinds2 and "bcc_outside" in kinds2, "an email sent from his phone and deleted is caught in the company archive mailbox")
    check(vis.check_archive_box(hcon, {}, "", {"ali@blacswanken.com": 1}, [], [], fetcher=lambda: msgs) == 0, "each archive email is looked at once")

    # ---- retention and the notice
    hcon.execute("UPDATE vis_sent SET at='2020-01-01T00:00:00+00:00'")
    hcon.commit()
    vis.purge(hcon, 24)
    check(hcon.execute("SELECT COUNT(*) FROM vis_sent").fetchone()[0] == 0 and hcon.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] >= 5,
          "old emails go after the retention period; incidents are kept")
    check(any("company property" in l for l in team.DISCLOSURE), "the sign-in notice says leads and conversations are company property")
    st = team.state(); st["consent"] = "1"; team.save_state(st)
    team.sign_out()
    try:
        team.sign_in(con, "bso.bot", "bso.bot-pass-1234")
        check(False, "people confirm the new notice once")
    except ValueError:
        check(True, "people confirm the new notice once")
    print(f"\nAll {n} visibility checks passed.")
finally:
    node.terminate()
