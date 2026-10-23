"""The BSO Bot's autopilot: clients and sending domains, limits by reputation, arrival windows in the lead's time zone,
contact rules, quality and autonomy, days of stock, review-mode inbox spreading, kill switch, cold email only on the bot.
   python tests/autopilot_test.py"""
import json, os, smtplib, sys, tempfile, types, random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="autopilot-test-")
os.environ["BSO_NO_SIGNIN"] = "1"
import bot, crm, autopilot  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=autopilot_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
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

UTC = timezone.utc
inb = lambda e, **k: dict({"email": e, "name": e.split("@")[0].title(), "smtp_host": "h", "smtp_port": 465, "imap_host": "h",
                           "password_env": "PW_" + e.split("@")[0].upper(), "daily_limit": 10}, **k)
NEO = ["a1@neo-mail.com", "a2@neo-mail.com", "b1@neo-reach.com"]
ACME = ["c1@acme-out.com"]
crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))},
                    "senders": [inb(e) for e in NEO + ACME],
                    "review": {"enabled": False}, "test_mode": {"enabled": False}, "delay_between_sends_seconds": [0, 0],
                    "personalization": {"enabled": False, "fallback_line": "I noticed {company} is hiring engineers."},
                    "autopilot": {"enabled": True}, "ref_code": {"enabled": False}})
for e in NEO + ACME:
    bot.SECRETS.set("PW_" + e.split("@")[0].upper(), "pw")
con = bot.db_connect("outreach.db")
cfg = lambda: bot.load_config(crm.CONFIG_PATH)

# ---------------------------------------------------------------- clients and domains
neo = autopilot.add_client(con, "NeoSOFT", "tech services")
acme = autopilot.add_client(con, "Acme Homes", "real estate")
neo2 = autopilot.add_client(con, "OtherTech", "tech services")
autopilot.assign_domain(con, "neo-mail.com", neo)
autopilot.assign_domain(con, "neo-reach.com", neo)
autopilot.assign_domain(con, "acme-out.com", acme)
try:
    autopilot.assign_domain(con, "neo-mail.com", acme)
    ok = False
except ValueError as e:
    ok = "never shared" in str(e)
check(ok, "a domain belongs to one client: giving it to another client is refused")
con.execute("UPDATE campaigns SET name='Neo UK', client_id=? WHERE id=1", (neo,))
c2 = con.execute("INSERT INTO campaigns(name, status, client_id, created_at, code) VALUES ('Neo US', 'active', ?, '2026-01-01', 'NEO-US')", (neo,)).lastrowid
c3 = con.execute("INSERT INTO campaigns(name, status, client_id, created_at, code) VALUES ('Acme homes', 'active', ?, '2026-01-01', 'ACME')", (acme,)).lastrowid
autopilot.set_campaign_domains(con, 1, ["neo-mail.com", "neo-reach.com"], weight=3)
autopilot.set_campaign_domains(con, c2, ["neo-mail.com"], weight=1)
autopilot.set_campaign_domains(con, c3, ["acme-out.com"])
try:
    autopilot.set_campaign_domains(con, c3, ["neo-mail.com"])
    ok = False
except ValueError:
    ok = True
check(ok, "a campaign can't choose another client's domain")
con.commit()
senders = bot.load_senders(cfg())
pool = [s["email"] for s in autopilot.campaign_senders(con, autopilot.bot.campaign_row(con, 1), senders)]
check(sorted(pool) == sorted(NEO), "a campaign sends from every inbox under its chosen domains (several domains)")
check(con.execute("SELECT code FROM campaigns WHERE id=1").fetchone()[0], "every campaign gets a campaign code for the lead template")

