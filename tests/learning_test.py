"""Prompt-based writing and the learning engine: fresh emails from the brief with fact and similarity guards,
the AI budget, the funnel, strategy testing, lead scoring, the weekly strategy report and its approval.
    python tests/learning_test.py
"""
import json, os, random, re, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="learn-test-"))
os.environ["OUTREACH_HOME"] = str(tmp / "bso")
os.environ["HUB_HOME"] = str(tmp / "hub")
os.environ["BSO_NO_SIGNIN"] = "1"
(tmp / "bso").mkdir()
sys.path.insert(0, str(root))
import bot, writer, learn, crm, hub, vis, convo  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=learning_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

crm.write_settings({"legal_acceptance": {"version": str(crm.cfg().get("legal", {}).get("version", "1.0"))}, "test_mode": {"enabled": False}})
cfg = crm.cfg()
con = bot.db_connect(cfg.get("database", "outreach.db"))
con.execute("INSERT INTO leads(email, first_name, company, website, extra, status, step, created_at) VALUES "
            "('dana@fintechco.com','Dana','FintechCo','','{\"title\": \"CTO\", \"industry\": \"Financial Services\"}','active',0,'2026-10-01')")
con.commit()
lead = dict(con.execute("SELECT * FROM leads WHERE email='dana@fintechco.com'").fetchone())
sender = {"email": "inbox1@getneosofttech.com", "name": "Cole Reed"}
os.environ["ANTHROPIC_API_KEY"] = "test-key"
bot.fetch_site_text = lambda url, limit=4000: ""

# ---- writing from the brief
replies = []
calls = []
def fake_llm(c, key, prompt, max_tokens=200, **k):
    calls.append(prompt)
    return replies.pop(0) if replies else None
bot.llm = fake_llm
b = writer.brief(cfg, con, 1)
check("4,000+ engineers" in " ".join(b["proof_points"]) and b["angles"], "the default brief comes from your offer and facts")
replies[:] = [json.dumps({"subject": "Engineers for FintechCo", "body": "Hi Dana, we cut costs for clients by 73% last year. Worth a 15-minute call? Cole Reed"}),
              json.dumps({"subject": "Engineers for FintechCo", "body": "Hi Dana, FintechCo could add React and Java engineers within two weeks, from a 4,000+ engineer pool. Worth a 15-minute call? Cole Reed"})]
got = writer.write_step(cfg, con, lead, sender, 0, {}, 4)
check(got and "4,000+" in got[1] and "73%" not in got[1], "an invented number (73%) is rejected and the email rewritten")
check("REJECTED" in calls[-1] and "73%" in calls[-1], "the AI is told what was wrong")
check(got[2].count("|") == 2, f"the email records which angle / subject style / ask it used ({got[2]})")
convo.record_sent(con, "<x1@x>", lead, "step", sender["email"], lead["email"], got[0], got[1], step=0, variant=got[2])
con.commit()
replies[:] = [json.dumps({"subject": "x", "body": got[1]}), json.dumps({"subject": "Team for FintechCo", "body": "Hi Dana, a different note entirely about scaling your mobile team with CMMI Level 5 delivery. Open to a short chat? Cole Reed"})]
got2 = writer.write_step(cfg, con, lead, sender, 0, {}, 4)
check(got2 and got2[1] != got[1] and "too similar" in calls[-1], "an email too similar to a recent one is rewritten")
prof = {"arms": {"tech_exec:financial_services": {"speed|company|call": 0.9, "cost|plain|question": 0.1}}, "explore": 0.0}
picks = [writer.pick_strategy(prof, lead, b, random.Random(i))[0] for i in range(200)]
check(picks.count("speed") > 150, "the strategy profile steers which angle is used for this kind of lead")
prof["explore"] = 0.2
picks = [writer.pick_strategy(prof, lead, b, random.Random(i))[0] for i in range(500)]
check(any(p != "speed" for p in picks), "but some emails always try other angles")
replies[:] = [json.dumps({"subject": "Hello", "body": "Hi Dana, a fresh one about QA. Cole Reed"})]
s_, b_, v_ = bot.compose(cfg, con, dict(lead), sender, 0)
check("fresh one about QA" in b_ and "|" in v_, "the run writes fresh emails when the AI is on")
con.execute("UPDATE campaigns SET writing='templates' WHERE id=1")
con.commit()
s_, b_, v_ = bot.compose(cfg, con, dict(lead), sender, 0)
check("|" not in v_, "and uses the templates when the campaign is set to templates")
con.execute("UPDATE campaigns SET writing='ai' WHERE id=1")
con.commit()

