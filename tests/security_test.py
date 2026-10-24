"""Security: 2-step sign-in on the console, export permissions and the data access log, idle lock, offline re-check,
trap contacts, erase-and-disconnect, laptop encryption report, sign-in lockouts. Uses the Google relay stand-in (needs Node.js).
    python tests/security_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
PORT = 8798
URL = f"http://127.0.0.1:{PORT}/exec"

tmp = Path(tempfile.mkdtemp(prefix="security-test-"))
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = str(tmp / "cole")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "cole").mkdir()
import hub, bot, team, relay, crm  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=security_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
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
    tok = lambda path, cl=None: re.search(r'name="_csrf" value="([^"]+)"', (cl or c).get(path).get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"')).group(1)
    c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar Ali", "email": "omar@blacswanken.com",
                           "password": "owner-pass-123", "password2": "owner-pass-123"})
    for name, user, email, role in (("Cole Reed", "cole.reed", "cole.reed@getneosofttech.com", "member"),
                                    ("Mia Park", "mia", "mia@getneosofttech.com", "manager"),
                                    ("Dan Ray", "dan", "dan@getneosofttech.com", "member")):
        c.post("/users/new", data={"_csrf": tok("/users"), "name": name, "email": email, "username": user, "role": role, "password": user + "-pass-1234"})
    c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    hcon = hub.sqlite3.connect(str(hub.DB_PATH)); hcon.row_factory = hub.sqlite3.Row
    check(hub._m_get(hcon, "relay_version") == str(hub.RELAY_LATEST), "console uses the latest Google script version")

    # ---- 2-step sign-in for the console
    c.post("/security/2fa", data={"_csrf": tok("/security/2fa"), "action": "start"})
    with c.session_transaction() as s_:
        secret = s_["totp_new"]
    r = c.post("/security/2fa", data={"_csrf": tok("/security/2fa"), "action": "confirm", "code": "000000" if hub.totp(secret) != "000000" else "111111"})
    check("didn't match" in r.get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "a wrong code doesn't switch 2-step sign-in on")
    r = c.post("/security/2fa", data={"_csrf": tok("/security/2fa"), "action": "confirm", "code": hub.totp(secret)})
    codes = re.findall(r"[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", r.get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'))
    check(len(codes) == 8, "the right code switches it on and shows 8 recovery codes")
    c2 = hub.app.test_client()
    r = c2.post("/login", data={"_csrf": tok("/login", c2), "email": "omar@blacswanken.com", "password": "owner-pass-123"})
    check("/login/code" in r.headers.get("Location", ""), "signing in now asks for the code after the password")
    check(c2.get("/users").status_code == 302, "the console stays closed until the code is entered")
    r = c2.post("/login/code", data={"_csrf": tok("/login/code", c2), "code": "123456" if hub.totp(secret) != "123456" else "654321"})
    check("isn't right" in r.get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "a wrong code is refused")
    c2.post("/login/code", data={"_csrf": tok("/login/code", c2), "code": hub.totp(secret)})
    check(c2.get("/users").status_code == 200, "the authenticator code opens the console")
    c3 = hub.app.test_client()
    c3.post("/login", data={"_csrf": tok("/login", c3), "email": "omar@blacswanken.com", "password": "owner-pass-123"})
    c3.post("/login/code", data={"_csrf": tok("/login/code", c3), "code": codes[0]})
    check(c3.get("/users").status_code == 200, "a recovery code works once")
    c4 = hub.app.test_client()
    c4.post("/login", data={"_csrf": tok("/login", c4), "email": "omar@blacswanken.com", "password": "owner-pass-123"})
    c4.post("/login/code", data={"_csrf": tok("/login/code", c4), "code": codes[0]})
    check(c4.get("/users").status_code == 302, "but not twice")

    # ---- rules for every BSO
    r = c.post("/security", data={"_csrf": tok("/security"), "action": "save", "idle_minutes": "10", "reauth_days": "5", "big_list": "50",
                                  "canary_domain": "desk-traps.com", "imap_host": "", "imap_user": ""}, follow_redirects=True)
    body = r.get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"')
    check("Saved" in body and "trap contact(s) planted" in body, "security rules saved; trap contacts planted")
    traps = {row["email"]: row["user_id"] for row in hcon.execute("SELECT * FROM canaries")}
    cole_id = hcon.execute("SELECT id FROM users WHERE username='cole.reed'").fetchone()[0]
    check(len(traps) == 6 and list(traps.values()).count(cole_id) == 2 and all(e.endswith("@desk-traps.com") for e in traps),
          "two trap contacts for each sender, each unique to that person")
    hub.relay_cycle()

    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234", consent=True)
    team.sync(con)
    check(team.security().get("idle_minutes") == 10 and team.security().get("reauth_days") == 5, "Cole's BSO receives the rules")
    mine = [e for e, u in traps.items() if u == cole_id]
    planted = {r_["email"]: dict(r_) for r_ in con.execute("SELECT * FROM leads")}
    check(set(mine) <= set(planted) and all(planted[e]["status"] == "done" for e in mine), "his trap contacts sit in his leads like old finished leads")
    check(all(team.is_trap(e) for e in mine) and not team.is_trap("sarah@acme.com"), "BSO knows which ones are traps")
    sent = []
    real_send = bot.send_email
    try:
        bot.send_email(bot.load_config(str(bot.DATA / "config.yaml"))["senders"][0] if bot.load_config(str(bot.DATA / "config.yaml")).get("senders") else
                       {"name": "Cole", "email": "cole.reed@getneosofttech.com"}, mine[0], "Hi", "Hello", dry_run=True)
        check(False, "BSO refuses to email a trap contact")
    except RuntimeError as e:
        check("do-not-contact" in str(e), "BSO refuses to email a trap contact")

    # ---- permissions: members can't export, and the attempt is logged
    crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}})
    w = crm.app.test_client()
    for i in range(60):
        con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at) VALUES (?,?,?,'{}','active',0,'2026-10-01')",
                    (f"lead{i}@co{i}.com", f"L{i}", f"Co {i}"))
    con.commit()
    r = w.get("/export.csv")
    check(r.status_code == 403, "a member can't export the leads")
    check(w.post("/about/open-data-folder").status_code == 403, "or open the data folder")
    w.get("/leads?view=list")
    team.sync(con)
    hub.relay_cycle()
    sec = c.get("/security").get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"')
    check("blocked" in sec and "export leads to CSV" in sec, "the console's Security page shows the blocked export")
    check("big list" in sec and "Cole Reed" in sec, "and the big list he opened")

    # ---- idle lock
    crm.LAST_ACTIVE["t"] = time.time() - 11 * 60
    r = w.get("/leads")
    check(r.status_code == 302 and "locked=1" in r.headers["Location"] and not team.signed_in(), "BSO locks itself after 10 idle minutes")
    check("locked itself" in w.get(r.headers["Location"]).get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "the sign-in screen says why")
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234")
    check(w.get("/leads").status_code == 200, "signing in again carries on")

    # ---- offline sign-in needs a fresh online check after N days
    st = team.state(); st["last_online_signin"] = bot.iso(bot.now_utc() - bot.timedelta(days=6)); team.save_state(st)
    team.sign_out()
    real_post = team._post
    def offline(*a, **k):
        raise OSError("no network")
    team._post = offline
    try:
        team.sign_in(con, "cole.reed", "cole.reed-pass-1234")
        check(False, "offline sign-in is refused after the re-check period")
    except ValueError as e:
        check("since BSO could check your password" in str(e), "offline sign-in is refused after the re-check period")
    st = team.state(); st["last_online_signin"] = bot.iso(bot.now_utc()); team.save_state(st)
    team.sign_in(con, "cole.reed", "cole.reed-pass-1234")
    check(team.signed_in() == "cole.reed", "but works within it")
    team._post = real_post

    # ---- laptop encryption is reported
    team._enc_cache.update(v=False, t=time.time())
    st = team.state(); st.pop("status_key", None); team.save_state(st)
    team.sync(con)
    hub.relay_cycle()
    sec = c.get("/security").get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"')
    check("not encrypted" in sec, "an unencrypted laptop drive shows as an alert")

    # ---- a trap contact receives mail: whose list leaked
    r = c.post("/security", data={"_csrf": tok("/security"), "action": "lookup", "address": mine[1].upper(), "note": "spam from listbroker.biz"})
    check("planted only for Cole Reed" in r.get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "checking an address names the person whose list it came from")
    check("was copied out" in c.get("/security").get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "and raises a leak alert")
    check(hcon.execute("SELECT COUNT(*) FROM audit WHERE action='canary_hit'").fetchone()[0] == 1, "logged in the audit log")

    # ---- sign-in lockout on the Google relay
    for _ in range(5):
        try:
            relay.call(URL, {"action": "login", "username": "dan", "password": "wrong"})
        except relay.RelayError:
            pass
    try:
        relay.call(URL, {"action": "login", "username": "dan", "password": "dan-pass-1234"})
        check(False, "5 wrong passwords lock the ID for 15 minutes, even with the right one")
    except relay.RelayError as e:
        check("Too many failed attempts" in str(e), "5 wrong passwords lock the ID for 15 minutes, even with the right one")
    hub.relay_cycle()
    sec = c.get("/security").get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"')
    check("5 wrong passwords for" in sec and "is locked out until" in sec, "the console shows the guessing and the lockout")

    # ---- erase laptop and disconnect: work is shared, account stays
    real_leads = con.execute("SELECT COUNT(*) FROM leads WHERE status NOT IN ('unsubscribed','bounced')").fetchone()[0] - 2
    c.post(f"/users/{cole_id}", data={"_csrf": tok(f"/users/{cole_id}"), "action": "wipe_revoke"})
    check(hcon.execute("SELECT wipe FROM users WHERE id=?", (cole_id,)).fetchone()[0] == "pending:disconnect", "erase requested")
    check("erase requested" in c.get(f"/users/{cole_id}").get_data(as_text=True).replace('&#39;', "'").replace('&#34;', '"'), "the user page shows it's waiting")
    hub.relay_cycle()
    for _ in range(3):
        team.sync(con)
        hub.relay_cycle()
    check(con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0 and not team.joined() and not team.SECRETS.load(),
          "Cole's BSO erased its leads and saved passwords")
    u = hcon.execute("SELECT * FROM users WHERE id=?", (cole_id,)).fetchone()
    check(u["wipe"] == "done" and u["status"] == "active", "the console records the erase; the account stays")
    got = [json.loads(x["item"]) for x in hcon.execute("SELECT item FROM notices WHERE item LIKE '%leads_in%'")]
    moved = [l["email"] for g_ in got for l in g_.get("leads") or []]
    check(len(moved) == real_leads and not any(e in moved for e in mine), f"his {real_leads} leads were shared with the others, without his trap contacts")
    print(f"\nAll {n} security checks passed.")
finally:
    node.terminate()