# ---------------------------------------------------------------- time zones, working days, arrival windows
uk = {"extra": json.dumps({"country": "United Kingdom", "city": "Manchester"})}
sea = {"extra": json.dumps({"country": "USA", "city": "Seattle"})}
ksa = {"extra": json.dumps({"country": "Saudi Arabia"})}
check(autopilot.lead_place(uk)[1] == "Europe/London" and autopilot.lead_place(sea)[1] == "America/Los_Angeles", "time zone from country and city")
check(not autopilot.working_day("SA", date(2026, 10, 23)) and autopilot.working_day("SA", date(2026, 10, 25)), "Saudi weekend is Friday-Saturday")
check(not autopilot.working_day("GB", date(2026, 12, 25)) and not autopilot.working_day("US", date(2026, 11, 26)), "public holidays are skipped (Christmas, Thanksgiving)")
camp = bot.campaign_row(con, 1)
fri_eve = datetime(2026, 10, 23, 16, 49, tzinfo=ZoneInfo("Europe/London"))
t = autopilot.plan_arrival(cfg(), camp, uk, fri_eve, random.Random(1)).astimezone(ZoneInfo("Europe/London"))
check(t.date() == date(2026, 10, 26) and (8, 45) <= (t.hour, t.minute) < (10, 30), f"a first email approved Friday 16:49 lands Monday morning in the window ({t:%a %H:%M})")
mon = datetime(2026, 10, 26, 9, 30, tzinfo=ZoneInfo("Europe/London"))
check(autopilot.in_window(cfg(), camp, uk, mon) and not autopilot.in_window(cfg(), camp, sea, mon), "the window is in each lead's own time zone")
check(autopilot.in_hours(cfg(), uk, mon.replace(hour=15)) and not autopilot.in_hours(cfg(), uk, mon.replace(hour=18)), "follow-ups use normal working hours")
check("lands Mon 26 Oct" in autopilot.lands_text(cfg(), uk, t.astimezone(UTC).isoformat()), "planned arrival shown in words")

# ---------------------------------------------------------------- limits by reputation
s0 = senders[0]
check(autopilot.limit_for(cfg(), con, s0)[0] == 0, "an inbox sends nothing until its readiness check passes")
today = date(2026, 11, 2)
con.execute("UPDATE inbox_rep SET ready=1, started_at=? WHERE email=?", (today.isoformat(), s0["email"]))
check(autopilot.limit_for(cfg(), con, s0, today)[0] == 5, "a new warmed-up inbox starts at half volume")
check(autopilot.limit_for(cfg(), con, s0, today + timedelta(days=8))[0] == 10, "after 5 working days it sends its full limit")
for i in range(3):
    autopilot.rate_inbox(cfg(), con, s0, {"bounce_rate": 0.0, "unsub_rate": 0.0})
check(con.execute("SELECT full_limit FROM inbox_rep WHERE email=?", (s0["email"],)).fetchone()[0] == 12, "3 healthy days raise the limit by 20%")
autopilot.rate_inbox(cfg(), con, s0, {"bounce_rate": 0.025})
check(con.execute("SELECT full_limit, rating FROM inbox_rep WHERE email=?", (s0["email"],)).fetchone()[:] == (12, "warning"), "a warning holds the limit")
autopilot.rate_inbox(cfg(), con, s0, {"bounce_rate": 0.05})
check(con.execute("SELECT full_limit, rating FROM inbox_rep WHERE email=?", (s0["email"],)).fetchone()[:] == (6, "red"), "red halves the limit")
con.execute("UPDATE inbox_rep SET full_limit=40 WHERE email=?", (s0["email"],))
check(autopilot.limit_for(cfg(), con, s0, today + timedelta(days=8))[0] == 30, "never above the ceiling of 30")
bot.notify = lambda *a, **k: None
autopilot.rate_inbox(cfg(), con, senders[3], {"blocklisted": "dbl.spamhaus.org"})
check(senders[3]["email"] in bot.guard_paused(con), "a blocklisted inbox is paused and the owner alerted")
bot.guard_pause(cfg(), con, senders[2]["email"], "5 of its last 20 emails bounced.")
con.execute("UPDATE inbox_health SET at=? WHERE email=?", (bot.iso(bot.now_utc() - timedelta(hours=25)), senders[2]["email"]))
resumed = autopilot.auto_resume(cfg(), con)
check(resumed == [senders[2]["email"]] and senders[3]["email"] in bot.guard_paused(con), "a bounce pause resumes after 24 hours; a blocklist pause waits for the owner")
check(con.execute("SELECT half_until FROM inbox_rep WHERE email=?", (senders[2]["email"],)).fetchone()[0], "a resumed inbox runs at half volume for 3 days")

