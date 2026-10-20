"""The master sets up a person's BSO from the console (inboxes, mail server, keys, schedule); their BSO applies it at
first sign-in, encrypted on the way. Uses the Google relay stand-in (needs Node.js).     python tests/setup_test.py
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
PORT = 8799
URL = f"http://127.0.0.1:{PORT}/exec"

if len(sys.argv) > 1 and sys.argv[1] == "--laptop":
    home, user, pw = sys.argv[2:5]
    os.environ["OUTREACH_HOME"] = home
    sys.path.insert(0, str(root))
    import bot, team, yaml  # noqa: E401
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, user, pw, consent=True)
    team.sync(con)                  # the app checks in every couple of minutes
    sp = bot.settings_path(str(bot.DATA / "config.yaml"))
    cur = yaml.safe_load(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    print("RESULT " + json.dumps({"senders": [x["email"] for x in cur.get("senders") or []],
                                  "pw1": team.SECRETS.load().get("SENDER1_PASSWORD"), "ai": team.SECRETS.load().get("ANTHROPIC_API_KEY")}))
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="setup-test-"))
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = str(tmp / "bot")
os.environ["HUB_HOME"] = str(tmp / "hub")
(tmp / "bot").mkdir()
import hub, bot, team, relay, yaml, sealbox  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=setup_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1
txt = lambda r: r.get_data(as_text=True).replace("&#39;", "'").replace("&#34;", '"')

# the encryption itself
k = os.urandom(32)
box = sealbox.seal(k, "secret text")
check(sealbox.open_(k, box) == b"secret text" and "secret" not in box, "sealed data opens with the key and hides the text")
for bad in (os.urandom(32),):
    try:
        sealbox.open_(bad, box); check(False, "a wrong key is refused")
    except ValueError:
        check(True, "a wrong key is refused")
tampered = box[:-6] + ("A" if box[-6] != "A" else "B") + box[-5:]
try:
    sealbox.open_(k, tampered); check(False, "changed data is refused")
except ValueError:
    check(True, "changed data is refused")
w = sealbox.wrap(k, "bso.bot", "pass-1234567")
check(sealbox.unwrap(w, "bso.bot", "pass-1234567") == k, "a key wrapped with a password opens with it")
try:
    sealbox.unwrap(w, "bso.bot", "wrong-password"); check(False, "and not with another password")
except ValueError:
    check(True, "and not with another password")

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
    c.post("/settings", data={"_csrf": tok("/settings"), "action": "relay_url", "relay_url": URL})
    hcon = hub.sqlite3.connect(str(hub.DB_PATH)); hcon.row_factory = hub.sqlite3.Row

    r = c.post("/bso-setup", data={"_csrf": tok("/bso-setup"), "smtp_host": "mail.getneosofttech.com", "smtp_port": "465", "imap_host": "mail.getneosofttech.com",
                                   "daily_limit": "15", "ramp": "on", "anthropic_key": "sk-ant-test-0000111122223333", "autorun_minutes": "30", "mode": "live"})
    check("Saved" in txt(r), "the master fills in BSO setup once")
    check("sk-ant-test-0000111122223333" not in txt(c.get("/bso-setup")) and "3333" in txt(c.get("/bso-setup")), "the AI key is shown masked")

    lines = "\n".join(f"inbox{i}@getneosofttech.com, Sender {i}, mailbox-pw-{i}" for i in range(1, 4))
    c.post("/users/new", data={"_csrf": tok("/users"), "name": "BSO Bot", "email": "bot@getneosofttech.com", "username": "bso.bot", "role": "bot",
                               "password": "bot-pass-12345", "inboxes": lines})
    bot_id = hcon.execute("SELECT id FROM users WHERE username='bso.bot'").fetchone()[0]
    hub.relay_cycle()
    doc = json.dumps(hub.relay_doc(hcon))
    check("mailbox-pw-1" not in doc and "sk-ant-test" not in doc and '"setup"' in doc, "what goes to Google Drive is encrypted (no inbox password or key in it)")

    # the bot's computer: install, sign in once
    team.built_in_hub = lambda: URL
    con = bot.db_connect("outreach.db")
    team.sign_in(con, "bso.bot", "bot-pass-12345", consent=True)
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    senders = [x for x in cfg.get("senders") or [] if "example" not in x.get("email", "")]
    check([x["email"] for x in senders] == [f"inbox{i}@getneosofttech.com" for i in range(1, 4)], "first sign-in sets up its 3 inboxes")
    check(all(x["smtp_host"] == "mail.getneosofttech.com" and x["smtp_port"] == 465 and x["imap_host"] == "mail.getneosofttech.com" and x["daily_limit"] == 15
              and x.get("ramp_from") for x in senders), "with the mail server, daily limit and warm-up")
    check([bot.env(x["password_env"]) for x in senders] == ["mailbox-pw-1", "mailbox-pw-2", "mailbox-pw-3"], "and their passwords, saved encrypted on the computer")
    check(bot.env("ANTHROPIC_API_KEY") == "sk-ant-test-0000111122223333", "the AI key is in place")
    check(cfg["autorun"]["enabled"] and cfg["autorun"]["every_minutes"] == 30 and not cfg["test_mode"]["enabled"], "campaigns run by themselves every 30 minutes, live")
    team.sync(con)
    hub.relay_cycle()
    u = hcon.execute("SELECT * FROM users WHERE id=?", (bot_id,)).fetchone()
    check(u["setup_applied"] and u["setup_applied"] == u["setup_sent"], "the console sees it was applied")
    check("applied on their BSO" in txt(c.get(f"/users/{bot_id}")) and "applied" in txt(c.get("/bso-setup")), "and shows it")
    page = txt(c.get(f"/users/{bot_id}"))
    check("mailbox-pw-1" not in page and "inbox1@getneosofttech.com, Sender 1, keep" in page, "the user page lists the inboxes without showing passwords")

    # the master adds an inbox later; existing passwords stay
    c.post(f"/users/{bot_id}", data={"_csrf": tok(f"/users/{bot_id}"), "action": "setup",
                                     "inboxes": "\n".join(f"inbox{i}@getneosofttech.com, Sender {i}, keep" for i in range(1, 4)) + "\ninbox4@getneosofttech.com, Sender 4, mailbox-pw-4"})
    hub.relay_cycle()
    team.sync(con)
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    senders = [x for x in cfg.get("senders") or [] if "example" not in x.get("email", "")]
    check(len(senders) == 4 and [bot.env(x["password_env"]) for x in senders] == [f"mailbox-pw-{i}" for i in range(1, 5)],
          "an inbox added later reaches the running bot, without anyone signing in again")
    c.post("/bso-setup", data={"_csrf": tok("/bso-setup"), "smtp_host": "mail.getneosofttech.com", "smtp_port": "465", "imap_host": "mail.getneosofttech.com",
                               "daily_limit": "20", "ramp": "on", "autorun_minutes": "45", "mode": "live"})
    hub.relay_cycle()
    team.sync(con)
    cfg = bot.load_config(str(bot.DATA / "config.yaml"))
    check(cfg["autorun"]["every_minutes"] == 45 and all(x["daily_limit"] == 20 for x in cfg["senders"] if "example" not in x["email"])
          and bot.env("ANTHROPIC_API_KEY") == "sk-ant-test-0000111122223333", "changing BSO setup updates every BSO (the saved key is kept)")

    # a new computer after a password reset
    c.post(f"/users/{bot_id}", data={"_csrf": tok(f"/users/{bot_id}"), "action": "resetpw", "password": "new-bot-pass-999"})
    hub.relay_cycle()
    envc = {k_: v for k_, v in os.environ.items() if not re.match(r"^(BSO_|SENDER|ANTHROPIC|INSTANTLY)", k_)}
    r_ = subprocess.run([sys.executable, __file__, "--laptop", str(tmp / "laptop2"), "bso.bot", "new-bot-pass-999"], capture_output=True, text=True, timeout=180, env=envc)
    line = next((l for l in r_.stdout.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        print(r_.stdout[-1500:], r_.stderr[-3000:])
    res = json.loads(line[7:]) if line else {}
    check(len(res.get("senders") or []) == 4 and res.get("pw1") == "mailbox-pw-1" and res.get("ai") == "sk-ant-test-0000111122223333",
          "a fresh install on another computer sets itself up with the new password")

    # a person created before this feature: the master gives their password once
    c.post("/users/new", data={"_csrf": tok("/users"), "name": "Cole Reed", "email": "cole.reed@getneosofttech.com", "username": "cole.reed", "role": "member",
                               "password": "cole-pass-12345"})
    cole = hcon.execute("SELECT id FROM users WHERE username='cole.reed'").fetchone()[0]
    hub._secret_store().set(f"SETUP_KEY_{cole}", "")
    r = c.post(f"/users/{cole}", data={"_csrf": tok(f"/users/{cole}"), "action": "setup", "inboxes": "cole.reed@getneosofttech.com, Cole Reed, pw-x", "password": "wrong-pass-999"},
               follow_redirects=True)
    check("isn't their current BSO password" in txt(r), "a wrong current password is refused")
    r = c.post(f"/users/{cole}", data={"_csrf": tok(f"/users/{cole}"), "action": "setup", "inboxes": "cole.reed@getneosofttech.com, Cole Reed, pw-x", "password": "cole-pass-12345"},
               follow_redirects=True)
    check("Setup sent" in txt(r), "with the right one the setup is sent")
    # ---- one spreadsheet for everyone
    import io, openpyxl
    r = c.get("/bso-setup/template.xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    check([x.value for x in wb["Inboxes"][1]] == hub.TEMPLATE_HEADERS and "How to fill" in wb.sheetnames, "the template downloads as an Excel file")
    ws = wb["Inboxes"]
    ws.delete_rows(2, 3)
    for i in range(5, 11):                           # 6 more inboxes for the bot
        ws.append([f"inbox{i}@getneosofttech.com", f"Sender {i}", f"mailbox-pw-{i}", "bso.bot", "", ""])
    ws.append(["sam.lee@getneosofttech.com", "Sam Lee", "sam-box-pw", "sam.lee", "Sam Lee", "member"])
    ws.append(["oops@getneosofttech.com", "Oops", "replace-with-password", "sam.lee", "", ""])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    r = c.post("/bso-setup/import", data={"_csrf": tok("/bso-setup"), "assign_to": "", "role": "member", "file": (buf, "inboxes.xlsx")},
               content_type="multipart/form-data")
    page = txt(r)
    check("still the example password" in page, "rows left with the example password are skipped")
    check(len(hub.user_inboxes(bot_id)) == 10, "the bot now has 10 inboxes (4 kept, 6 added)")
    sam = hcon.execute("SELECT * FROM users WHERE username='sam.lee'").fetchone()
    check(sam and sam["role"] == "member" and sam["name"] == "Sam Lee" and "1 new sign-in" in page, "a new person in the sheet is created")
    tokn = re.search(r"/bso-setup/credentials/([^.]+)\.csv", page).group(1)
    csvt = c.get(f"/bso-setup/credentials/{tokn}.csv").get_data(as_text=True)
    sam_pw = csvt.strip().splitlines()[1].split(",")[2]
    check("sam.lee" in csvt and len(sam_pw) >= 10, "their sign-in can be downloaded")
    hub.relay_cycle()
    r_ = subprocess.run([sys.executable, __file__, "--laptop", str(tmp / "laptop2"), "bso.bot", "new-bot-pass-999"], capture_output=True, text=True, timeout=180, env=envc)
    res = json.loads(next(l for l in r_.stdout.splitlines() if l.startswith("RESULT "))[7:])
    check(len(res["senders"]) == 10, "the bot's computer picks up all 10 inboxes")
    r_ = subprocess.run([sys.executable, __file__, "--laptop", str(tmp / "sam"), "sam.lee", sam_pw], capture_output=True, text=True, timeout=180, env=envc)
    res = json.loads(next(l for l in r_.stdout.splitlines() if l.startswith("RESULT "))[7:])
    check(res["senders"] == ["sam.lee@getneosofttech.com"] and res["pw1"] == "sam-box-pw", "Sam signs in on his laptop and his inbox is ready")
    buf2 = io.StringIO("inbox email,sender name,password\ncsv1@getneosofttech.com,Csv One,pw-c1\n")
    r = c.post("/bso-setup/import", data={"_csrf": tok("/bso-setup"), "assign_to": str(bot_id), "file": (io.BytesIO(buf2.getvalue().encode()), "more.csv")},
               content_type="multipart/form-data")
    check(len(hub.user_inboxes(bot_id)) == 11, "a plain CSV works too, with everything assigned to the bot")
    print(f"\nAll {n} setup checks passed.")
finally:
    node.terminate()
