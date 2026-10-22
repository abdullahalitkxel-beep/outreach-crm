"""The learning engine (runs on the Master Console, where everyone's data comes together).

Journey log  ->  funnel and breakdowns  ->  what works  ->  strategy profile  ->  every BSO (after you approve it)

  - funnel: found, emailed, replied, positive, handed over, meeting booked, meeting held, proposal, negotiation, won
  - which angle / subject style / call to action leads to meetings and deals, per kind of lead (Thompson sampling,
    ~20% of volume always kept for trying alternatives)
  - winning emails become style examples; weekly lessons (AI when a key is available, rules otherwise)
  - lead scoring: a small logistic model of which leads tend to turn positive, so the bots email the likeliest first
  - next best step for every open deal
"""
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

STAGES = [("found", "Leads found"), ("emailed", "Emailed"), ("replied", "Replied"), ("positive", "Positive (hot or warm)"),
          ("handed", "Handed over"), ("meeting", "Meeting booked"), ("met", "Meeting held"), ("proposal", "Proposal sent"),
          ("negotiation", "Negotiation"), ("won", "Won")]
DEAL_ORDER = ["new", "contacted", "meeting", "met", "proposal", "negotiation", "won"]
WEIGHT = {"positive": 1, "meeting": 3, "met": 4, "proposal": 6, "negotiation": 8, "won": 12}
MIN_TRIALS = 30
EXPLORE = 0.2


def _seg(extra_json):
    try:
        import writer
        return writer.segment({"extra": extra_json or "{}"})
    except Exception:
        return "other:any"


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def journeys(con, since=None):
    """One row per lead the senders worked: where it got to, and the attributes we learn from."""
    out = {}
    leads = con.execute("SELECT * FROM vis_leads WHERE handed_from IS NULL").fetchall()
    for l in leads:
        out[l["email"]] = {"email": l["email"], "user_id": l["user_id"], "campaign": l["campaign"] or "Main campaign", "extra": l["data"] or "{}",
                           "segment": _seg(l["data"]), "temperature": l["temperature"], "stages": {"found"}, "deal_value": l["deal_value"] or 0,
                           "lost_reason": l["lost_reason"], "arm": None, "inbox": None, "first_at": None, "reply_at": None, "came_back": l["came_back"]}
    for s in con.execute("SELECT lead_email, user_id, at, angle, variant, sender, step FROM vis_sent WHERE kind='step' ORDER BY at"):
        j = out.get(s["lead_email"])
        if not j:
            continue
        j["stages"].add("emailed")
        if j["first_at"] is None:
            j["first_at"], j["arm"], j["inbox"], j["variant"] = s["at"], s["angle"] or s["variant"], s["sender"], s["variant"]
            j["hour"] = (_iso(s["at"]) or datetime.now(timezone.utc)).hour
    for r in con.execute("SELECT lead_email, at, temperature, label FROM vis_received ORDER BY at"):
        j = out.get(r["lead_email"])
        if not j:
            continue
        j["stages"].add("replied")
        j["reply_at"] = j["reply_at"] or r["at"]
        if r["temperature"] in ("hot", "warm") or r["label"] in ("interested", "meeting_request", "question", "referral"):
            j["stages"].add("positive")
    for h in con.execute("SELECT h.*, u.name sup FROM handovers h LEFT JOIN users u ON u.id=h.supervisor_id"):
        j = out.get(h["lead_email"])
        if not j:
            continue
        j["stages"] |= {"handed", "positive", "replied"}
        j["supervisor"] = h["sup"]
        j["handed_at"] = h["at"]
        st = h["status"] or "new"
        if st in DEAL_ORDER:
            for k in DEAL_ORDER[2:DEAL_ORDER.index(st) + 1]:
                j["stages"].add(k)
        if st == "won":
            j["won_at"] = h["status_at"]
            j["deal_value"] = h["deal_value"] or j["deal_value"]
        if st == "lost":
            j["lost_reason"] = h["lost_reason"] or j["lost_reason"]
    if since:
        out = {k: v for k, v in out.items() if (v.get("first_at") or "9") >= since}
    return list(out.values())


def funnel(js):
    c = Counter()
    for j in js:
        for s in j["stages"]:
            c[s] += 1
    rows, prev = [], None
    for key, label in STAGES:
        n = c.get(key, 0)
        rows.append({"key": key, "label": label, "n": n, "rate": (f"{n * 100 / prev:.1f}%" if prev else "")})
        prev = n or prev
    return rows


