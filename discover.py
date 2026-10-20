"""Find more decision-makers at companies already in BSO, using only work emails that are PUBLISHED on the web.

For each company (once), the AI (your Anthropic key, with its web search tool) looks for people in your
target roles whose work email is written on a public page, and says where. BSO then checks every address
itself before using it:
  1. it must be at the company's own domain and not a shared inbox (info@, sales@…);
  2. BSO opens the page the AI named and confirms the address is really written there (no guessing);
  3. if your Instantly key is set, Instantly's verifier must say "verified" (only these few addresses are checked).
Settings → Lead targeting → "Find more contacts on the web". Costs: about $0.03–0.05 per company searched.
"""
import json
import re
import time
from datetime import timedelta

import requests

from bot import EMAIL_RE, ROLE_INBOXES, _reply_text, add_lead, env, iso, log, now_utc

FREEMAIL = {"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "aol.com", "icloud.com", "me.com",
            "proton.me", "protonmail.com", "gmx.com", "mail.com", "yandex.com", "zoho.com"}
DEFAULT_ROLES = ["CTO", "VP Engineering", "Head of Engineering", "Director of Engineering", "Head of Product", "CIO"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BSO/1.0"}


def settings(cfg):
    return cfg.get("discover") or {}


def domain_of(lead):
    w = (lead.get("website") or "").lower().strip()
    w = re.sub(r"^https?://", "", w).split("/")[0].removeprefix("www.")
    if w and "." in w:
        return w
    d = (lead.get("email") or "").split("@")[-1].lower()
    return d if d and d not in FREEMAIL else ""


def _prompt(company, domain, roles):
    return f"""Find people who work at {company} (website {domain}) in these roles: {', '.join(roles)}.

Only include a person if their WORK email address (ending in @{domain} or a subdomain of it) is written
publicly on a web page you found: the company's own site, a press release, a conference or speaker page,
a public directory, a GitHub profile or similar. Never construct or guess an address from a name pattern.
At most 3 people. For each, give the exact URL of the page where the email address is written.

Answer with only a JSON array, no other text:
[{{"name": "...", "title": "...", "email": "...", "source_url": "https://..."}}]
If you find nobody whose email is published, answer: []"""


def ask_ai(cfg, company, domain, roles):
    """[{name, title, email, source_url}] from the AI's web search. Raises on API problems."""
    import anthropic
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": int(settings(cfg).get("max_searches") or 3)}]
    models = [(cfg.get("models") or {}).get("discover") or "claude-haiku-5-5", "claude-sonnet-5-5"]
    messages = [{"role": "user", "content": _prompt(company, domain, roles)}]
    last_err = None
    for model in dict.fromkeys(models):
        try:
            msg = client.messages.create(model=model, max_tokens=2000, tools=tools, messages=messages)
            for _ in range(2):                       # long searches may pause; let them finish
                if getattr(msg, "stop_reason", "") != "pause_turn":
                    break
                msg = client.messages.create(model=model, max_tokens=2000, tools=tools,
                                             messages=messages + [{"role": "assistant", "content": msg.content}])
            return parse(_reply_text(msg))
        except anthropic.BadRequestError as e:       # this model can't use web search: try the next
            last_err = e
            continue
    raise last_err or RuntimeError("no model available for web search")


def parse(text):
    for m in reversed(list(re.finditer(r"\[[\s\S]*?\]", text or ""))):
        try:
            data = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


def page_shows(url, email):
    """True if the page really contains the address (plain or as a mailto: link)."""
    if not re.match(r"^https?://", url or ""):
        return False
    try:
        r = requests.get(url, headers=UA, timeout=20)
    except requests.RequestException:
        return False
    if r.status_code >= 400:
        return False
    text = r.text.lower()
    e = email.lower()
    return e in text or e.replace("@", "&#64;") in text or e.replace("@", "%40") in text


def instantly_verdict(email):
    """'verified' / 'invalid' / None (no Instantly key or no answer)."""
    if not env("INSTANTLY_API_KEY"):
        return None
    try:
        import instantly
        j = instantly._call("POST", "/email-verification", {"email": email})
        for _ in range(6):
            st = j.get("verification_status")
            if st in ("verified", "invalid"):
                return st
            time.sleep(5)
            j = instantly._call("GET", f"/email-verification/{email}")
    except Exception as e:
        log.warning("Instantly verification of %s failed: %s", email, e)
    return None


def check_person(p, domain, known):
    """Why this found contact can't be used, or '' if it passes every check."""
    email = str(p.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return "no valid email"
    dom = email.split("@")[1]
    if not (dom == domain or dom.endswith("." + domain)):
        return "not at the company's domain"
    if email.split("@")[0] in ROLE_INBOXES:
        return "shared inbox"
    if email in known:
        return "already in BSO"
    if not page_shows(p.get("source_url"), email):
        return "address not found on the page given"
    v = instantly_verdict(email)
    if v == "invalid":
        return "Instantly says the address is invalid"
    return ""


def maybe_discover(cfg, con):
    """Called on each bot run: research a few companies not searched before. Never raises."""
    ds = settings(cfg)
    if not ds.get("enabled") or not env("ANTHROPIC_API_KEY"):
        return ""
    per_day = int(ds.get("per_day") or 10)
    today = iso(now_utc().replace(hour=0, minute=0, second=0, microsecond=0))
    budget = per_day - con.execute("SELECT COUNT(*) FROM discovered WHERE at>=?", (today,)).fetchone()[0]
    if budget <= 0:
        return ""
    roles = [r.strip() for r in ds.get("roles") or DEFAULT_ROLES if r.strip()]
    done = {r[0] for r in con.execute("SELECT domain FROM discovered")}
    targets = {}
    for r in con.execute("SELECT * FROM leads WHERE status NOT IN ('bounced','unsubscribed') ORDER BY id DESC LIMIT 2000"):
        d = domain_of(dict(r))
        if d and d not in done and d not in targets:
            targets[d] = dict(r)
        if len(targets) >= budget:
            break
    added_total, checked = 0, 0
    for domain, lead in targets.items():
        known = {r[0] for r in con.execute("SELECT email FROM leads")}
        try:
            people = ask_ai(cfg, lead.get("company") or domain, domain, roles)
        except Exception as e:
            log.warning("Contact search for %s failed: %s", domain, e)
            return f"PROBLEM: web search for new contacts failed ({e})."
        added, notes = 0, []
        for p in people[:5]:
            why = check_person(p, domain, known)
            if why:
                notes.append(f"{p.get('email')}: {why}")
                continue
            name = str(p.get("name") or "").split()
            row = {"email": p["email"].strip().lower(), "first_name": name[0] if name else "", "last_name": " ".join(name[1:]),
                   "company": lead.get("company") or "", "website": lead.get("website") or domain, "title": p.get("title") or "",
                   "source": f"published at {p.get('source_url')}", "found_via": lead["email"], "campaign_id": lead.get("campaign_id") or 1}
            if add_lead(con, row, "active" if ds.get("add_to_sequence", True) else "paused"):
                added += 1
                known.add(row["email"])
        con.execute("INSERT OR REPLACE INTO discovered(domain, at, found, added, note) VALUES (?,?,?,?,?)",
                    (domain, iso(now_utc()), len(people), added, "; ".join(notes)[:1000]))
        con.commit()
        added_total += added
        checked += 1
    return f"Web search: {checked} compan{'y' if checked == 1 else 'ies'} searched, {added_total} new contact(s) with published emails added." if checked else ""
