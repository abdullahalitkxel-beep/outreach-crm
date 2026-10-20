"""Referral capture, Instantly auto-pull (against a stand-in Instantly API) and web discovery of published
emails (against a stand-in AI and web).     python tests/lead_growth_test.py"""
import json, os, sys, tempfile, threading, types
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="growth-test-")
os.environ["INSTANTLY_API_BASE"] = "http://127.0.0.1:8796/api/v2"
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
import bot  # noqa: E402

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=lead_growth_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
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

con = bot.db_connect("outreach.db")
cfg = bot.load_config(str(bot.DATA / "config.yaml"))
cfg["senders"] = [{"email": "cole.reed@getneosofttech.com", "name": "Cole Reed", "daily_limit": 10}]
cfg["handover"] = {"enabled": False}
bot.notify = lambda *a, **k: None
sender = {"email": "cole.reed@getneosofttech.com", "name": "Cole Reed"}

# ---------------- referrals
con.execute("INSERT INTO leads(email, first_name, company, website, extra, status, step, created_at, first_subject) "
            "VALUES ('john@acme.com','John','Acme','acme.com',?, 'active',1,'2026-10-01','Quick question')", (json.dumps({"last_name": "Smith"}),))
con.execute("INSERT INTO leads(email, first_name, company, website, extra, status, step, created_at) VALUES ('kate@globex.com','Kate','Globex','globex.com','{}','active',1,'2026-10-01')")
con.commit()
def reply(frm, text):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = frm, "Re: Quick question", f"<{frm}-{len(text)}>"
    m.set_content(text)
    bot.handle_message(cfg, con, sender, m)

reply("John Smith <john@acme.com>", "Thanks, but I'm not the right person. Please contact Sarah Lee (sarah.lee@acme.com), she handles engineering partnerships.\n\n"
      "On Tue, Oct 20, 2026 at 9:00 AM Cole Reed <cole.reed@getneosofttech.com> wrote:\n> write to info@acme.com or bob@acme.com")
sarah = con.execute("SELECT * FROM leads WHERE email='sarah.lee@acme.com'").fetchone()
check(sarah and sarah["first_name"] == "Sarah" and json.loads(sarah["extra"])["last_name"] == "Lee" and sarah["status"] == "active",
      "referred colleague added as a lead with her name, in the sequence")
check(not con.execute("SELECT 1 FROM leads WHERE email IN ('bob@acme.com','info@acme.com')").fetchone(), "addresses in the quoted old email are ignored")
x = json.loads(sarah["extra"])
check(x["referred_by"] == "John Smith" and x["referred_by_email"] == "john@acme.com", "the new lead remembers who referred her")
subj, body, _ = bot.compose(cfg, con, dict(sarah), sender, 0)
check(subj == "John suggested I reach out" and body.split("\n")[2] == "John Smith suggested I get in touch with you.", "her first email opens with the referral")
check(con.execute("SELECT 1 FROM events WHERE type='reply' AND detail='referral'").fetchone() is not None, "the reply is recorded as a referral")
reply("Kate <kate@globex.com>", "Not interested right now.\n\nKate Jones | Globex | kate@globex.com | ops@globex.com | mike.ross@globex.com")
check(not con.execute("SELECT 1 FROM leads WHERE email='mike.ross@globex.com'").fetchone(), "emails in a signature without a 'talk to…' are not treated as referrals")

# ---------------- Instantly (stand-in API)
from flask import Flask, jsonify, request  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
fake = Flask("fake-instantly")
state = {"polls": 0, "bodies": []}
LEADS = [{"id": f"id{i}", "email": f"lead{i}@company{i}.com", "first_name": f"Ann{i}", "last_name": "Lee", "company_name": f"Company {i}",
          "website": f"company{i}.com", "job_title": "CTO", "payload": {"linkedIn": f"https://linkedin.com/in/ann{i}"}} for i in range(150)]
LEADS[3]["email"] = "john@acme.com"              # already in BSO

@fake.post("/api/v2/supersearch-enrichment/count-leads-from-supersearch")
def f_count():
    state["bodies"].append(request.json)
    return jsonify(number_of_leads=1234)
@fake.post("/api/v2/supersearch-enrichment/enrich-leads-from-supersearch")
def f_enrich():
    state["bodies"].append(request.json)
    return jsonify(id="e1", resource_id="L1", resource_type=2)
@fake.get("/api/v2/supersearch-enrichment/<rid>")
def f_status(rid):
    state["polls"] += 1
    return jsonify(exists=True, in_progress=state["polls"] < 2)
@fake.post("/api/v2/leads/list")
def f_list():
    b = request.json
    assert b["list_id"] == "L1"
    start = int(b.get("starting_after", "id-1")[2:]) + 1 if b.get("starting_after") else 0
    page = LEADS[start:start + b["limit"]]
    return jsonify(items=page, next_starting_after=page[-1]["id"] if start + b["limit"] < len(LEADS) else None)
@fake.post("/api/v2/email-verification")
def f_verify():
    e = request.json["email"]
    return jsonify(email=e, verification_status="invalid" if e.startswith("bad") else "verified", catch_all=False)