def breakdown(js, by):
    groups = defaultdict(list)
    for j in js:
        k = j.get(by) or "—"
        groups[k].append(j)
    out = []
    for k, g in groups.items():
        c = Counter(s for j in g for s in j["stages"])
        em = c.get("emailed", 0)
        out.append({"key": k, "leads": len(g), "emailed": em, "replied": c.get("replied", 0), "positive": c.get("positive", 0),
                    "meetings": c.get("meeting", 0), "won": c.get("won", 0), "value": sum(j["deal_value"] or 0 for j in g if "won" in j["stages"]),
                    "reply_rate": f"{c.get('replied', 0) * 100 / em:.1f}%" if em else "–", "positive_rate": f"{c.get('positive', 0) * 100 / em:.1f}%" if em else "–",
                    "meeting_rate": f"{c.get('meeting', 0) * 100 / em:.2f}%" if em else "–"})
    return sorted(out, key=lambda r: (-r["meetings"], -r["positive"], -r["emailed"]))


def timings(js):
    def days(a, b):
        da, db = _iso(a), _iso(b)
        return (db - da).total_seconds() / 86400 if da and db and db >= da else None
    out = {}
    for name, a, b in (("first email → reply", "first_at", "reply_at"), ("handover → won", "handed_at", "won_at")):
        vals = [d for d in (days(j.get(a), j.get(b)) for j in js) if d is not None]
        out[name] = {"median_days": round(median(vals), 1) if vals else None, "n": len(vals)}
    return out


def score_of(j):
    return max([WEIGHT.get(s, 0) for s in j["stages"]] + [0])


def arms(js, rnd=None):
    """Share of volume per strategy (angle|subject|cta), per segment and overall, by Thompson sampling on outcomes."""
    rnd = rnd or random.Random(7)
    by = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for j in js:
        arm = j.get("arm")
        if not arm or "|" not in str(arm) or "emailed" not in j["stages"]:
            continue
        reward = min(1.0, score_of(j) / 3.0)        # a positive reply counts 1/3; a meeting or better counts fully
        for seg in (j["segment"], j["segment"].split(":")[0] + ":any", "all"):
            by[seg][arm][0] += 1
            by[seg][arm][1] += reward
    out, stats = {}, {}
    for seg, arms_ in by.items():
        if sum(v[0] for v in arms_.values()) < MIN_TRIALS:
            continue
        wins = Counter()
        for _ in range(2000):
            best = max(arms_, key=lambda a: rnd.betavariate(1 + arms_[a][1], 1 + arms_[a][0] - arms_[a][1]))
            wins[best] += 1
        out[seg] = {a: round(wins[a] / 2000, 3) for a in arms_ if wins[a]}
        stats[seg] = {a: {"trials": v[0], "reward": round(v[1], 1)} for a, v in arms_.items()}
    return out, stats


def examples(con, js, per=3):
    good = {j["email"]: j for j in js if score_of(j) >= WEIGHT["meeting"] or (j.get("temperature") == "hot")}
    out = defaultdict(list)
    for s in con.execute("SELECT lead_email, body FROM vis_sent WHERE kind='step' AND step=0 ORDER BY at DESC LIMIT 5000"):
        j = good.get(s["lead_email"])
        if j and s["body"]:
            for seg in (j["segment"], "all"):
                if len(out[seg]) < per:
                    out[seg].append(s["body"][:700])
    return dict(out)


# ---------------------------------------------------------------- lead scoring

def _features(j):
    try:
        x = json.loads(j.get("extra") or "{}")
    except ValueError:
        x = {}
    f = {"bias": 1.0, "role:" + j["segment"].split(":")[0]: 1.0}
    ind = j["segment"].split(":")[1]
    if ind != "any":
        f["ind:" + ind] = 1.0
    if x.get("fit"):
        f["fit:" + str(x["fit"])] = 1.0
    if x.get("country"):
        f["country:" + str(x["country"]).lower()[:20]] = 1.0
    if x.get("website") or x.get("source"):
        f["src:" + str(x.get("source") or "")[:20].lower()] = 1.0
    return f


def train_scoring(js, epochs=200, lr=0.1, l2=0.01):
    data = [(_features(j), 1.0 if "positive" in j["stages"] else 0.0) for j in js if "emailed" in j["stages"]]
    pos = sum(y for _, y in data)
    if len(data) < 200 or pos < 10:
        return None
    keys = Counter(k for f, _ in data for k in f)
    vocab = [k for k, n in keys.items() if n >= 5 or k == "bias"]
    w = {k: 0.0 for k in vocab}
    for _ in range(epochs):
        grad = {k: 0.0 for k in vocab}
        for f, y in data:
            z = sum(w.get(k, 0) * v for k, v in f.items())
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            for k, v in f.items():
                if k in grad:
                    grad[k] += (p - y) * v
        for k in vocab:
            w[k] -= lr * (grad[k] / len(data) + l2 * w[k])
    return {k: round(v, 4) for k, v in w.items() if abs(v) > 1e-4}


