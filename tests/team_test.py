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

check(b"omar@blacswanken.com" in c.get("/setup").data, "setup page pre-fills the master email")
# --- owner setup and login
check(c.get("/").status_code == 302, "console needs sign-in")
rc = hub.app.test_client()
REMOTE = {"REMOTE_ADDR": "203.0.113.9"}
rtok = re.search(r'name="_csrf" value="([^"]+)"', rc.get("/setup", environ_base=REMOTE).get_data(as_text=True)).group(1)
check(b'name="setup_key"' in rc.get("/setup", environ_base=REMOTE).data and b'name="setup_key"' not in c.get("/setup").data,
      "setup key asked only from another computer")
form = {"_csrf": rtok, "setup_key": "wrong", "name": "Omar", "email": "o@x.com", "password": "correct-horse-9", "password2": "correct-horse-9"}
r = rc.post("/setup", data=form, environ_base=REMOTE)
check(b"setup key is wrong" in r.data, "wrong setup key refused from another computer")
r = rc.post("/setup", data=form, environ_base=REMOTE, headers={"X-Forwarded-For": "127.0.0.1"})
check(b"setup key is wrong" in r.data, "faked local address doesn't skip the key")
r = c.post("/setup", data={"_csrf": csrf("/setup"), "org": "NeoSOFT", "name": "Omar", "email": "o@x.com", "password": "correct-horse-9", "password2": "correct-horse-9"})
check(r.status_code == 302, "owner created on the hub computer without a key")
check(c.get("/").status_code == 200, "owner sees the dashboard")
check(c.post("/users/new", data={"name": "x"}).status_code == 400, "POST without CSRF token is rejected")

# --- login throttling
c2 = hub.app.test_client()
for _ in range(5):
    c2.post("/login", data={"_csrf": re.search(r'name="_csrf" value="([^"]+)"', c2.get("/login").get_data(as_text=True)).group(1), "email": "o@x.com", "password": "nope"})
r = c2.post("/login", data={"_csrf": re.search(r'name="_csrf" value="([^"]+)"', c2.get("/login").get_data(as_text=True)).group(1), "email": "o@x.com", "password": "correct-horse-9"})
check(b"Too many failed attempts" in r.data, "login is locked after repeated failures")