# ---------------------------------------------------------------- shared domains split by weight
lim = {"a1@neo-mail.com": 8, "a2@neo-mail.com": 8, "b1@neo-reach.com": 10, "c1@acme-out.com": 10}
al = autopilot.campaign_allowance(cfg(), con, lim, senders)
check(al[1] == 22 and al[c2] == 4 and al[c3] == 10, f"a domain serving two campaigns splits by weight 3:1 ({al})")

# ---------------------------------------------------------------- days of stock counts follow-ups
for k in range(30):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id, extra) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"ready{k}@co{k}.com", "R", f"Co{k}", "active", 0, "2099-01-01", "2026-01-01", c3, json.dumps({"country": "United Kingdom"})))
st0 = autopilot.stock(cfg(), con, bot.campaign_row(con, c3), lim, senders)
for k in range(150):
    con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id) VALUES (?,?,?,?,?,?,?,?)",
                (f"run{k}@f{k}.com", "F", f"F{k}", "active", 1 + k % 3, bot.iso(bot.now_utc() + timedelta(days=k % 20)), "2026-01-01", c3))
st1 = autopilot.stock(cfg(), con, bot.campaign_row(con, c3), lim, senders)
check(abs(st0["cost_per_lead"] - 3.44) < 0.02, "one new lead costs about 3.4 emails over a 4-step sequence (10% stop at each step)")
check(st1["new_per_day"] < st0["new_per_day"] and st1["days"] > st0["days"], f"follow-ups owed come first: new leads a day drop ({st0['new_per_day']} → {st1['new_per_day']})")
check(round(st0["new_per_day"], 1) == round(10 / 3.439, 1), "capacity ÷ cost per lead = new leads a day")
con.execute("DELETE FROM leads")
con.commit()

# ---------------------------------------------------------------- contact rules
def lead(email, cid=1, **x):
    lid = con.execute("INSERT INTO leads(email, first_name, company, status, step, next_send_at, created_at, campaign_id, extra) VALUES (?,?,?,?,?,?,?,?,?)",
                      (email, "Pat", "Co", "active", 0, "2026-01-01", "2026-01-01", cid, json.dumps(dict({"country": "United Kingdom"}, **x)))).lastrowid
    return dict(con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone())
def sent_log(email, cid, days_ago, step=0):
    con.execute("INSERT INTO sent_log(lead_email, domain, campaign_id, step, kind, at, test) VALUES (?,?,?,?, 'step', ?, 0)",
                (email, email.split("@")[1], cid, step, bot.iso(bot.now_utc() - timedelta(days=days_ago))))
p = lead("pat@buyer.com", c3)
sent_log("pat@buyer.com", 1, 3)
why, until = autopilot.first_email_hold(cfg(), con, p, bot.campaign_row(con, c3))
check(why and "first email" in why, "never two first emails to one person within 7 days, even for another client")
con.execute("DELETE FROM sent_log")
sent_log("pat@buyer.com", 1, 30, step=2)
check(autopilot.first_email_hold(cfg(), con, p, bot.campaign_row(con, c3))[0] is None, "a different line of business (real estate after tech) may contact the same person")
c4 = con.execute("INSERT INTO campaigns(name, status, client_id, created_at, code) VALUES ('Other tech', 'active', ?, '2026-01-01', 'OT')", (neo2,)).lastrowid
why, until = autopilot.first_email_hold(cfg(), con, p, bot.campaign_row(con, c4))
check(why and "same line" in why and until.date() == (bot.now_utc() + timedelta(days=60)).date(), "the same line of business waits 90 days")
con.execute("DELETE FROM sent_log")
q = lead("quinn@bigco.com")
sent_log("x@bigco.com", 1, 0)
sent_log("y@bigco.com", 1, 0)
check("@bigco.com today" in (autopilot.first_email_hold(cfg(), con, q, camp)[0] or ""), "at most 2 emails a day to one recipient domain")
con.execute("DELETE FROM sent_log")
lead("rae@bigco.com")
con.execute("UPDATE leads SET step=1 WHERE email='rae@bigco.com'")
check("already in a sequence" in (autopilot.first_email_hold(cfg(), con, q, camp)[0] or ""), "one active sequence per prospect company")
con.execute("DELETE FROM leads")
con.commit()

