"""Hundreds of inboxes on the BSO Bot: 1000 inboxes on 50 domains for 2 clients. Bulk add, update and remove; a round
sends at most one email per inbox and sends in parallel; replies are read in parallel; live connections are capped;
"why not more emails" stays readable; the console accepts setup for 1000 inboxes.
   python tests/scale_test.py"""
import json, os, sys, tempfile, threading, time, types
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="scale-test-")
os.environ["BSO_NO_SIGNIN"] = "1"
import bot, crm, autopilot  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=scale_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [{"email": "first@d0.com", "name": "First", "smtp_host": "h", "imap_host": "h", "password_env": "SENDER1_PASSWORD"}],
                    "review": {"enabled": False}, "test_mode": {"enabled": False}, "ref_code": {"enabled": False},
                    "personalization": {"enabled": False, "fallback_line": "I noticed {company} is growing."}, "autopilot": {"enabled": True, "hours": [0, 24]}})
w = crm.app.test_client()
lines = "\n".join(f"s{i}@d{i % 50}.com, Sender {i}, pw{i}" for i in range(1000))
t0 = time.time()
r = w.post("/settings/inboxes/bulk", data={"lines": lines, "smtp_host": "mail.x", "smtp_port": "465", "imap_host": "mail.x", "daily_limit": "20"},
           follow_redirects=True)
snd = crm.cfg()["senders"]
check(len(snd) == 1001 and "Added 1000 inbox" in r.get_data(as_text=True), f"1000 inboxes added in one go ({time.time() - t0:.1f}s)")
t0 = time.time()
page = w.get("/settings?tab=inbox").get_data(as_text=True)
check("1001 inboxes" in page and 'name="email500"' not in page and time.time() - t0 < 15, f"the inbox settings show a table, not 1000 forms ({time.time() - t0:.1f}s)")
r = w.post("/settings/inboxes/bulk", data={"lines": "s5@d5.com, New Name, newpw", "smtp_host": "", "imap_host": ""}, follow_redirects=True)
check(next(x for x in crm.cfg()["senders"] if x["email"] == "s5@d5.com")["name"] == "New Name" and "updated 1" in r.get_data(as_text=True),
      "an existing inbox is updated in bulk (new name and password)")
r = w.post("/settings/inboxes/remove", data={"emails": "@d49.com\ns0@d0.com"}, follow_redirects=True)
check(len(crm.cfg()["senders"]) == 1001 - 21 and "Removed 21" in r.get_data(as_text=True), "inboxes removed by address or a whole @domain")

con = bot.db_connect("outreach.db")
cfg = lambda: bot.load_config(crm.CONFIG_PATH)
a = autopilot.add_client(con, "NeoSOFT", "tech services")
b = autopilot.add_client(con, "Acme Homes", "real estate")
for d in range(49):
    autopilot.assign_domain(con, f"d{d}.com", a if d < 30 else b)
con.execute("UPDATE campaigns SET name='Neo', client_id=? WHERE id=1", (a,))
c2 = con.execute("INSERT INTO campaigns(name, status, client_id, created_at) VALUES ('Acme', 'active', ?, '2026-01-01')", (b,)).lastrowid
autopilot.set_campaign_domains(con, 1, [f"d{d}.com" for d in range(30)])
autopilot.set_campaign_domains(con, c2, [f"d{d}.com" for d in range(30, 49)])
con.execute("UPDATE campaigns SET autonomy='autonomous', arrive='00:00-23:59'")
for s in bot.load_senders(cfg()):
    autopilot.rep(con, s["email"])
con.execute("UPDATE inbox_rep SET ready=1, started_at='2026-01-01', full_limit=20")
import json as _j
for k in range(3000):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id, extra) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"lead{k}@co{k}.com", "Pat", f"Co{k}", "active", 0, "2026-01-01", "2026-01-01", 1 if k % 3 else c2,
                 _j.dumps({"country": "United Kingdom", "title": "Director"})))
con.commit()

sent, lock, active, peak = [], threading.Lock(), [0], [0]
def fake_send(s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None):
    with lock:
        active[0] += 1
        peak[0] = max(peak[0], active[0])
    time.sleep(0.05)                          # a mail server's answer time
    with lock:
        active[0] -= 1
        sent.append(s["email"])
    return f"<{len(sent)}@x>"
bot.send_email = fake_send
bot.notify = lambda *a, **k: None
import ops
ops.housekeeping = lambda c, con: []
autopilot.working_day = lambda code, d: True
args = types.SimpleNamespace(dry_run=False, force=False, no_check=True, limit=50)
t0 = time.time()
try:
    bot.cmd_run(cfg(), con, args)
except SystemExit:
    pass
took = time.time() - t0
per = {}
for e in sent:
    per[e] = per.get(e, 0) + 1
check(len(sent) > 900 and max(per.values()) == 1, f"one round: {len(sent)} emails, never more than one per inbox")
check(peak[0] > 4, f"emails are sent in parallel (up to {peak[0]} at once)")
check(took < 120, f"the round takes {took:.0f}s for {len(sent)} emails")
doms_a = {e.split("@")[1] for e in sent if int(e.split("@")[1][1:-4]) < 30}
check(all(int(e.split("@")[1][1:-4]) < 49 for e in sent), "nothing goes out from removed inboxes")
why = autopilot.why_not(cfg(), con)
check(len(why) < 40, f"'why not more emails' stays readable with hundreds of inboxes ({len(why)} lines)")
rp = autopilot.report(cfg(), con)
check(len(rp["inboxes"]) == 980 and len(json.dumps(rp)) < 2_000_000, f"the console report covers every inbox ({len(json.dumps(rp)) // 1024} KB)")

import watcher
started = []
class FakeW:
    def __init__(self, cp, s, allowed):
        self.key = (s["email"].lower(), s.get("imap_host"), s.get("password"))
        self.stop = threading.Event()
    def start(self): started.append(1)
    def is_alive(self): return True
watcher.Watcher = FakeW
for s in crm.cfg()["senders"]:
    bot.SECRETS.set(s["password_env"], "pw")
bot.SECRETS.apply_to_environ()
watcher.reconcile(crm.CONFIG_PATH, lambda: True)
check(len(started) == watcher.LIVE_MAX, f"live connections are capped at {watcher.LIVE_MAX}; the rest are read every few minutes")

checked, lock2 = [], threading.Lock()
def fake_process(cfg_, c, s, recheck=False, days=None):
    time.sleep(0.01)
    with lock2:
        checked.append(s["email"])
    return 0
bot.process_inbox = fake_process
bot.recheck_once = lambda cfg_, c, s: 0
t0 = time.time()
bot.cmd_check(cfg(), con, args)
check(len(checked) == 980 and time.time() - t0 < 30, f"replies are read from 980 inboxes in {time.time() - t0:.1f}s (in parallel)")

os.environ["HUB_HOME"] = tempfile.mkdtemp(prefix="scale-hub-")
import hub
ib, bad = hub.parse_inbox_lines("\n".join(f"x{i}@h{i % 40}.com, X {i}, pw" for i in range(1200)))
check(len(ib) == 1200 and not bad, "the console takes setup lines for more than 1000 inboxes")
print(f"\nAll {n} scale checks passed.")
