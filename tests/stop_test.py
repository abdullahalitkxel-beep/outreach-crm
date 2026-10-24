"""Stop requests are always honoured. Each check first reproduces a way the audits found BSO could keep emailing someone
who asked it to stop, then proves it can't any more:
  - a stop that arrives while a run is under way (the run must not send, nor put the lead back in the sequence)
  - a do-not-contact entry with the lead still "active"
  - switching test mode off erasing real stops
  - stop wording the first rules missed, and replies written below the quoted email
  - a reply that crashes the handler (non-English subject) or a crash between reading and handling
  - a long outage (stop requests older than the fixed 14-day window)
  - the team's do-not-contact list (console -> every laptop; the bot waits for it)
  - a run left "running" by a crash blocking every later run; a stuck run; the "online but not sending" alarm
   python tests/stop_test.py"""
import json, os, sqlite3, sys, tempfile, threading, time, types  # noqa: E401
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
tmp = Path(tempfile.mkdtemp(prefix="stop-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "bso")
os.environ["HUB_HOME"] = str(tmp / "hub")
os.environ["BSO_NO_SIGNIN"] = "1"
(tmp / "bso").mkdir()
import bot, crm, safety, guard, convo  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=stop_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1
def _crash(t, v, tb):
    import traceback
    gh_error("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)
sys.excepthook = _crash

crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [{"email": "a@d1.com", "name": "A", "smtp_host": "h", "imap_host": "h", "password_env": "PA", "daily_limit": 20}],
                    "review": {"enabled": False}, "test_mode": {"enabled": False}, "ref_code": {"enabled": False},
                    "send_window": {"enabled": False}, "pacing": {"min_seconds": 0, "max_seconds": 0},
                    "personalization": {"enabled": False, "fallback_line": "I noticed {company} is growing."}})
bot.SECRETS.set("PA", "pw")
bot.SECRETS.apply_to_environ()
cfg = lambda: bot.load_config(crm.CONFIG_PATH)
con = bot.db_connect("outreach.db")
smtp = []
bot._smtp_deliver = lambda s, m: smtp.append(m["To"])
bot.notify = lambda *a, **k: None
args = types.SimpleNamespace(dry_run=False, force=True, no_check=True, limit=50)
def lead(email, step=0, status="active", due="2026-01-01T00:00:00Z"):
    return con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at) VALUES (?,?,?,?,?,?,'2026-01-01')",
                       (email, "Pat", "Co", status, step, due)).lastrowid
def row(email):
    return con.execute("SELECT * FROM leads WHERE email=?", (email,)).fetchone()
def run():
    try:
        bot.cmd_run(cfg(), con, args)
    except SystemExit:
        pass

# ---------------------------------------------------------------- 1. a stop that arrives during a run
lead("dana@acme.com")
lead("eve@beta.com")
con.commit()
real_unsub = bot.unsub_headers
def stop_arrives(c, sender, l, test=False):            # called just before each send: the live watcher handles Dana's "unsubscribe" now
    if l["email"] == "dana@acme.com":
        other = sqlite3.connect(str(bot.DATA / "outreach.db"))
        other.execute("UPDATE leads SET status='unsubscribed' WHERE email='dana@acme.com'")
        other.execute("INSERT OR IGNORE INTO suppression(email, reason, at) VALUES ('dana@acme.com','unsubscribe','2026-10-24')")
        other.commit()
        other.close()
    return real_unsub(c, sender, l, test)
bot.unsub_headers = stop_arrives
run()
bot.unsub_headers = real_unsub
check("dana@acme.com" not in smtp and "eve@beta.com" in smtp, f"a stop that arrives mid-run: that email isn't sent; others are ({smtp})")
check(row("dana@acme.com")["status"] == "unsubscribed" and row("dana@acme.com")["step"] == 0,
      "…and the run doesn't put the lead back in the sequence")
check(row("eve@beta.com")["step"] == 1, "the other lead moves on as usual")