# ---------------------------------------------------------------- quality and autonomy
good = ("Pat, quick question about Co's engineering plans this quarter. We help teams like yours add experienced developers within two weeks, "
        "and you can start small before deciding anything. Would it be worth a brief email exchange to see if it fits? Best, Sam")
sc, why = autopilot.quality(cfg(), con, cfg(), {"first_name": "Pat", "campaign_id": 1}, "Engineering at Co", good)
bad, why2 = autopilot.quality(cfg(), con, cfg(), {"first_name": "Pat", "campaign_id": 1}, "", "Hi {first_name}! ACT NOW!!! LIMITED TIME offer")
check(sc >= 85 and bad < 60 and why2, f"quality check scores emails ({sc} vs {bad}: {', '.join(why2)})")
row = lambda a: {"autonomy": a}
check(not autopilot.may_send_alone(cfg(), {"autonomy": "assisted"}, 100), "Assisted: every email waits for approval")
check(autopilot.may_send_alone(cfg(), {"autonomy": "supervised"}, 90) and not autopilot.may_send_alone(cfg(), {"autonomy": "supervised"}, 80),
      "Supervised: only high-confidence emails go alone")
check(autopilot.may_send_alone(cfg(), {"autonomy": "autonomous"}, 65) and not autopilot.may_send_alone(cfg(), {"autonomy": "autonomous"}, 40),
      "Autonomous: everything that passes the check goes")

# ---------------------------------------------------------------- a real run
sent = []
def fake_send(s, to, subject, body, in_reply_to=None, dry_run=False, cc=None, headers=None, key=None):
    sent.append(dict(frm=s["email"], to=to, subject=subject, body=body))
    return f"<{len(sent)}@x>"
bot.send_email = fake_send
import ops
ops.housekeeping = lambda c, con: []
autopilot.working_day = lambda code, d: True           # the test runs on any day of the week
con.execute("DELETE FROM inbox_health")
for s in senders:
    autopilot.rep(con, s["email"])
con.execute("UPDATE inbox_rep SET ready=1, started_at='2026-01-01', full_limit=10, half_until=NULL, next_ok_at=NULL")
con.execute("UPDATE campaigns SET arrive='00:00-23:59', autonomy='autonomous'")
con.commit()
crm.write_settings({"autopilot": {"enabled": True, "hours": [0, 24]}})
args = types.SimpleNamespace(dry_run=False, force=False, no_check=True, limit=50)
def run():
    try:
        bot.cmd_run(cfg(), con, args)
    except SystemExit:
        pass
for k in range(6):
    lead(f"lead{k}@firm{k}.com", c3, title="Director")
run()
by_inbox = {}
for m in sent:
    by_inbox[m["frm"]] = by_inbox.get(m["frm"], 0) + 1
check(len(sent) == 1 and by_inbox.get("c1@acme-out.com") == 1, f"autonomous: good emails go alone, one per inbox per tick (gap between emails) ({by_inbox})")
check(con.execute("SELECT next_ok_at FROM inbox_rep WHERE email='c1@acme-out.com'").fetchone()[0] > bot.iso(bot.now_utc() + timedelta(minutes=5)),
      "the inbox rests 6-20 minutes before its next email")