c.post("/settings", data={"_csrf": csrf("/settings"), "action": "org", "org": "NeoSOFT", "public_url": "http://127.0.0.1:8791", "grace_hours": "72"})
# --- forgot password by email
sent = []
hub.send_mail = lambda to, subj, body: sent.append((to, body))
c3 = hub.app.test_client()
tk = lambda cl, path: re.search(r'name="_csrf" value="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
r = c3.post("/forgot", data={"_csrf": tk(c3, "/forgot"), "email": "nobody@x.com"})
check(b"on its way" in r.data and not sent, "unknown address gets the same answer and no email")
r = c3.post("/forgot", data={"_csrf": tk(c3, "/forgot"), "email": "o@x.com"})
check(b"on its way" in r.data and len(sent) == 1 and sent[0][0] == "o@x.com", "owner gets a reset email")
link = re.search(r"/reset/([\w-]+)", sent[0][1]).group(1)
r = c3.post(f"/reset/{link}", data={"_csrf": tk(c3, f"/reset/{link}"), "password": "short", "password2": "short"})
check(b"at least 10" in r.data, "weak new password refused")
r = c3.post(f"/reset/{link}", data={"_csrf": tk(c3, f"/reset/{link}"), "password": "fresh-owner-pass-1", "password2": "fresh-owner-pass-1"})
check(r.status_code == 302 and "reset=1" in r.headers["Location"], "reset link sets the new password")
check(c3.get(f"/reset/{link}").status_code == 400, "reset link works only once")
_t = hub.sqlite3.connect(str(hub.DB_PATH)); _t.execute("DELETE FROM throttle"); _t.commit()   # the earlier lockout test used this address
r = c3.post("/login", data={"_csrf": tk(c3, "/login"), "email": "o@x.com", "password": "fresh-owner-pass-1"})
check(r.status_code == 302, "owner signs in with the new password")
check(c3.get("/").status_code == 200, "console opens")
# expired token
exp = hub.sqlite3.connect(str(hub.DB_PATH)); exp.execute("INSERT INTO reset_tokens(user_id, token_hash, expires_at) VALUES (1, ?, '2000-01-01T00:00:00Z')", (hub.sha("old"),)); exp.commit()
check(c3.get("/reset/old").status_code == 400, "expired reset link refused")
c.post("/login", data={"_csrf": csrf("/login"), "email": "o@x.com", "password": "fresh-owner-pass-1"})

# --- add a member who needs master approval, with a cap of 2
t = csrf("/users")
r = c.post("/users/new", data={"_csrf": t, "name": "Ayesha", "email": "a@x.com", "username": "Ayesha", "role": "member", "daily_cap": "2", "weekly_target": "10", "approval": "master", "password": ""}, follow_redirects=True)
html = r.get_data(as_text=True)
m = re.search(r"ID: (\w+)<br>Password: (\S+)</div>", html)
check(bool(m), "sign-in details are shown once, with a generated password")
uname, pw = m.group(1), m.group(2)
check(uname == "ayesha" and len(pw) == 12, "ID is lower-cased; password is 12 characters")
check("Password: " not in c.get("/users").get_data(as_text=True), "password is not shown again")
r = c.post("/users/new", data={"_csrf": csrf("/users"), "name": "Dup", "email": "d@x.com", "username": "ayesha", "role": "member"}, follow_redirects=True)
check(b"already used" in r.data, "duplicate ID refused")
r = c.post("/users/new", data={"_csrf": csrf("/users"), "name": "Weak", "email": "w@x.com", "username": "weak1", "role": "member", "password": "short"}, follow_redirects=True)
check(b"at least 10" in r.data, "weak password refused")

# --- the laptop: locked until signed in
con = bot.db_connect("outreach.db")
for i in range(1, 5):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, created_at) VALUES (?,?,?,?,0,'2026-01-01')", (f"l{i}@acme.com", f"L{i}", "Acme", "active"))
con.commit()
(tmp / "laptop" / "team-defaults.json").write_text(json.dumps({"hub": "http://127.0.0.1:8791", "required": True}))
check(team.required() and not team.signed_in() and not team.joined(), "managed install requires sign-in")
allowed, why, _ = team.gate(con)
check(not allowed and "Sign in" in why, "bot refuses to send before sign-in")
for bad_user, bad_pw, expect in (("ayesha", "wrongpassword", "wrong"), ("nobody", "whatever12345", "wrong")):
    try:
        team.sign_in(con, bad_user, bad_pw, consent=True)
        check(False, "bad credentials refused")
    except ValueError as e:
        check(expect in str(e), f"bad credentials refused ({bad_user})")
try:
    team.sign_in(con, uname, pw, consent=False)
    check(False, "first sign-in needs consent")
except ValueError:
    check(True, "first sign-in needs consent")
pol = team.sign_in(con, uname, pw, consent=True)
check(team.joined() and team.signed_in() == "ayesha" and team.holds_approval(), "signed in; manager approval is in force")
team.sign_out()
check(not team.signed_in(), "sign-out locks the app")
pol = team.sign_in(con, uname, pw)
check(team.signed_in(), "second sign-in needs no consent box")

# --- offline sign-in works from the cached check, not with a wrong password
real = team.hub_url
_dfl = team.defaults; team.defaults = lambda: {"hub": "http://127.0.0.1:1", "required": True}   # hub unreachable
team.sign_out()
try:
    team.sign_in(con, uname, "wrongpassword")
    check(False, "offline: wrong password refused")
except ValueError:
    check(True, "offline: wrong password refused")
