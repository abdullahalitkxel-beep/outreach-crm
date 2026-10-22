"""Emails written fresh for every lead from the campaign brief, instead of the same template words over and over.

The brief holds the ingredients (offer, ideal customer, proof points, angles, what each step is for, rules). For each
lead the AI combines the brief with what we know about the lead and their company, using the angle / subject style /
call to action the strategy profile (learned by the Master Console from what actually led to meetings and deals) picks
for this kind of lead. Every email is checked before it's used:
  - facts: numbers and client claims must come from the brief's proof points (no invented facts)
  - wording: not too similar to the last few hundred emails sent (rewritten if it is)
  - rules: length, avoided phrases, one call to action
If the AI isn't available, over budget, or keeps failing the checks, BSO falls back to the campaign's templates.
"""
import json
import random
import re
from datetime import date

import bot

ANGLES = ["cost", "speed", "quality", "timezone", "skills", "scaling"]
ANGLE_TEXT = {
    "cost": "lower total cost than hiring in-house, without lowering quality",
    "speed": "engineers ready to start within weeks instead of months of hiring",
    "quality": "proven delivery quality and process maturity",
    "timezone": "working-hours overlap and easy collaboration with their team",
    "skills": "specific skills that are hard to hire (match to their stack if known)",
    "scaling": "scaling the team up or down as the roadmap changes",
}
SUBJECT_STYLES = {"question": "a short question about their team or goal", "company": "mentions their company name naturally",
                  "outcome": "names the outcome they'd get, plainly", "plain": "two to four plain words, lowercase is fine"}
CTAS = {"call": "ask for a 15-minute call this week or next", "question": "end with one easy question about their situation",
        "permission": "ask if it's worth sending a short example / case study", "resource": "offer a relevant case study to look at"}
STEP_PURPOSE = ["first contact: why them, why us, one ask", "add proof: one concrete example or result",
                "a different angle from the first email", "polite close: ask if it's a 'no' or 'not now'"]


def default_brief(cfg):
    facts = cfg.get("facts") or {}
    return {
        "offer": " ".join(str(cfg.get("offer") or "").split()),
        "ideal_customer": "mid-size and enterprise companies and software product companies that need to scale engineering",
        "proof_points": [f"{k.replace('_', ' ')}: {v}" for k, v in facts.items()],
        "angles": list(ANGLES),
        "step_purposes": STEP_PURPOSE,
        "tone": "plain, direct and friendly; like a person writing, not marketing",
        "max_words": 110,
        "rules": "one call to action; no exaggeration; no 'I hope this finds you well'; no buzzwords",
    }


def brief(cfg, con, campaign_id):
    b = default_brief(cfg)
    try:
        r = con.execute("SELECT brief FROM campaigns WHERE id=?", (campaign_id or 1,)).fetchone()
        if r and r["brief"]:
            b.update({k: v for k, v in json.loads(r["brief"]).items() if v not in (None, "", [])})
    except Exception:
        pass
    return b


def enabled(cfg, con, campaign_id):
    w = cfg.get("writing") or {}
    if w.get("mode", "ai") != "ai" or not bot.env("ANTHROPIC_API_KEY"):
        return False
    try:
        r = con.execute("SELECT writing FROM campaigns WHERE id=?", (campaign_id or 1,)).fetchone()
        if r and r["writing"] == "templates":
            return False
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- strategy (what to try for this kind of lead)

def segment(lead):
    x = json.loads(lead.get("extra") or "{}") if isinstance(lead, dict) else {}
    title = (x.get("title") or "").lower()
    if re.search(r"\b(ceo|founder|owner|president|chief executive)\b", title):
        role = "founder"
    elif re.search(r"\b(cto|cio|vp eng|vice president.*eng|head of eng|engineering director|director of engineering|chief tech)", title):
        role = "tech_exec"
    elif re.search(r"\b(product|cpo)\b", title):
        role = "product"
    elif re.search(r"\b(engineer|engineering|developer|architect|devops|it manager|tech lead|technical lead|software)\b", title):
        role = "tech"
    else:
        role = "other"
    ind = (x.get("industry") or "").lower()
    ind = re.sub(r"[^a-z]+", "_", ind).strip("_")[:24] or "any"
    return f"{role}:{ind}"