# ---- AI budget
class U: input_tokens, output_tokens = 2_000_000, 400_000
bot._record_spend(cfg, "claude-haiku-5-5", U())
sp = bot.ai_spend()
check(sp["calls"] == 1 and abs(sp["usd"] - 4.0) < 0.01, f"AI spending is tracked (${sp['usd']:.2f} this month)")
cfg2 = dict(cfg, ai_budget={"monthly_usd": 3})
check(bot.ai_over_budget(cfg2) and not bot.ai_over_budget(dict(cfg, ai_budget={"monthly_usd": 10})), "a monthly limit stops AI use when reached")
w = crm.app.test_client()
page = w.get("/settings?tab=keys").get_data(as_text=True)
check("AI spending" in page and "$4.00" in page, "Settings shows this month's AI spending")
check("Brief: how the AI writes" in w.get("/campaign/1").get_data(as_text=True), "each campaign has a brief")
w.post("/campaigns/1/brief", data={"offer": "QA teams", "proof_points": "200 QA engineers\nISO 27001", "angles": ["quality"], "writing_on": "1",
                                  "step_purposes": "first\nsecond", "tone": "plain", "rules": "short", "max_words": "90"})
b2 = writer.brief(cfg, con, 1)
check(b2["proof_points"] == ["200 QA engineers", "ISO 27001"] and b2["angles"] == ["quality"] and b2["max_words"] == 90, "the brief can be edited")

# ---- the learning engine (on the console)
hub.prepare(str(tmp / "hub"))
c = hub.app.test_client()
tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
c.post("/setup", data={"_csrf": tok("/setup"), "org": "NeoSOFT", "name": "Omar Ali", "email": "omar@blacswanken.com",
                       "password": "owner-pass-123", "password2": "owner-pass-123"})
c.post("/users/new", data={"_csrf": tok("/users"), "name": "BSO Bot", "email": "bot@getneosofttech.com", "username": "bso.bot", "role": "bot", "password": "bso.bot-pass-1234"})
c.post("/users/new", data={"_csrf": tok("/users"), "name": "Ali Khan", "email": "ali@blacswanken.com", "username": "ali", "role": "supervisor", "password": "ali-pass-12345"})
h = hub.sqlite3.connect(str(hub.DB_PATH)); h.row_factory = hub.sqlite3.Row
bot_id = h.execute("SELECT id FROM users WHERE username='bso.bot'").fetchone()[0]
sup_id = h.execute("SELECT id FROM users WHERE username='ali'").fetchone()[0]
rnd = random.Random(3)
t0 = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
titles = [("CTO", "Financial Services"), ("Head of Engineering", "Software"), ("Founder", "Retail"), ("Office Manager", "Retail")]
for i in range(600):
    title, ind = titles[i % 4]
    e = f"lead{i}@co{i}.com"
    arm = "speed|company|call" if i % 2 else "cost|plain|question"
    good = rnd.random() < (0.12 if arm.startswith("speed") else 0.03) * (2 if title != "Office Manager" else 0.3)
    h.execute("INSERT INTO vis_leads(user_id, email, local_id, first_name, company, campaign, status, temperature, data) VALUES (?,?,?,?,?,?,?,?,?)",
              (bot_id, e, i, f"L{i}", f"Co {i}", "Main campaign", "replied" if good else "active", "hot" if good else None,
               json.dumps({"title": title, "industry": ind, "fit": "A" if title != "Office Manager" else "C"})))
    at = (t0 + timedelta(hours=i)).isoformat(timespec="seconds")
    h.execute("INSERT INTO vis_sent(user_id, local_id, lead_email, campaign_id, step, variant, kind, sender, subject, body, at, angle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (bot_id, i, e, 1, 0, arm, "step", "inbox1@x", "Hi", f"Hi L{i}, body for {arm}", at, arm))
    if good:
        h.execute("INSERT INTO vis_received(user_id, local_id, lead_email, frm, subject, body, at, label, temperature) VALUES (?,?,?,?,?,?,?,?,?)",
                  (bot_id, i, e, e, "Re: Hi", "Let's talk", (t0 + timedelta(hours=i + 30)).isoformat(timespec="seconds"), "meeting_request", "hot"))
        st = rnd.choice(["new", "meeting", "met", "proposal", "won", "lost"])
        h.execute("INSERT INTO handovers(from_user_id, lead_local_id, supervisor_id, label, at, lead_email, lead_name, company, payload, status, status_at, deal_value, lost_reason) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (bot_id, i, sup_id, "meeting_request", at, e, f"L{i}", f"Co {i}", json.dumps({"temperature": "hot"}), st,
                                                         (t0 + timedelta(days=20)).isoformat(timespec="seconds"), 15000 if st == "won" else None, "price" if st == "lost" else None))