# the autopilot (bot) path too
smtp.clear()
con.execute("UPDATE campaigns SET autonomy='autonomous', arrive='00:00-23:59'")
import autopilot  # noqa: E402
autopilot.working_day = lambda code, d: True
autopilot.rep(con, "a@d1.com")
con.execute("UPDATE inbox_rep SET ready=1, started_at='2026-01-01', full_limit=20")
lead("fay@gamma.com")
lead("gus@delta.com")
con.commit()
import ops  # noqa: E402
ops.housekeeping = lambda c, k: []
crm.write_settings({"autopilot": {"enabled": True, "hours": [0, 24]}})
def stop_fay(c, sender, l, test=False):
    if l["email"] == "fay@gamma.com":
        other = sqlite3.connect(str(bot.DATA / "outreach.db"))
        other.execute("UPDATE leads SET status='replied' WHERE email='fay@gamma.com'")
        other.commit()
        other.close()
    return real_unsub(c, sender, l, test)
bot.unsub_headers = stop_fay
run()
bot.unsub_headers = real_unsub
check("fay@gamma.com" not in smtp and row("fay@gamma.com")["status"] == "replied",
      f"the bot's parallel round re-checks each lead right before its email goes ({smtp})")
crm.write_settings({"autopilot": {"enabled": False}})

# ---------------------------------------------------------------- 2. do-not-contact always wins
smtp.clear()
lead("hal@eps.com")
con.execute("INSERT INTO suppression(email, reason, at) VALUES ('hal@eps.com','manual','2026-10-24')")   # an old row; the lead still says active
con.commit()
run()
check("hal@eps.com" not in smtp, "an address on do-not-contact gets nothing, even if its lead still says active")
lead("ivy@zeta.com")
con.execute("INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at) VALUES ((SELECT id FROM leads WHERE email='ivy@zeta.com'),0,'a@d1.com','s','b','pending','x')")
bot.suppress(con, "IVY@zeta.com", "unsubscribe")
check(row("ivy@zeta.com")["status"] == "unsubscribed" and
      con.execute("SELECT status FROM outbox WHERE lead_id=(SELECT id FROM leads WHERE email='ivy@zeta.com')").fetchone()[0] == "cancelled",
      "putting an address on do-not-contact stops its lead and withdraws its drafts at once")

# ---------------------------------------------------------------- 3. test mode never erases real stops
crm.write_settings({"test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
bot.sync_mode(con, cfg())
lead("real@kappa.com", step=1)
lead("tested@lambda.com", step=1)
con.execute("UPDATE leads SET first_msg_id='<m1@d1.com>', sender='a@d1.com' WHERE email IN ('real@kappa.com','tested@lambda.com')")
con.commit()
def reply(frm, subject, body, mid):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "a@d1.com"
    m["Subject"] = subject
    m["Message-ID"] = mid
    m.set_content(body)
    return bot.handle_message(cfg(), con, {"email": "a@d1.com", "name": "A"}, m)
reply("Real Person <real@kappa.com>", "Re: Quick question", "Please stop emailing me.", "<r1@k>")
reply("Omar <omar@blacswanken.com>", "Re: [TEST for tested@lambda.com] Quick question", "Unsubscribe", "<t1@b>")
con.commit()
check(row("real@kappa.com")["status"] == "unsubscribed" and row("tested@lambda.com")["status"] == "unsubscribed", "both stop while testing")
crm.write_settings({"test_mode": {"enabled": False}})
bot.sync_mode(con, cfg())
check(row("real@kappa.com")["status"] == "unsubscribed" and con.execute("SELECT 1 FROM suppression WHERE email='real@kappa.com'").fetchone(),
      "going live keeps a real person's stop that arrived during testing")
check(row("tested@lambda.com")["status"] == "active" and not con.execute("SELECT 1 FROM suppression WHERE email='tested@lambda.com'").fetchone(),
      "…and only undoes the stop that came from your own test reply")
check(con.execute("SELECT COUNT(*) FROM received WHERE msg_id='<r1@k>'").fetchone()[0] == 1, "the real reply stays in the conversation")
h = bot.unsub_headers(con, {"email": "a@d1.com"}, {"email": "x@y.com"}, test=True)
check("http" not in h["List-Unsubscribe"], "a test email (it goes to you) carries no real unsubscribe link")

# ---------------------------------------------------------------- 4. stop wording, and replies below the quote
for t in ("Please stop.", "Stop please", "I already asked you to stop.", "Not interested, stop.", "Bitte keine E-Mails mehr", "Kindly remove"):
    check(guard.stop_reason("Re: x", t) == "unsubscribe", f"'{t}' is a stop")
below = "On Mon, 19 Oct 2026, A <a@d1.com> wrote:\n> Hi Pat\n> Not relevant? Reply \"unsubscribe\" and I won't email again.\n\nPlease take me off your list."
check(guard.stop_reason("Re: x", below) == "unsubscribe", "a stop written below the quoted email is seen")
check(guard.stop_reason("Re: x", "On Mon, A wrote:\n> Hi\n> Not relevant? Reply \"unsubscribe\" and I won't email again.\n\nSounds good, Tuesday?") is None,
      "…but our quoted footer alone never is")
for t in ("Feel free to stop by our booth.", "We can't stop hiring. Tell me more.", "Is this a one-stop shop?"):
    check(guard.stop_reason("Re: x", t) is None, f"'{t}' is not a stop")

# ---------------------------------------------------------------- 5. replies are never lost
class FakeIMAP:
    boxes = {}
    searches = []
    def __init__(self, host, port=993, timeout=None, **k):
        self.timeout = timeout
    def login(self, u, p): pass
    def list(self): return "OK", [b'(\\HasNoChildren) "/" "INBOX"']
    def select(self, f, readonly=True): return "OK", [b"1"]
    def uid(self, cmd, *a):
        msgs = FakeIMAP.boxes.get("a@d1.com", [])
        if cmd == "search":
            FakeIMAP.searches.append(a[-1])
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(msgs)))]
        raw = msgs[int(a[0]) - 1]
        return "OK", [(b"1", raw)]
    def logout(self): pass
