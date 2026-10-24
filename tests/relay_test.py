"""End-to-end test of team mode through the Google Drive relay.
Runs relay/Code.gs in a local stand-in for Google Apps Script (needs Node.js), the master console,
and a team laptop.     python tests/relay_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
tmp = Path(tempfile.mkdtemp(prefix="relay-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "laptop")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "laptop").mkdir()

import hub, bot, team, relay  # noqa: E402

n = 0
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)
    n += 1

PORT = 8793
URL = f"http://127.0.0.1:{PORT}/exec"
hub.prepare(str(tmp / "hub"))
key = hub.relay_key(create=True)
node = subprocess.Popen(["node", str(root / "relay" / "test_double.js"), str(PORT), hashlib.sha256(key.encode()).hexdigest()],
                        env=dict(os.environ, UNSUB_LINKS="1"))   # the master switched unsubscribe links on in this test
try:
    for _ in range(50):
        try:
            relay.call(URL, {"action": "ping"}, timeout=2)
            break
        except relay.RelayError:
            time.sleep(0.2)
    c = hub.app.test_client()
    tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)

    # --- master: account, a member, connect the relay
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    check(c.get("/").status_code == 200, "master account created")
    c.post("/users/new", data={"_csrf": tok("/users"), "name": "Ayesha", "email": "a@x.com", "username": "ayesha", "role": "member",
                               "daily_cap": "2", "approval": "master", "password": "ayesha-pass-1"})
    page = c.get("/settings").get_data(as_text=True)
    check("script.google.com" in page and hashlib.sha256(key.encode()).hexdigest() in page and key not in page,
          "settings show the script with the key's fingerprint, never the key")
    r = c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": "https://example.com/not-a-script"})
    check(b"script.google.com/macros" in r.data, "a wrong address is refused")
    r = c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    check(b"Connected" in r.data, "relay connected and test passed")

    # --- laptop: sign in with only ID and password (relay address built in)
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    for i in range(1, 4):
        con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at) VALUES (?,?,?,?,0,'2026-01-01')",
                    (f"l{i}@acme.com", f"L{i}", "Acme", "active"))
    con.commit()
    try:
        team.sign_in(con, "ayesha", "wrong-password", consent=True)
        check(False, "wrong password refused")
    except ValueError as e:
        check("wrong" in str(e), "wrong password refused by the relay")
    team.sign_in(con, "ayesha", "ayesha-pass-1", consent=True)
    check(team.signed_in() == "ayesha" and team.holds_approval() and team.policy().get("daily_cap") == 2,
          "member signs in through the relay and gets their limits")
    team.sign_out()
    team.sign_in(con, "omar@blacswanken.com", "owner-pass-123", consent=True)
    check(team.signed_in() == "omar@blacswanken.com", "master can sign in to BSO with the master email")
    ok, msg = team.sync(con)
    check(ok, "master's laptop checks in (ID with dots): " + msg)
    check(team.gate(con)[0], "master's laptop is allowed to send")
    c.post("/users/new", data={"_csrf": tok("/users"), "name": "Cole Reed", "email": "cole.reed@getneosofttech.com", "username": "cole.reed",
                               "role": "member", "password": "cole-pass-1234"})
    hub.relay_cycle()
    team.leave()
    team.sign_in(con, "cole.reed", "cole-pass-1234", consent=True)
    check(team.sync(con)[0], "member with a dotted ID signs in and checks in")
    hub.relay_cycle()
    check("Cole Reed" in c.get("/").get_data(as_text=True), "dotted ID shows in the console")
    team.leave()
    team.sign_in(con, "ayesha", "ayesha-pass-1", consent=True)

    # --- activity and a draft travel up
    bot.log_event(con, 1, "sent", "step 1", "me@x.com", "A")
    bot.log_event(con, 1, "reply", "interested", "me@x.com")
    con.execute("INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at) VALUES (2,0,'me@x.com','Hello','Body text','pending','2026-01-01')")
    con.commit()
    ok, msg = team.sync(con)
    check(ok, "laptop report accepted by the relay")
    files = json.loads(__import__("urllib.request").request.urlopen(f"http://127.0.0.1:{PORT}/_files").read())
    check(any(f["name"] == "BSO Team Data" for f in files) and any(f["name"].startswith("ayesha_") for f in files),
          "report stored in the master's Drive folder")
    ok, msg = hub.relay_cycle()
    check(ok and re.search(r"[12] reports? collected", msg), "master console collects it: " + msg)
    act = c.get("/activity").get_data(as_text=True)
    check("l1@acme.com" in act and "interested" in act, "activity visible in the console")
    check("Online" in c.get("/").get_data(as_text=True), "laptop shows as online")
    files = json.loads(__import__("urllib.request").request.urlopen(f"http://127.0.0.1:{PORT}/_files").read())
    check(not any(f["name"].startswith("ayesha_") for f in files), "collected reports are cleared from Drive")

    # --- approval travels down, acknowledgement back up
    ap = c.get("/approvals").get_data(as_text=True)
    did = int(re.search(r"/approvals/(\d+)", ap).group(1))
    c.post(f"/approvals/{did}", data={"_csrf": tok("/approvals"), "action": "approve", "subject": "Hello there", "body": "Edited by manager"})
    hub.relay_cycle()
    team.sync(con)
    o = con.execute("SELECT * FROM outbox WHERE lead_id=2").fetchone()
    check(o["status"] == "approved" and o["body"] == "Edited by manager" and o["subject"] == "Hello there", "approved, edited draft reaches the laptop")
    team.sync(con)
    hub.relay_cycle()
    check("waiting for their laptop" not in c.get("/approvals").get_data(as_text=True), "console knows it was delivered")

    # --- pause, resume, password reset
    uid = hub.sqlite3.connect(str(hub.DB_PATH)).execute("SELECT id FROM users WHERE username='ayesha'").fetchone()[0]
    c.post(f"/users/{uid}", data={"_csrf": tok(f"/users/{uid}"), "action": "disable"})
    hub.relay_cycle()
    allowed, why, _ = team.gate(con)
    check(not allowed and "paused" in why.lower(), "pausing in the console stops sending on the laptop")
    c.post(f"/users/{uid}", data={"_csrf": tok(f"/users/{uid}"), "action": "enable"})
    hub.relay_cycle()
    check(team.gate(con)[0], "resuming lets it send again")
    c.post(f"/users/{uid}", data={"_csrf": tok(f"/users/{uid}"), "action": "resetpw", "password": "new-ayesha-pass"})
    hub.relay_cycle()
    ok, msg = team.sync(con)
    check(not ok and "disconnected" in msg, "password reset signs the laptop out")
    try:
        team.sign_in(con, "ayesha", "ayesha-pass-1")
        check(False, "old password refused")
    except ValueError:
        check(True, "old password refused")
    team.sign_in(con, "ayesha", "new-ayesha-pass")
    check(team.sync(con)[0], "new password works")

    # --- one-click unsubscribe through the relay (version 5+, switched on by the master)
    import urllib.request, urllib.parse, safety  # noqa: E401
    check(team.state().get("relay_version") == 6, "the laptop sees relay version 6")
    check("var UNSUB_LINKS = false;" in (root / "relay" / "Code.gs").read_text(),
          "the script as shipped keeps unsubscribe links off (they would print the relay address in every cold email)")
    st = team.state(); st["relay_unsub"] = False; team.save_state(st)
    h = safety.unsub_headers(con, "cole@neosoft-mail.com", "l1@acme.com")
    check(h == {"List-Unsubscribe": "<mailto:cole@neosoft-mail.com?subject=unsubscribe>"}, "links off: cold emails carry only the mailto: unsubscribe")
    team.sync(con)
    check(team.state().get("relay_unsub") is True, "the laptop learns the master switched links on")
    h = safety.unsub_headers(con, "cole@neosoft-mail.com", "l1@acme.com")
    con.commit()
    m = re.match(r"<(http[^>]+)>, <mailto:cole@neosoft-mail.com\?subject=unsubscribe>", h["List-Unsubscribe"])
    check(m and h.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click" and "acme" not in h["List-Unsubscribe"],
          "cold emails carry a one-click link (a random token, not the address) and a mailto: fallback")
    link = m.group(1)
    page = urllib.request.urlopen(link, timeout=10).read().decode()
    check("<form method=\"post\"" in page and "Unsubscribe" in page and not con.execute("SELECT 1 FROM suppression WHERE email='l1@acme.com'").fetchone(),
          "opening the link only shows a button: a mail scanner that opens links unsubscribes nobody")
    req = urllib.request.Request(link, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    out = urllib.request.urlopen(req, timeout=10).read().decode()
    check("unsubscribed" in out, "Gmail's one-click POST is accepted by the relay")
    req = urllib.request.Request(URL + "?u=bogus!!", data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    check("not valid" in urllib.request.urlopen(req, timeout=10).read().decode(), "a made-up token is refused")
    check(team.sync(con)[0] and con.execute("SELECT reason FROM suppression WHERE email='l1@acme.com'").fetchone()[0] == "unsubscribe link"
          and con.execute("SELECT status FROM leads WHERE email='l1@acme.com'").fetchone()[0] == "unsubscribed",
          "the next sync stops all email to that person")
    team.sync(con)
    check(not con.execute("SELECT 1 FROM suppression WHERE email='l2@acme.com'").fetchone(), "nobody else is affected")

    # --- off-laptop backups to the master's Google Drive (version 6)
    safety.DRIVE_PART = 2000                      # small parts so the test covers a backup in several pieces
    check(safety.drive_ready(), "a signed-in laptop on relay 6 has Google Drive backups with nothing to set up")
    r = safety.backup(con)
    check(r["ok"] and r.get("drive_ok") and "read back from Google Drive" in r.get("drive_detail", ""),
          "a backup goes to Google Drive, is read back and passes the restore test: " + str(r.get("drive_detail")))
    lst = safety.drive_list()
    check(len(lst) == 1 and lst[0]["complete"] and lst[0]["parts"] > 1 and lst[0]["name"] == r["drive"], "it is listed, complete, in several parts")
    files = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/_files").read())
    raw = [f for f in files if f["name"].startswith("BSO-backup-")]
    check(raw and not any("acme" in json.dumps(f) for f in raw), "Drive holds only the sealed copy")
    p = safety.drive_fetch(r["drive"])
    check(safety.check_sealed(p, team._team_key())[0], "a replacement laptop with the same sign-in downloads it and it opens")
    try:
        safety.drive_fetch("../../evil")
        check(False, "odd names refused")
    except RuntimeError:
        check(True, "a made-up backup name is refused before anything is written")
    sealed = p.read_text()
    for i in range(16):
        safety.drive_put(f"BSO-backup-202601{i + 10:02d}-0600.bsobak", sealed[:500])
    lst = safety.drive_list()
    check(len(lst) == 14 and lst[0]["name"] == r["drive"] and all(x["complete"] for x in lst), "only the newest 14 are kept on Drive")
    try:
        safety._drive_call("backup_put", {"name": "BSO-backup-20260101-0000.bsobak", "part": 0, "parts": 1, "data": "<script>"})
        check(False, "bad data refused")
    except RuntimeError:
        check(True, "the relay refuses anything that isn't a sealed backup")
    team.leave()
    team.sign_in(con, "cole.reed", "cole-pass-1234", consent=True)
    check(safety.drive_list() == [], "another person's sign-in can't see this laptop's backups")
    team.leave()
    team.sign_in(con, "ayesha", "new-ayesha-pass", consent=True)

    # --- relay unreachable: laptop keeps working within the grace period
    team.built_in_hub = lambda: "http://127.0.0.1:1/exec"
    team.sign_out()
    team.sign_in(con, "ayesha", "new-ayesha-pass")
    check(team.signed_in() and team.SIGNED_IN.get("offline"), "relay unreachable: same person can still sign in on this laptop")
    print(f"\nAll {n} relay checks passed.")
finally:
    node.terminate()