def pick_strategy(state_profile, lead, b, rnd=random):
    """(angle, subject style, cta). Mostly what's working for this segment, ~20% trying something else."""
    seg = segment(lead)
    prof = (state_profile or {}).get("arms") or {}
    shares = prof.get(seg) or prof.get(seg.split(":")[0] + ":any") or prof.get("all") or {}
    angles = [a for a in (b.get("angles") or ANGLES) if a in ANGLE_TEXT] or ANGLES
    if shares and rnd.random() > float((state_profile or {}).get("explore", 0.2)):
        arms = [(k, v) for k, v in shares.items() if k.split("|")[0] in angles]
        if arms:
            total = sum(v for _, v in arms)
            pick = rnd.random() * total
            for k, v in arms:
                pick -= v
                if pick <= 0:
                    parts = (k.split("|") + ["question", "call"])[:3]
                    return parts[0], parts[1] if parts[1] in SUBJECT_STYLES else "question", parts[2] if parts[2] in CTAS else "call"
    return rnd.choice(angles), rnd.choice(list(SUBJECT_STYLES)), rnd.choice(list(CTAS))


def arm_name(angle, style, cta):
    return f"{angle}|{style}|{cta}"


# ---------------------------------------------------------------- checks

def _shingles(text, n=5):
    w = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def too_similar(con, body, limit=300, threshold=0.45):
    """True when this body repeats too much of a recent email (by 5-word shingles)."""
    mine = _shingles(body)
    if len(mine) < 8:
        return False
    try:
        rows = con.execute("SELECT body FROM sent_log WHERE kind='step' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        return False
    for r in rows:
        other = _shingles(r["body"])
        if other and len(mine & other) / max(1, min(len(mine), len(other))) > threshold:
            return True
    return False


NUM_RE = re.compile(r"\b\d[\d,.]*\s*(%|\+|x|k|m)?")


def unsupported_facts(text, b):
    """Numbers in the email that don't appear in the proof points or the offer (the AI must not invent figures)."""
    known = " ".join(b.get("proof_points") or []) + " " + (b.get("offer") or "")
    known_nums = {re.sub(r"[^\d]", "", m.group(0)) for m in NUM_RE.finditer(known)}
    bad = []
    for m in NUM_RE.finditer(text):
        digits = re.sub(r"[^\d]", "", m.group(0))
        if not digits or len(digits) <= 1 and not m.group(1):
            continue                                # "1 call", "2 weeks" style small numbers are fine
        if digits not in known_nums and not re.fullmatch(r"(15|30|20|10|45|60)", digits):   # meeting lengths
            bad.append(m.group(0).strip())
    return bad


# ---------------------------------------------------------------- writing

def _lead_facts(lead):
    x = json.loads(lead.get("extra") or "{}")
    keep = {k: x[k] for k in ("title", "industry", "city", "country", "employees", "technologies", "keywords") if x.get(k)}
    keep["company"] = bot.short_company(lead.get("company")) or ""
    return keep


def _json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def write_step(cfg, con, lead, sender, idx, profile=None, steps=4):
    """Returns (subject, body, arm) or None to fall back to the template."""
    lead = dict(lead)
    b = brief(cfg, con, lead.get("campaign_id"))
    angle, style, cta = pick_strategy(profile, lead, b)
    purposes = b.get("step_purposes") or STEP_PURPOSE
    purpose = purposes[min(idx, len(purposes) - 1)] if idx < steps - 1 or len(purposes) <= idx else purposes[-1]
    site = ""
    if idx == 0:
        try:
            site = bot.fetch_site_text(lead.get("website") or "", 2500)
        except Exception:
            site = ""
    prior = []
    try:
        prior = [dict(r) for r in con.execute("SELECT subject, body FROM sent_log WHERE lead_id=? AND kind='step' ORDER BY id", (lead["id"],))]
    except Exception:
        pass
    examples = [e for e in (profile or {}).get("examples", {}).get(segment(lead), []) + (profile or {}).get("examples", {}).get("all", [])][:3]
    lessons = (profile or {}).get("lessons") or []
    prompt = f"""You write one B2B cold email for a sales team. Write it fresh: never reuse stock phrases.

OUR OFFER: {b['offer']}
IDEAL CUSTOMER: {b.get('ideal_customer', '')}
PROOF POINTS (the ONLY facts and numbers you may state): {json.dumps(b.get('proof_points') or [])}
THIS EMAIL: step {idx + 1} of {steps} in the sequence. Purpose: {purpose}.
ANGLE TO LEAD WITH: {angle}: {ANGLE_TEXT.get(angle, angle)}
SUBJECT STYLE: {SUBJECT_STYLES[style]}
CALL TO ACTION: {CTAS[cta]} (exactly one)
TONE: {b.get('tone')}. RULES: {b.get('rules')}. At most {int(b.get('max_words') or 110)} words in the body. Address them by first name.
Sign off with: {sender['name']}

ABOUT THE LEAD'S COMPANY (business facts only): {json.dumps(_lead_facts(lead))}
THEIR WEBSITE (may be empty): {site[:2000]}
{('EMAILS WE ALREADY SENT THEM (do not repeat their points): ' + json.dumps([p['body'][:600] for p in prior])) if prior else ''}
{('WHAT HAS WORKED FOR SIMILAR LEADS (style only; do not copy): ' + json.dumps([e[:700] for e in examples])) if examples else ''}
{('LESSONS FROM OUR RESULTS: ' + '; '.join(lessons[:8])) if lessons else ''}

Mention something specific about their company only if it's in the facts or website above; otherwise don't pretend.
No placeholders, no links, no signature block beyond the name.
Return only JSON: {{"subject": "...", "body": "..."}}"""
    avoid = None
    for attempt in range(3):
        raw = bot.llm(cfg, "write" if (cfg.get("models") or {}).get("write") else "personalize", prompt + (avoid or ""), max_tokens=700)
        j = _json(raw)
        if not j or not j.get("body"):
            return None
        subject = " ".join(str(j.get("subject") or "").split())[:120]
        body = str(j["body"]).strip()
        problems = []
        if idx > 0 and lead.get("first_subject"):
            subject = "Re: " + lead["first_subject"]
        if len(body.split()) > int(b.get("max_words") or 110) * 1.3:
            problems.append("too long")
        bad = unsupported_facts(body + " " + subject, b)
        if bad:
            problems.append("states figures not in the proof points: " + ", ".join(bad[:4]))
        if bot.find_avoid(cfg, subject, body):
            problems.append("uses avoided phrases: " + ", ".join(bot.find_avoid(cfg, subject, body)[:4]))
        if "{" in body or "[" in body:
            problems.append("contains placeholders")
        if not problems and too_similar(con, body):
            problems.append("too similar to recent emails: use different wording and structure")
        if not problems:
            return subject or "Quick question", body, arm_name(angle, style, cta)
        avoid = "\n\nYOUR PREVIOUS DRAFT WAS REJECTED: " + "; ".join(problems) + ". Write a new one that fixes this."
    return None


def reconnect(cfg, con, lead, sender, why):
    """A soft check-back email that refers to what they actually said."""
    if not bot.env("ANTHROPIC_API_KEY"):
        return None
    lead = dict(lead)
    said = ""
    try:
        r = con.execute("SELECT body FROM received WHERE lead_id=? ORDER BY id DESC LIMIT 1", (lead["id"],)).fetchone()
        said = (r["body"] if r else "")[:1200]
    except Exception:
        pass
    b = brief(cfg, con, lead.get("campaign_id"))
    prompt = f"""Write a short, warm check-back email (max 80 words) to {lead.get('first_name') or 'them'} at {bot.short_company(lead.get('company')) or 'their company'}.
Why now: {why}. What they told us last time: \"\"\"{said}\"\"\"
Our offer, for context: {b['offer']}. Only state facts from: {json.dumps(b.get('proof_points') or [])}.
Refer to what they said, in your own words. One easy question at the end. No pressure, no buzzwords. Sign off: {sender['name']}.
Return only the email body."""
    text = bot.llm(cfg, "write" if (cfg.get("models") or {}).get("write") else "personalize", prompt, max_tokens=400)
    if not text or unsupported_facts(text, b) or bot.find_avoid(cfg, text):
        return None
    return text.strip()


def suggest_reply(cfg, lead, reply_text, temp, sender_name="", playbook_note=""):
    """A suggested answer for the supervisor, in the style the playbook asks for (hot: book the call; warm: answer + offer the call)."""
    if not bot.env("ANTHROPIC_API_KEY"):
        return ""
    style = {"hot": "Their reply is HOT: propose two specific time slots for a call in the next two working days and confirm what they asked for. "
                    "If they asked about price, give a range only if it's in the facts, else say you'll cover it on the call.",
             "warm": "Their reply is WARM: answer their question directly first, then offer a 15-minute call. Suggest one relevant example if helpful.",
             "cold": "Their reply is COLD: thank them briefly, keep the door open, ask permission to check back later."}.get(temp, "")
    prompt = f"""Draft a reply from our lead gen supervisor to this prospect. {style} {playbook_note}
Max 90 words. Plain and human. Don't invent facts or prices. Facts you may use: {json.dumps((cfg.get('facts') or {}))}
Prospect's reply: \"\"\"{(reply_text or '')[:1500]}\"\"\"
Return only the email body, starting with their first name ({lead.get('first_name') or 'there'})."""
    return (bot.llm(cfg, "write" if (cfg.get("models") or {}).get("write") else "personalize", prompt, max_tokens=400) or "").strip()


# ---------------------------------------------------------------- AI spending

def month():
    return date.today().strftime("%Y-%m")