import imaplib  # noqa: E402
imaplib.IMAP4_SSL = FakeIMAP
lead("ola@theta.com", step=1)
con.execute("UPDATE leads SET first_msg_id='<m2@d1.com>', sender='a@d1.com' WHERE email='ola@theta.com'")
con.commit()
raw = ("From: Ola <ola@theta.com>\r\nTo: a@d1.com\r\nSubject: Re: Quick question – bitte abmelden, café\r\n"
       "Message-ID: <ola1@theta>\r\nIn-Reply-To: <m2@d1.com>\r\n\r\nBitte keine E-Mails mehr. Unsubscribe.\r\n").encode("utf-8")
FakeIMAP.boxes["a@d1.com"] = [raw]
s = {"email": "a@d1.com", "name": "A", "imap_host": "h", "password": "pw"}
bot.process_inbox(cfg(), con, s)
check(row("ola@theta.com")["status"] == "unsubscribed", "a stop with an unencoded non-English subject is handled (it used to crash and be lost)")

lead("pia@iota.com", step=1)
con.commit()
raw2 = b"From: Pia <pia@iota.com>\r\nTo: a@d1.com\r\nSubject: Re: hi\r\nMessage-ID: <pia1@iota>\r\n\r\nPlease remove me.\r\n"
FakeIMAP.boxes["a@d1.com"] = [raw2]
real_handle = bot.handle_message
fails = [2]
def flaky(*a, **k):
    if fails[0]:
        fails[0] -= 1
        raise RuntimeError("database is locked")
    return real_handle(*a, **k)
bot.handle_message = flaky
try:
    bot.process_inbox(cfg(), con, s)
    check(False, "a failed email makes the read fail")
except RuntimeError as e:
    check("couldn't be handled yet" in str(e), "an email that couldn't be handled makes this read count as failed (the inbox won't send yet)")
check(row("pia@iota.com")["status"] == "active" and con.execute("SELECT tries FROM msg_pending WHERE key='<pia1@iota>'").fetchone()[0] == 1,
      "…and it is kept to be tried again, not marked done")
con.execute("UPDATE msg_pending SET at='2020-01-01T00:00:00Z'")
con.commit()
try:
    bot.process_inbox(cfg(), con, s)
except RuntimeError:
    pass
con.execute("UPDATE msg_pending SET at='2020-01-01T00:00:00Z'")
con.commit()
bot.process_inbox(cfg(), con, s)
bot.handle_message = real_handle
check(row("pia@iota.com")["status"] == "unsubscribed" and not con.execute("SELECT 1 FROM msg_pending").fetchone(),
      "once the problem clears, the email is handled: the stop is applied")

lead("quin@kap.com", step=1)
con.commit()
raw3 = b"From: Quin <quin@kap.com>\r\nTo: a@d1.com\r\nSubject: Re: hi\r\nMessage-ID: <q1@kap>\r\n\r\nStop emailing me.\r\n"
FakeIMAP.boxes["a@d1.com"] = [raw3]
bot.handle_message = lambda *a, **k: (_ for _ in ()).throw(ValueError("always broken"))
for _ in range(safety.MAX_TRIES):
    try:
        bot.process_inbox(cfg(), con, s)
    except RuntimeError:
        pass
    con.execute("UPDATE msg_pending SET at='2020-01-01T00:00:00Z'")
    con.commit()