def lead_score(weights, extra_json, segment):
    if not weights:
        return None
    f = _features({"extra": extra_json, "segment": segment})
    z = sum(weights.get(k, 0) * v for k, v in f.items())
    return round(100 / (1 + math.exp(-max(-30, min(30, z)))), 1)


# ---------------------------------------------------------------- lessons and next steps

def rule_lessons(js, arm_stats):
    out = []
    allst = arm_stats.get("all") or {}
    ranked = sorted(((a, v) for a, v in allst.items() if v["trials"] >= MIN_TRIALS), key=lambda x: -x[1]["reward"] / x[1]["trials"])
    if len(ranked) >= 2:
        best, worst = ranked[0], ranked[-1]
        r = lambda v: v["reward"] / v["trials"] * 100
        out.append(f"Best performer so far: angle '{best[0].split('|')[0]}' with a {best[0].split('|')[1]} subject and a {best[0].split('|')[2]} close "
                   f"({r(best[1]):.1f} outcome points per 100 emails vs {r(worst[1]):.1f} for '{worst[0].split('|')[0]}').")
    hours = defaultdict(lambda: [0, 0])
    for j in js:
        if "emailed" in j["stages"] and j.get("hour") is not None:
            hours[j["hour"]][0] += 1
            hours[j["hour"]][1] += "replied" in j["stages"]
    good = [(h, v[1] / v[0]) for h, v in hours.items() if v[0] >= 40]
    if len(good) >= 3:
        h, rate = max(good, key=lambda x: x[1])
        out.append(f"Emails sent around {h:02d}:00 UTC got the most replies ({rate * 100:.1f}%).")
    seg = breakdown(js, "segment")
    seg = [s for s in seg if s["emailed"] >= 50]
    if seg:
        top = max(seg, key=lambda s: s["positive"] / max(1, s["emailed"]))
        low = min(seg, key=lambda s: s["positive"] / max(1, s["emailed"]))
        if top["key"] != low["key"]:
            out.append(f"'{top['key']}' leads respond best ({top['positive_rate']} positive); '{low['key']}' worst ({low['positive_rate']}). "
                       "Shift volume toward the first.")
    lost = Counter((j.get("lost_reason") or "").lower().strip() for j in js if j.get("lost_reason"))
    if lost:
        r, n = lost.most_common(1)[0]
        out.append(f"Most common reason deals are lost: '{r}' ({n}).")
    back = sum(1 for j in js if j.get("came_back"))
    if back:
        out.append(f"{back} lead(s) came back months after going quiet: keep check-backs on.")
    return out


def ai_lessons(cfg_like, js, arm_stats, sample_wins, sample_losses):
    """A short list of lessons written by the AI from the numbers and a few real examples. None if no key."""
    try:
        import bot
        if not bot.env("ANTHROPIC_API_KEY"):
            return None
        summary = {"funnel": funnel(js), "by_segment": breakdown(js, "segment")[:12], "arms": arm_stats.get("all") or {}}
        prompt = f"""You analyse a B2B outbound email programme for an IT services company.
Numbers: {json.dumps(summary)[:9000]}
First emails that led to meetings or hot replies: {json.dumps(sample_wins)[:4000]}
First emails that got no reply: {json.dumps(sample_losses)[:3000]}
Write 5 to 8 short, specific lessons (one line each) about what to do more of and less of: which angles, subject styles, kinds of
lead, wording and timing work. Base every lesson on the data above; say when the evidence is thin. Return a JSON list of strings."""
        raw = bot.llm(cfg_like, "write" if (cfg_like.get("models") or {}).get("write") else "personalize", prompt, max_tokens=600)
        m = re.search(r"\[.*\]", raw or "", re.S)
        return [str(x)[:240] for x in json.loads(m.group(0))][:8] if m else None
    except Exception:
        return None


NEXT_STEP = {
    "new": "Reply now: the playbook deadline is running. Answer their question, then propose two call times.",
    "contacted": "No answer yet? Nudge after 1 working day (hot) or 3 days (warm) with one relevant case study.",
    "meeting": "Before the meeting: send a short agenda and one case study from their industry.",
    "met": "Send the proposal within 2 working days while the meeting is fresh; recap their goals in their words.",
    "proposal": "Check in 3-4 days after sending; offer a short call to walk through it.",
    "negotiation": "Address the main objection directly; offer a pilot / proof of concept if price or risk is the issue.",
}