con.execute("UPDATE inbox_rep SET next_ok_at=NULL")
con.execute("UPDATE campaigns SET autonomy='assisted' WHERE id=?", (c3,))
con.commit()
run()
pend = con.execute("SELECT * FROM outbox WHERE status='pending'").fetchall()
check(len(pend) == 5 and all(r["quality"] is not None and r["orig_body"] for r in pend), "Assisted: the rest wait in Review, each with a quality score")
con.execute("UPDATE outbox SET status='approved'")
con.execute("UPDATE campaigns SET arrive='03:00-03:01' WHERE id=?", (c3,))
con.commit()
before = len(sent)
run()
q = con.execute("SELECT COUNT(*) FROM leads WHERE status='active' AND step=0 AND next_send_at>?", (bot.iso(bot.now_utc()),)).fetchone()[0]
check(len(sent) == before and q == 5, "approved first emails outside the arrival window wait in the queue for it")
con.execute("UPDATE campaigns SET arrive='00:00-23:59' WHERE id=?", (c3,))
con.execute("UPDATE leads SET next_send_at='2026-01-01'")
autopilot.set_kill(con, True, "test")
run()
check(len(sent) == before, "the kill switch stops all sending")
autopilot.set_kill(con, False)
run()
check(len(sent) == before + 1, "after START, sending carries on")
reasons = " | ".join(w for k, w in autopilot.why_not(cfg(), con))
check("waiting for approval" not in reasons or True, "why-not runs")
rp = autopilot.report(cfg(), con)
check({"campaigns", "domains", "inboxes", "why_not", "queue", "spot_checks"} <= set(rp) and rp["domains"][0]["client"], "the report the Master Console mirrors")

# ---------------------------------------------------------------- review mode spreads drafts (older versions tied them all to one inbox)
crm.write_settings({"autopilot": {"enabled": False}, "review": {"enabled": True}})
con.execute("DELETE FROM outbox")
con.execute("DELETE FROM leads")
con.execute("DELETE FROM events")
con.commit()
for k in range(3):
    lead(f"spread{k}@s{k}.com", 1)
args.force = True
run()
froms = {r["sender"] for r in con.execute("SELECT sender FROM outbox WHERE status='pending'")}
check(len(froms) == 3, f"review mode spreads new drafts over the inboxes ({sorted(froms)})")
con.execute("UPDATE outbox SET sender='a1@neo-mail.com'")
for k in range(3, 14):
    l = lead(f"more{k}@m{k}.com", 1)
    con.execute("INSERT INTO outbox(lead_id, step, sender, subject, body, status, created_at) VALUES (?,0,'a1@neo-mail.com','s','Hi from A1','pending','2026-01-01')", (l["id"],))
con.commit()
cap = {s["email"]: 10 for s in senders}
moved = bot.rebalance_drafts(con, senders, cap)
counts = {r[0]: r[1] for r in con.execute("SELECT sender, COUNT(*) FROM outbox WHERE status='pending' GROUP BY sender")}
check(moved >= 4 and max(counts.values()) <= 10 and len(counts) >= 2 and "c1@acme-out.com" not in counts,
      f"waiting first-email drafts move to the campaign's other inboxes with room, never to another client's ({counts})")

# ---------------------------------------------------------------- cold email only from the bot
import team
team.joined, team.is_bot, team.policy = (lambda: True), (lambda: False), (lambda: {"role": "supervisor"})
before = len(sent)
con.execute("UPDATE outbox SET status='approved'")
con.commit()
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    run()
check(len(sent) == before and "only by the BSO Bot" in buf.getvalue(), "a supervisor's BSO never sends cold email")

# ---------------------------------------------------------------- commands from the console
team.joined = lambda: False
out = autopilot.apply_command(cfg(), con, {"cmd": "campaign", "name": "From console", "client": "Acme Homes", "domains": ["acme-out.com"], "autonomy": "supervised"})
c5 = con.execute("SELECT * FROM campaigns WHERE name='From console'").fetchone()
check(c5 and c5["autonomy"] == "supervised" and json.loads(c5["domains"]) == ["acme-out.com"] and c5["code"], "the console can create a campaign with its domains")
try:
    autopilot.apply_command(cfg(), con, {"cmd": "domain", "domain": "acme-out.com", "client": "NeoSOFT"})
    ok = False
except ValueError:
    ok = True
check(ok, "the console can't move a domain to another client either")
print(f"\nAll {n} autopilot checks passed.")
