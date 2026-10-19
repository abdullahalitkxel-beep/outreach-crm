"""End-to-end test of team mode: a hub and a member laptop talking over real HTTP.
   python tests/team_test.py
"""
import os, re, sys, tempfile, threading, json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
tmp = Path(tempfile.mkdtemp(prefix="team-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "laptop")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "laptop").mkdir()

import hub, bot, team  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

n = 0
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)
    n += 1

d, key = hub.prepare(str(tmp / "hub"))
srv = make_server("127.0.0.1", 8791, hub.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
c = hub.app.test_client()

def csrf(path="/login"):
    html = c.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

# --- owner setup and login
check(c.get("/").status_code == 302, "console needs sign-in")
r = c.post("/setup", data={"_csrf": csrf("/setup"), "setup_key": "wrong", "name": "Omar", "email": "o@x.com", "password": "correct-horse-9", "password2": "correct-horse-9"})
check(b"setup key is wrong" in r.data, "wrong setup key refused")
r = c.post("/setup", data={"_csrf": csrf("/setup"), "setup_key": key, "org": "NeoSOFT", "name": "Omar", "email": "o@x.com", "password": "correct-horse-9", "password2": "correct-horse-9"})
check(r.status_code == 302, "owner created with the setup key")
check(c.get("/").status_code == 200, "owner sees the dashboard")
check(c.post("/users/new", data={"name": "x"}).status_code == 400, "POST without CSRF token is rejected")

# --- login throttling
c2 = hub.app.test_client()
for _ in range(5):
    c2.post("/login", data={"_csrf": re.search(r'name="_csrf" value="([^"]+)"', c2.get("/login").get_data(as_text=True)).group(1), "email": "o@x.com", "password": "nope"})
r = c2.post("/login", data={"_csrf": re.search(r'name="_csrf" value="([^"]+)"', c2.get("/login").get_data(as_text=True)).group(1), "email": "o@x.com", "password": "correct-horse-9"})
check(b"Too many failed attempts" in r.data, "login is locked after repeated failures")

c.post("/settings", data={"_csrf": csrf("/settings"), "action": "org", "org": "NeoSOFT", "public_url": "http://127.0.0.1:8791", "grace_hours": "72"})
# --- add a member who needs master approval, with a cap of 2
t = csrf("/users")
r = c.post("/users/new", data={"_csrf": t, "name": "Ayesha", "email": "a@x.com", "role": "member", "daily_cap": "2", "weekly_target": "10", "approval": "master"}, follow_redirects=True)
m = re.search(r"BSO1\.[A-Za-z0-9_-]+", r.get_data(as_text=True))
check(bool(m), "invite string is shown")
invite = m.group(0)
check(team.parse_invite(invite)[0] == "http://127.0.0.1:8791", "invite carries the hub address")
url, code = team.parse_invite(invite)

# --- member joins
con = bot.db_connect("outreach.db")
for i in range(1, 5):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at) VALUES (?,?,?,?,0,'2026-01-01')", (f"l{i}@acme.com", f"L{i}", "Acme", "active"))
con.commit()
try:
    team.join(invite, consent=False)
    check(False, "joining needs consent")
except ValueError:
    check(True, "joining needs consent")
pol, hubu = team.join(invite, consent=True, device="test-laptop")
team.finish_join(con, hubu, pol)
check(team.joined() and team.holds_approval(), "member joined; manager approval is in force")
try:
    team.join(invite, consent=True)
    check(False, "invite works only once")
except ValueError:
    check(True, "invite works only once")

# --- activity and drafts sync up
bot.log_event(con, 1, "sent", "step 1", "me@x.com", "A")
bot.log_event(con, 1, "reply", "interested", "me@x.com")
bot.log_event(con, 2, "error", "SMTP login for secret@x.com failed password=abc", "me@x.com")
con.execute("INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at) VALUES (3,0,'me@x.com','Hello','Body text','pending','2026-01-01')")
con.commit()
ok, msg = team.sync(con)
check(ok, "sync succeeds: " + msg)
page = c.get("/activity").get_data(as_text=True)
check("l1@acme.com" in page and "interested" in page, "manager sees the send and the reply label")
check("password=abc" not in page and "send failed" in page, "raw error text is not shared")
ap = c.get("/approvals").get_data(as_text=True)
check("l3@acme.com" in ap and "Body text" in ap, "draft awaiting approval appears")

# --- manager edits and approves; member receives it
did = int(re.search(r'/approvals/(\d+)', ap).group(1))
rr = c.post(f"/approvals/{did}", data={"_csrf": csrf("/approvals"), "action": "approve", "subject": "Hello there", "body": "Edited by manager"})
ok, _ = team.sync(con)
o = con.execute("SELECT * FROM outbox WHERE lead_id=3").fetchone()
check(o["status"] == "approved" and o["body"] == "Edited by manager" and o["subject"] == "Hello there", "approved + edited draft arrives on the laptop")
team.sync(con)
check(c.get("/approvals").get_data(as_text=True).count("waiting for their laptop") == 0, "hub knows the decision was delivered")

# --- cap and pause enforced through gate()
allowed, why, cap = team.gate(con)
check(allowed and cap == 2, "gate allows sending with a cap of 2")
uid = hub.sqlite3.connect(str(hub.DB_PATH)).execute("SELECT id FROM users WHERE email='a@x.com'").fetchone()[0]
c.post(f"/users/{uid}", data={"_csrf": csrf(f"/users/{uid}"), "action": "disable"})
allowed, why, cap = team.gate(con)
check(not allowed and "paused" in why.lower(), "paused account stops sending")
c.post(f"/users/{uid}", data={"_csrf": csrf(f"/users/{uid}"), "action": "enable"})
check(team.gate(con)[0], "resumed account sends again")

# --- bad token, revoke
check(hub.app.test_client().post("/api/v1/sync", json={}).status_code == 401, "sync without a token is refused")
c.post(f"/users/{uid}", data={"_csrf": csrf(f"/users/{uid}"), "action": "revoke"})
ok, msg = team.sync(con)
check(not ok and "disconnected" in msg, "revoked laptop is cut off")
team.leave()
check(not team.joined(), "member can leave the team")

# --- http to a public host is refused
try:
    team.parse_invite("BSO1." + __import__("base64").urlsafe_b64encode(json.dumps({"u": "http://example.com", "c": "X"}).encode()).decode())
    check(False, "plain http to a public host is refused")
except ValueError:
    check(True, "plain http to a public host is refused")

srv.shutdown()
print(f"\nAll {n} team checks passed.")