bot.handle_message = real_handle
check(con.execute("SELECT 1 FROM suppression WHERE email='quin@kap.com'").fetchone() and row("quin@kap.com")["status"] == "unsubscribed"
      and con.execute("SELECT COUNT(*) FROM tasks WHERE subject LIKE '%quin@kap.com%couldn''t be handled%'").fetchone()[0] == 1,
      "an email that can never be handled: if it reads like a stop, the sender is stopped anyway, and a person gets a task")

lead("rae@lam.com", step=1)
con.commit()
raw4 = b"From: Rae <rae@lam.com>\r\nTo: a@d1.com\r\nSubject: Re: hi\r\nMessage-ID: <rae1@lam>\r\n\r\nUnsubscribe.\r\n"
FakeIMAP.boxes["a@d1.com"] = [raw4]
con.execute("INSERT INTO seen_messages VALUES ('<rae1@lam>')")                     # claimed, then BSO died before handling it
con.execute("INSERT INTO msg_pending(key, at, tries, inbox) VALUES ('<rae1@lam>','2020-01-01T00:00:00Z',0,'a@d1.com')")
con.commit()
bot.process_inbox(cfg(), con, s)
check(row("rae@lam.com")["status"] == "unsubscribed", "an email claimed just before a crash is handled after the restart")