team.sign_in(con, uname, pw)
check(team.signed_in() and team.SIGNED_IN.get("offline"), "offline: right password lets the same person in (within grace)")
team.defaults = _dfl
team.sign_in(con, uname, pw)

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
pol = team.sign_in(con, uname, pw)
check(team.sync(con)[0], "signing in again restores a revoked laptop")
c.post(f"/users/{uid}", data={"_csrf": csrf(f"/users/{uid}"), "action": "resetpw", "password": "brand-new-pass-1"})
ok, msg = team.sync(con)
check(not ok, "password reset signs old laptops out")
try:
    team.sign_in(con, uname, pw)
    check(False, "old password no longer works")
except ValueError:
    check(True, "old password no longer works")
team.sign_in(con, uname, "brand-new-pass-1")
check(team.signed_in() == "ayesha", "new password works")
team.leave()
check(not team.joined(), "an unlocked install can leave the team")

# --- http to a public host is refused
try:
    team.check_hub_url("http://example.com")
    check(False, "plain http to a public host is refused")
except ValueError:
    check(True, "plain http to a public host is refused")

# --- the CRM itself opens on the sign-in screen, even with no admin file
import crm  # noqa: E402
(tmp / "laptop" / "team-defaults.json").unlink(missing_ok=True)
team.leave()
check(team.required(), "every install requires sign-in by default")
w0 = crm.app.test_client()
r = w0.get("/")
check(r.status_code == 302 and "/signin" in r.headers["Location"], "opening the app shows sign-in first (before terms)")
sp = w0.get("/signin").data
check(b'name="hub"' in sp and b"What your manager can see" in sp and b'class="gsearch"' not in sp, "first-time sign-in asks for hub address, shows disclosure, no app menus")
r = w0.post("/signin", data={"username": "ayesha", "password": "brand-new-pass-1", "consent": "1", "hub": "http://127.0.0.1:8791"})
check(r.status_code == 302, "first sign-in with hub address works")
r = w0.get("/")
check(r.status_code == 302 and "/welcome" in r.headers["Location"], "after sign-in, terms come next")
w0.post("/signout")
_sp = w0.get("/signin").data; check(b"Hub address: http://127.0.0.1:8791" in _sp, "hub address is remembered")
(tmp / "laptop" / "team-defaults.json").unlink(missing_ok=True)
_orig_bi = team.built_in_hub
team.built_in_hub = lambda: "http://127.0.0.1:8791"
team.leave()
_sp = w0.get("/signin").data
check(b'name="hub"' not in _sp and b'name="username"' in _sp, "built-in hub address: sign-in asks only for ID and password")
r = w0.post("/signin", data={"username": "ayesha", "password": "brand-new-pass-1", "consent": "1"})
check(r.status_code == 302 and team.hub_url() == "http://127.0.0.1:8791", "sign-in uses the built-in hub")
w0.post("/signout")
team.built_in_hub = _orig_bi
(tmp / "laptop" / "team-defaults.json").write_text(json.dumps({"hub": "http://127.0.0.1:8791", "required": True}))
crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}})
team.leave()
w = crm.app.test_client()
r = w.get("/leads")
check(r.status_code == 302 and "/signin" in r.headers["Location"], "CRM pages redirect to the sign-in page")
check(w.get("/signin").status_code == 200 and b"What your manager can see" in w.get("/signin").data, "sign-in page shows the disclosure")
r = w.post("/signin", data={"username": "ayesha", "password": "nope", "consent": "1"})
check(b"wrong" in r.data, "wrong password shows an error")
r = w.post("/signin", data={"username": "ayesha", "password": "brand-new-pass-1", "consent": "1", "next": "/leads"})
check(r.status_code == 302 and w.get("/leads").status_code == 200, "signing in opens the CRM")
check(b"Signed in as" in w.get("/").data, "app shows who is signed in")
w.post("/signout")
check(w.get("/leads").status_code == 302, "sign-out locks the CRM again")

srv.shutdown()
print(f"\nAll {n} team checks passed.")