def next_steps(con, js):
    out = []
    by_stage = defaultdict(list)
    for j in js:
        if j.get("handed_at") and "won" not in j["stages"]:
            pass
    for h in con.execute("SELECT h.*, u.name sup FROM handovers h LEFT JOIN users u ON u.id=h.supervisor_id "
                         "WHERE COALESCE(h.status,'new') NOT IN ('won','lost') ORDER BY h.at DESC LIMIT 200"):
        st = h["status"] or "new"
        since = _iso(h["status_at"] or h["at"])
        days = (datetime.now(timezone.utc) - since).days if since else 0
        by_stage[st].append(days)
        out.append({"email": h["lead_email"], "name": h["lead_name"], "company": h["company"], "sup": h["sup"], "stage": st, "days": days,
                    "step": NEXT_STEP.get(st, "Keep the conversation moving.")})
    return out


# ---------------------------------------------------------------- the strategy profile

def build(con, cfg_like=None, prev=None):
    js = journeys(con)
    shares, stats = arms(js)
    weights = train_scoring(js)
    lessons = None
    if cfg_like is not None:
        wins = [j["email"] for j in js if score_of(j) >= WEIGHT["meeting"]][:5]
        sample_w = [r["body"][:500] for r in con.execute(f"SELECT body FROM vis_sent WHERE step=0 AND lead_email IN ({','.join('?' * len(wins))})", wins)] if wins else []
        cold = [j["email"] for j in js if j["stages"] == {"found", "emailed"}][:5]
        sample_l = [r["body"][:500] for r in con.execute(f"SELECT body FROM vis_sent WHERE step=0 AND lead_email IN ({','.join('?' * len(cold))})", cold)] if cold else []
        lessons = ai_lessons(cfg_like, js, stats, sample_w, sample_l)
    lessons = lessons or rule_lessons(js, stats)
    return {"version": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"), "arms": shares, "arm_stats": stats, "explore": EXPLORE,
            "examples": examples(con, js), "lessons": lessons, "scoring": weights,
            "data": {"leads": len(js), "emailed": sum(1 for j in js if "emailed" in j["stages"]),
                     "positive": sum(1 for j in js if "positive" in j["stages"]), "meetings": sum(1 for j in js if "meeting" in j["stages"]),
                     "won": sum(1 for j in js if "won" in j["stages"])}}


def diff(old, new):
    """Readable changes between two profiles, and whether they're all small (for auto-approval)."""
    lines, small = [], True
    old_arms, new_arms = (old or {}).get("arms") or {}, (new or {}).get("arms") or {}
    for seg in sorted(set(old_arms) | set(new_arms)):
        o, n = old_arms.get(seg, {}), new_arms.get(seg, {})
        for arm in sorted(set(o) | set(n)):
            a, b = o.get(arm, 0), n.get(arm, 0)
            if abs(a - b) >= 0.05:
                lines.append(f"{seg}: {arm.replace('|', ' / ')} {a * 100:.0f}% → {b * 100:.0f}% of emails")
                small = small and abs(a - b) < 0.10
    if (old or {}).get("lessons") != (new or {}).get("lessons") and (new or {}).get("lessons"):
        lines.append("New lessons for the writer (below).")
    if bool((old or {}).get("scoring")) != bool((new or {}).get("scoring")):
        lines.append("Lead scoring " + ("switched on: enough results to rank leads" if (new or {}).get("scoring") else "off"))
        small = False
    return lines, small


def svg_funnel(rows, width=640):
    """A log-scale funnel: every stage stays readable even when 10,000 leads become 5 deals."""
    top = max([r["n"] for r in rows] + [1])
    h, bar = 30, 22
    out = [f'<svg viewBox="0 0 {width} {len(rows) * h + 10}" width="100%" role="img" aria-label="Funnel, log scale" style="max-width:{width}px">']
    for i, r in enumerate(rows):
        frac = math.log10(r["n"] + 1) / math.log10(top + 1) if top > 0 else 0
        w = max(2, (width - 290) * frac)
        y = 5 + i * h
        out.append(f'<text x="0" y="{y + 16}" font-size="12" fill="currentColor">{r["label"]}</text>'
                   f'<rect x="170" y="{y}" width="{w:.0f}" height="{bar}" rx="3" fill="#0176d3" opacity="{0.35 + 0.65 * (1 - i / len(rows)):.2f}"></rect>'
                   f'<text x="{176 + w:.0f}" y="{y + 16}" font-size="12" fill="currentColor">{r["n"]:,}{(" · " + r["rate"]) if r["rate"] else ""}</text>')
    out.append("</svg>")
    return "".join(out)