srv = make_server("127.0.0.1", 8796, fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import instantly  # noqa: E402
cfg["instantly"] = {"titles": ["CTO", "VP Engineering"], "countries": ["United States"], "levels": ["C-Level"], "sizes": ["100 - 250"],
                    "industries": ["Software & Internet"], "per_pull": 150, "auto": True, "auto_below_days": 3}
try:
    instantly.count(cfg)
    check(False, "a missing API key is reported clearly")
except instantly.InstantlyError as e:
    check("API key" in str(e), "a missing API key is reported clearly")
bot.SECRETS.set(instantly.KEY, "inst-key")
check(instantly.count(cfg) == 1234, "counts matching people")
f = state["bodies"][-1]["search_filters"]
check(f["title"]["include"] == ["CTO", "VP Engineering"] and f["locations"] == {"include": [{"country": "United States"}]}
      and f["industry"] == {"include": ["Software & Internet"]} and f["employeeCount"] == ["100 - 250"], "filters are sent in Instantly's format")
before = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
con.execute("UPDATE leads SET status='done'"); con.commit()            # no leads left: the bot should start an import itself
note = instantly.maybe_pull(cfg, con)
check("started a new import" in note and state["bodies"][-1]["limit"] == 150 and state["bodies"][-1]["skip_rows_without_email"], "low on leads: the bot starts an import itself")
note = instantly.maybe_pull(cfg, con)
check("new lead" not in note and con.execute("SELECT status FROM instantly_pulls").fetchone()[0] == "running", "while Instantly is still working, nothing is imported")
note = instantly.maybe_pull(cfg, con)
added = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] - before
check(added == 149 and "149 new lead" in note, "finished import collected across pages: 149 added, 1 already in BSO skipped")
l7 = con.execute("SELECT * FROM leads WHERE email='lead7@company7.com'").fetchone()
x7 = json.loads(l7["extra"])
check(l7["first_name"] == "Ann7" and l7["company"] == "Company 7" and x7["title"] == "CTO" and "linkedin.com/in/ann7" in x7.get("linkedin", ""), "names, company, title and LinkedIn come across")
check("started" not in instantly.maybe_pull(cfg, con), "no new import while there are plenty of leads (or one started recently)")

# ---------------- web discovery of published emails (stand-in AI and web)
import discover  # noqa: E402
cfg["discover"] = {"enabled": True, "per_day": 2, "roles": ["CTO"]}
answers = {"acme.com": [{"name": "Priya Shah", "title": "VP Engineering", "email": "priya@acme.com", "source_url": "https://acme.com/team"},
                        {"name": "Tom Fake", "title": "CTO", "email": "tom@acme.com", "source_url": "https://acme.com/team"},
                        {"name": "Ext Person", "title": "CTO", "email": "ext@gmail.com", "source_url": "https://acme.com/team"},
                        {"name": "Bad Addr", "title": "CTO", "email": "bad.addr@acme.com", "source_url": "https://acme.com/press"}]}
calls = []
class FakeMessages:
    def create(self, **kw):
        calls.append(kw)
        dom = kw["messages"][0]["content"].split("(website ")[1].split(")")[0]
        text = "I searched the web.\n" + json.dumps(answers.get(dom, []))
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="server_tool_use"), types.SimpleNamespace(type="text", text=text)])
sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda **k: types.SimpleNamespace(messages=FakeMessages()), BadRequestError=Exception)
pages = {"https://acme.com/team": "<p>Priya Shah, VP Engineering: <a href='mailto:priya@acme.com'>email</a></p>",
         "https://acme.com/press": "<p>Press: bad.addr@acme.com</p>"}
discover.requests = types.SimpleNamespace(get=lambda url, **k: types.SimpleNamespace(status_code=200 if url in pages else 404, text=pages.get(url, "")),
                                          RequestException=Exception)
con.execute("UPDATE leads SET status='active' WHERE email='john@acme.com'"); con.commit()
con.execute("DELETE FROM leads WHERE email LIKE 'lead%'"); con.commit()     # keep the test small: acme, globex
note = discover.maybe_discover(cfg, con)
check(con.execute("SELECT 1 FROM leads WHERE email='priya@acme.com'").fetchone() is not None, "published address, seen on the page, verified: added")
check(not con.execute("SELECT 1 FROM leads WHERE email IN ('tom@acme.com','ext@gmail.com','bad.addr@acme.com')").fetchone(),
      "rejected: not on the page given, not at the company's domain, and Instantly says invalid")
d = con.execute("SELECT * FROM discovered WHERE domain='acme.com'").fetchone()
check(d and d["added"] == 1 and "not found on the page" in d["note"] and "not at the company" in d["note"] and "invalid" in d["note"],
      "the reasons for each rejected address are recorded")
pr = json.loads(con.execute("SELECT extra FROM leads WHERE email='priya@acme.com'").fetchone()[0])
check(pr["source"] == "published at https://acme.com/team" and pr["title"] == "VP Engineering", "each found contact keeps the page it was published on")
tool = calls[0]["tools"][0]
check(tool["type"] == "web_search_20250305" and tool["max_uses"] == 3, "uses the AI's web search tool, at most 3 searches per company")
n_calls = len(calls)
discover.maybe_discover(cfg, con)
check(len(calls) == n_calls, "each company is searched only once, and the daily limit is respected")
print(f"\nAll {n} lead-growth checks passed.")
srv.shutdown()