FakeIMAP.searches.clear()
con.execute("INSERT INTO inbox_read(email, ok_at) VALUES ('a@d1.com', ?) ON CONFLICT(email) DO UPDATE SET ok_at=excluded.ok_at",
            ((datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
con.commit()
bot.process_inbox(cfg(), con, s)
since = datetime.strptime(FakeIMAP.searches[-1].split('"')[1], "%d-%b-%Y")
check((datetime.now() - since).days >= 31, f"after a 30-day outage replies are read from before the outage, not just 14 days back ({FakeIMAP.searches[-1]})")
check(FakeIMAP("h", timeout=60).timeout == 60, "mailbox connections have a time limit (a frozen server can't hang the run)")

# ---------------------------------------------------------------- 6. the team's do-not-contact list
import team  # noqa: E402
lead("sam@mu.com")
lead("tia@nu.com")
con.commit()
st = team.state()
st.pop("dnc_ready", None)
team.save_state(st)
check(team.apply_dnc(con, [team.dnc_hash("SAM@mu.com"), "not-a-hash"], last=False) == 1 and row("sam@mu.com")["status"] == "unsubscribed"
      and row("tia@nu.com")["status"] == "active", "an address on the team's list stops that lead here; others are untouched")
check(not team.dnc_ready(), "the list isn't complete until its last part arrives")
team.apply_dnc(con, [], last=True)
check(team.dnc_ready(), "…then it is")
import hashlib  # noqa: E402
check(team.dnc_hash("Sam@MU.com") == team.dnc_hash("sam@mu.com") == hashlib.sha256(b"bso-dnc:sam@mu.com").hexdigest()[:16]
      and "@" not in team.dnc_hash("sam@mu.com"), "a one-way code (the same for any capitalisation) travels, not the address")
real_joined = team.joined
team.joined = lambda: True
con.execute("DELETE FROM leads WHERE email='sam@mu.com'")
con.execute("DELETE FROM suppression WHERE email='sam@mu.com'")
con.commit()
why = []
check(bot.add_lead(con, {"email": "sam@mu.com", "first_name": "Sam"}, "active", why) is None and "team's do-not-contact" in " ".join(why),
      "an import can't bring back someone on the team's list")
team.joined = real_joined

os.environ["HUB_HOME"] = str(tmp / "hub")
import hub, hub_bot  # noqa: E402,E401
hub.prepare(str(tmp / "hub"))
hc = sqlite3.connect(str(hub.DB_PATH))
hc.row_factory = sqlite3.Row
for i, (nm, role) in enumerate((("Bot", "bot"), ("Ali", "supervisor"), ("Sara", "supervisor")), start=1):
    hc.execute("INSERT INTO users(id, name, email, username, role, status, pw_hash, created_at) VALUES (?,?,?,?,?,'active','x','2026-01-01')",
               (i, nm, f"{nm.lower()}@b.com", nm.lower(), role))
hc.commit()
hc.execute("UPDATE users SET last_seen='2026-10-24T00:00:00Z' WHERE id IN (1,2)")     # Sara (3) has never signed in
hc.commit()
codes = [team.dnc_hash(f"p{i}@y.com") for i in range(1500)]
hub_bot.ingest_extra(hc, 2, {"type": "__dnc", "data": {"h": codes[:1200] + ["zzz", "not hex at all!"]}})
check(hc.execute("SELECT COUNT(*) FROM dnc").fetchone()[0] == 1200, "the console keeps the valid codes a laptop reports")
hub_bot.dnc_catch_up(hc, 1)
hub_bot.dnc_catch_up(hc, 1)
hub_bot.dnc_catch_up(hc, 3)
nts = [json.loads(r["item"]) for r in hc.execute("SELECT item FROM notices WHERE user_id=1")]
check(len(nts) == 1 and len(nts[0]["h"]) == 1000 and not nts[0]["last"], "the bot gets the codes in parts of 1,000, one waiting part at a time")
check(not hc.execute("SELECT 1 FROM notices WHERE user_id=3").fetchone(), "nothing piles up for someone who has never signed in")
nid = hc.execute("SELECT id FROM notices WHERE user_id=1").fetchone()[0]
hub.ingest(hc, 1, {"acked": [f"ho:nt:{nid}"]})
hub_bot.ingest_extra(hc, 2, {"type": "__dnc", "data": {"h": codes[1200:]}})
hub_bot.dnc_catch_up(hc, 1)
nts = [json.loads(r["item"]) for r in hc.execute("SELECT item FROM notices WHERE user_id=1 AND delivered=0")]
check(len(nts) == 1 and nts[0]["h"] == codes[1000:1500] and nts[0]["last"], "after it confirms a part, the next one follows, with codes added meanwhile")
hub_bot.ingest_extra(hc, 1, {"type": "__dnc_req", "data": {}})
hub_bot.dnc_catch_up(hc, 1)
nts = [json.loads(r["item"]) for r in hc.execute("SELECT item FROM notices WHERE user_id=1 AND delivered=0")]
check(len(nts) == 1 and nts[0]["h"] == codes[:1000], "a restored or rebuilt bot that asks for the whole list starts again from the first code")
check(len(json.dumps(nts[0])) < 25_000, f"a part stays small for the Google relay ({len(json.dumps(nts[0]))} bytes)")

out = []
real_print = print
import builtins  # noqa: E402
team.joined = lambda: True
team.works_as_bot = lambda: True
team.is_bot = lambda: True
team.gate = lambda c, v="": (True, "", 0)
team.policy = lambda: {"role": "bot"}
st = team.state()
st.pop("dnc_ready", None)
team.save_state(st)
check(not team.dnc_must_wait(con), "a bot that was already sending keeps going while the list arrives (an upgrade never halts it)")
st = team.state()
st["dnc_must_wait"] = True                       # as after a restore
team.save_state(st)
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
smtp.clear()
lead("uma@xi.com")
con.commit()
try:
    run()
finally:
    builtins.print = real_print
check("uma@xi.com" not in smtp and any("do-not-contact list" in l for l in out), "a restored or new bot sends no first email until it has the team's list")
lead("vic@omi.com", step=1)
con.commit()
smtp.clear()
out.clear()
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
try:
    run()
finally:
    builtins.print = real_print
check("vic@omi.com" in smtp and "uma@xi.com" not in smtp, f"…but follow-ups still go meanwhile, checked against this laptop's own list ({smtp})")
smtp.clear()
st = team.state()
st["dnc_ready"] = "2026-10-24"
team.save_state(st)
team.joined = real_joined

# ---------------------------------------------------------------- 7. a run left "running" never blocks the bot again
crm_con = bot.db_connect("outreach.db")
crm_con.execute("INSERT INTO jobs(cmd, args, status, output, started_at) VALUES ('run','','running','','2026-10-22T16:23:00Z')")
crm_con.commit()
check(crm.job_running(), "a leftover 'running' row (BSO died during a run)")
check(crm.clear_stale_jobs() == 1 and not crm.job_running(), "it is closed as interrupted when BSO starts")
crm_con.execute("INSERT INTO jobs(cmd, args, status, output, started_at) VALUES ('run','','running','','2026-10-22T16:23:00Z')")
crm_con.commit()
crm.bot_argv = lambda *a, **k: [sys.executable, "-c", "print('ok')"]
jid, already = crm.start_job(crm_con, "run")
check(not already, "and a new run starts even if a leftover row appears later")
for _ in range(100):
    if not crm.job_running():
        break
    time.sleep(0.1)
crm.JOB_MAX_MINUTES = 0.03
jid = crm_con.execute("INSERT INTO jobs(cmd, args, status, output, started_at) VALUES ('run','','running','','x')").lastrowid
crm_con.commit()
t0 = time.time()
crm._run_job(jid, [sys.executable, "-c", "import time; time.sleep(60)"])
j = crm_con.execute("SELECT status, output FROM jobs WHERE id=?", (jid,)).fetchone()
check(j["status"] == "failed" and "stuck" in j["output"] and time.time() - t0 < 20, "a stuck run is stopped after its time limit and marked failed")

# ---------------------------------------------------------------- 8. the "online but not sending" alarm
now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
u = {"status": "active", "last_seen": iso(now), "bot_report": json.dumps({
    "due_now": 40, "last_sent_at": iso(now - timedelta(hours=5)), "killed": False, "breakers": [], "inboxes": [],
    "last_run": {"status": "running", "started_at": iso(now - timedelta(hours=40))}, "why_not": [], "test_mode": False})}
weekday_noon = datetime(2026, 10, 21, 12, 0)
why = hub_bot.not_sending(u, loc=weekday_noon)
check(why and "stuck" in why and "40 lead(s)" in why, "online, leads due, nothing sent for 5 hours: the owner is alerted, with the likely cause")
check(hub_bot.not_sending(u, loc=datetime(2026, 10, 24, 12, 0)) is None, "not at weekends")
u2 = dict(u, bot_report=json.dumps(dict(json.loads(u["bot_report"]), last_sent_at=iso(now - timedelta(minutes=30)))))
check(hub_bot.not_sending(u2, loc=weekday_noon) is None, "not while it is sending")
u3 = dict(u, bot_report=json.dumps(dict(json.loads(u["bot_report"]), test_mode=True)))
check(hub_bot.not_sending(u3, loc=weekday_noon) is None, "not in test mode")

# ---------------------------------------------------------------- 9. after the release audit
# test mode: a real person's ordinary reply survives going live; your own address is never blocked; nothing leaves while testing
crm.write_settings({"test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}})
bot.sync_mode(con, cfg())
lead("hot@real.com", step=1)
lead("mine@test.com", step=1)
con.execute("UPDATE leads SET first_msg_id='<m9@d1.com>', sender='a@d1.com' WHERE email IN ('hot@real.com','mine@test.com')")
con.commit()
reply("Hot Lead <hot@real.com>", "Re: Quick question", "Interested! Please call me tomorrow at 10am.", "<hot1@r>")
reply("Omar <omar@blacswanken.com>", "Re: [TEST for mine@test.com] Quick question", "Stop emailing me.", "<t9@b>")
con.commit()
check(not con.execute("SELECT 1 FROM suppression WHERE email='omar@blacswanken.com'").fetchone(), "your own test reply never blocks your own address")
import team as _team  # noqa: E402
sent_body = {}
real_post, real_joined2 = _team._post, _team.joined
_team._post = lambda url, path, payload, token=None, timeout=20: (sent_body.update(payload) or (200, {"policy": {}}))
_team.joined = lambda: True
st = _team.state()
st["hub"] = "http://127.0.0.1:1"
_team.save_state(st)
_team._sync(con)
codes_sent = [h for e in sent_body.get("events", []) if e.get("type") == "__dnc" for h in e["data"]["h"]]
check(_team.dnc_hash("mine@test.com") not in codes_sent and _team.dnc_hash("omar@blacswanken.com") not in codes_sent,
      "while testing, no stop is reported to the team list (it may be undone)")
tasks_before = con.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=(SELECT id FROM leads WHERE email='hot@real.com')").fetchone()[0]
crm.write_settings({"test_mode": {"enabled": False, "redirect_to": ""}})          # even with the test address cleared first
bot.sync_mode(con, cfg())
check(row("hot@real.com")["status"] == "replied" and tasks_before > 0 and
      con.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=(SELECT id FROM leads WHERE email='hot@real.com')").fetchone()[0] == tasks_before,
      "going live keeps a real person's interested reply: their state and their follow-up task stay (no cold email to them)")
check(row("mine@test.com")["status"] == "active" and not con.execute("SELECT 1 FROM suppression WHERE email='mine@test.com'").fetchone(),
      "…and still undoes your own test stop (the test address was remembered)")
sent_body.clear()
_team._sync(con)
codes_sent = [h for e in sent_body.get("events", []) if e.get("type") == "__dnc" for h in e["data"]["h"]]
check(_team.dnc_hash("real@kappa.com") in codes_sent and _team.dnc_hash("mine@test.com") not in codes_sent,
      "once live, real stops are reported to the team list; undone test stops never are")
sent_body.clear()
_team._sync(con)
check(not [e for e in sent_body.get("events", []) if e.get("type") == "__dnc"], "each stop is reported once")
_team._post, _team.joined = real_post, real_joined2

# an email still being handled keeps the inbox from sending, even when it's looked at again soon
lead("wyn@pi.com", step=1)
con.commit()
FakeIMAP.boxes["a@d1.com"] = [b"From: Wyn <wyn@pi.com>\r\nTo: a@d1.com\r\nSubject: Re: hi\r\nMessage-ID: <w1@pi>\r\n\r\nRemove me please.\r\n"]
bot.handle_message = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
for i in range(2):
    try:
        bot.process_inbox(cfg(), con, s)
        ok = True
    except RuntimeError:
        ok = False
    check(not ok, f"check {i + 1} soon after a failure: the inbox still doesn't count as read")
bot.handle_message = real_handle

# no email to anyone on do-not-contact, whoever sends it
smtp.clear()
try:
    bot.send_email({"email": "a@d1.com", "name": "A", "smtp_host": "h"}, "Hal <hal@eps.com>", "Re: info", "Here is more information.")
    check(False, "blocked")
except bot.Skipped as e:
    check("do-not-contact" in str(e) and not smtp, "an automatic answer or a manual email to a do-not-contact address is refused")
try:
    bot.send_email({"email": "a@d1.com", "name": "A", "smtp_host": "h"}, "x@ok.com", "Hi", "b", cc="hal@eps.com")
    check(False, "blocked cc")
except bot.Skipped:
    check(True, "…also in Cc")
bot.send_email({"email": "a@d1.com", "name": "A", "smtp_host": "h"}, "hal@eps.com", "Handover", "brief", headers={"X-BSO-Kind": "brief"})
check(smtp == ["hal@eps.com"], "BSO's own emails to the team are not prospects and still go")
pid = lead("pax@rho.com", step=2, status="paused")
con.commit()
check(safety.why_not_send(pid, ("nurture", "active", "replied", "done", "paused")) is None, "a check-back email you approved for a paused lead may go")

# unsubscribe codes any of the team's laptops can resolve
key = os.urandom(32)
safety._team_key = lambda: key
t = safety.token_for(con, "zed@sig.com")
lead("zed@sig.com", step=1)
con.execute("DELETE FROM unsub_tokens")                 # a restored or replacement bot never issued it
con.commit()
check(safety.apply_unsubs(con, [{"t": t, "at": "2026-10-24"}]) == ["zed@sig.com"] and row("zed@sig.com")["status"] == "unsubscribed",
      "an unsubscribe click is matched even on a laptop that didn't send that email")
check(safety.token_for(con, "zed@sig.com") == t and "zed" not in t, "the code is the same on every laptop and reveals nothing")

# one run at a time
con.commit()
crm_con.execute("UPDATE jobs SET status='ok' WHERE status='running'")
crm_con.commit()
crm.bot_argv = lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(3)"]
res = []
ths = [threading.Thread(target=lambda: res.append(crm.start_job(bot.db_connect("outreach.db"), "run"))) for _ in range(2)]
[t_.start() for t_ in ths]
[t_.join() for t_ in ths]
check(sorted(r[1] for r in res) == [False, True] and res[0][0] == res[1][0], f"two starts at the same moment: one run ({res})")
lock = bot.acquire_lock()
check(bot.run_in_progress(), "a run's lock is seen from outside it")
crm.PROCS.clear()
crm_con.execute("INSERT INTO jobs(cmd, args, status, output, started_at) VALUES ('run','','running','','x')")
crm_con.commit()
check(crm.clear_stale_jobs() == 0, "a run still going after a restart (it holds the lock) isn't mistaken for a leftover")
lock.close()
check(crm.clear_stale_jobs() >= 1, "once it has ended, its row is closed")
print(f"\nAll {n} stop checks passed.")