h.commit()
js = learn.journeys(h)
fun = {r["key"]: r["n"] for r in learn.funnel(js)}
check(fun["found"] == 600 and fun["emailed"] == 600 and fun["replied"] > 10 and fun["handed"] == fun["replied"] and fun["won"] >= 1,
      f"the journey log builds the funnel (found {fun['found']}, replied {fun['replied']}, won {fun['won']})")
shares, stats = learn.arms(js)
check(shares["all"]["speed|company|call"] > 0.8, f"testing finds the better angle ({shares['all']['speed|company|call'] * 100:.0f}% of volume goes to it)")
wts = learn.train_scoring(js)
check(wts and wts.get("role:other", 0) < wts.get("role:tech_exec", 0), "lead scoring learns that office managers respond less than CTOs")
check(learn.lead_score(wts, json.dumps({"title": "CTO", "industry": "Financial Services", "fit": "A"}), "tech_exec:financial_services") >
      learn.lead_score(wts, json.dumps({"title": "Office Manager", "industry": "Retail", "fit": "C"}), "other:retail"), "and scores a CTO above an office manager")
svg = learn.svg_funnel(learn.funnel(js))
check(svg.startswith("<svg") and "Won" in svg, "the funnel is drawn on a log scale")
prof = learn.build(h, None, {})
check(prof["lessons"] and any("angle" in l for l in prof["lessons"]) and any("price" in l for l in prof["lessons"]), "lessons are drawn from the results")
check(prof["examples"].get("all"), "emails that led to meetings become style examples")
hub.strategy_cycle(h, force=True)
check(json.loads(h.execute("SELECT value FROM meta WHERE key='strategy_pending'").fetchone()[0]).get("version"), "a weekly strategy report is proposed")
ins = c.get("/insights").get_data(as_text=True)
check("From lead to sale" in ins and "Proposed changes" in ins and "Approve" in ins and "Next best step" in ins, "Insights shows the funnel, the proposal and next steps")
check("speed / company / call" in c.get("/insights?by=strategy").get_data(as_text=True), "results can be broken down by angle")
c.post("/insights", data={"_csrf": tok("/insights"), "action": "approve"})
act = json.loads(h.execute("SELECT value FROM meta WHERE key='strategy_active'").fetchone()[0])
check(act.get("approved_by") == "Omar Ali", "nothing changes until the owner approves")
hub._secret_store().set(f"VIS_KEY_{bot_id}", os.urandom(32).hex())
items = hub.decisions_for(h, bot_id, handovers=False)
st = next((x for x in items if x.get("action") == "strategy"), None)
check(st and str(st.get("sealed", "")).startswith("BSOSEAL:") and "speed" not in json.dumps(st), "the approved strategy goes to each BSO, sealed")
print(f"\nAll {n} learning checks passed.")
