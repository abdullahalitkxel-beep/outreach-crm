"""Fresh leads from Instantly SuperSearch, pulled by the bot itself.

Settings → Lead targeting → Instantly. The bot starts a SuperSearch import (Instantly finds the people and
their work emails, using your Instantly credits), then on later runs collects the finished list into BSO.
With "pull automatically" on, it starts a new import whenever fewer than N days of new leads are left.
"""
import json
import os
from datetime import timedelta

import requests

from bot import env, import_rows, iso, log, now_utc

API = os.getenv("INSTANTLY_API_BASE", "https://api.instantly.ai/api/v2")
KEY = "INSTANTLY_API_KEY"
LEVELS = ["C-Level", "VP-Level", "Director-Level", "Manager-Level", "Owner", "Partner"]
DEPARTMENTS = ["Engineering", "IT & IS", "Operations", "Finance & Administration", "Marketing", "Sales", "Human Resources", "Support", "Other"]
SIZES = ["0 - 25", "25 - 100", "100 - 250", "250 - 1000", "1K - 10K", "10K - 50K", "50K - 100K", "> 100K"]
INDUSTRIES = ["Software & Internet", "Computers & Electronics", "Business Services", "Financial Services", "Healthcare, Pharmaceuticals, & Biotech",
              "Telecommunications", "Media & Entertainment", "Retail", "Manufacturing", "Education", "Energy & Utilities", "Real Estate & Construction",
              "Transportation & Storage", "Wholesale & Distribution", "Consumer Services", "Travel, Recreation, and Leisure", "Agriculture & Mining",
              "Government", "Non-Profit", "Other"]


class InstantlyError(Exception):
    pass


def _call(method, path, body=None, timeout=60):
    key = env(KEY)
    if not key:
        raise InstantlyError("Add your Instantly API key first (Settings → Lead targeting → Instantly).")
    try:
        r = requests.request(method, API + path, json=body, timeout=timeout,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise InstantlyError(f"Can't reach Instantly ({e}).")
    if r.status_code == 401:
        raise InstantlyError("Instantly didn't accept the API key. Create one in Instantly → Settings → Integrations → API keys (API v2).")
    if r.status_code == 402:
        raise InstantlyError("Instantly says your plan or credits don't allow this (402). Check your SuperSearch credits.")
    if r.status_code == 429:
        raise InstantlyError("Instantly is rate-limiting requests; BSO will try again on a later run.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise InstantlyError(f"Instantly returned an error ({r.status_code}): {msg}")
    try:
        return r.json()
    except ValueError:
        return {}


def settings(cfg):
    return cfg.get("instantly") or {}


def search_filters(cfg):
    ic = settings(cfg)
    f = {}
    titles = [t.strip() for t in ic.get("titles") or [] if t.strip()]
    if titles:
        f["title"] = {"include": titles, "includeMode": "CONTAINS"}
    if ic.get("levels"):
        f["level"] = list(ic["levels"])
    if ic.get("departments"):
        f["department"] = list(ic["departments"])
    if ic.get("sizes"):
        f["employeeCount"] = list(ic["sizes"])
    countries = [c.strip() for c in ic.get("countries") or [] if c.strip()]
    if countries:
        f["locations"] = {"include": [{"country": c} for c in countries]}
    if ic.get("industries"):
        f["industry"] = {"include": list(ic["industries"])}
    if (ic.get("keywords") or "").strip():
        f["keyword_filter"] = {"include": ic["keywords"].strip(), "include_mode": "ANY"}
    return f


def count(cfg):
    """How many SuperSearch leads match (doesn't import anything)."""
    f = search_filters(cfg)
    if not f:
        raise InstantlyError("Set at least one filter (job titles, countries, …) first.")
    j = _call("POST", "/supersearch-enrichment/count-leads-from-supersearch",
              {"search_filters": f, "skip_owned_leads": True, "show_one_lead_per_company": bool(settings(cfg).get("one_per_company", True))})
    return int(j.get("number_of_leads") or 0)


def start_pull(cfg, con, limit=None):
    """Ask Instantly to find and enrich new leads into a fresh list. Returns the list id."""
    ic = settings(cfg)
    f = search_filters(cfg)
    if not f:
        raise InstantlyError("Set at least one filter (job titles, countries, …) first.")
    f["skip_owned_leads"] = True
    f["show_one_lead_per_company"] = bool(ic.get("one_per_company", True))
    limit = int(limit or ic.get("per_pull") or 50)
    name = f"BSO {now_utc().strftime('%Y-%m-%d %H:%M')}"
    j = _call("POST", "/supersearch-enrichment/enrich-leads-from-supersearch",
              {"search_filters": f, "limit": limit, "list_name": name, "skip_rows_without_email": True, "work_email_enrichment": True})
    rid = j.get("resource_id") or j.get("id")
    if not rid:
        raise InstantlyError("Instantly didn't return a list for the import.")
    con.execute("INSERT INTO instantly_pulls(resource_id, started_at, wanted, status) VALUES (?,?,?,'running')", (rid, iso(now_utc()), limit))
    con.commit()
    log.info("Instantly: started importing up to %d leads into list %s", limit, rid)
    return rid


def _row(lead):
    p = lead.get("payload") or {}
    row = {"email": lead.get("email") or "", "first_name": lead.get("first_name") or p.get("firstName") or "",
           "last_name": lead.get("last_name") or p.get("lastName") or "", "company": lead.get("company_name") or p.get("companyName") or "",
           "website": lead.get("website") or p.get("website") or "", "title": lead.get("job_title") or p.get("jobTitle") or "",
           "source": "Instantly SuperSearch"}
    for k, v in p.items():
        if "linkedin" in k.lower() and v:
            row["linkedin"] = v
    return row


def collect(cfg, con):
    """Bring finished Instantly imports into BSO. Returns how many leads were added."""
    added_total = 0
    for p in con.execute("SELECT * FROM instantly_pulls WHERE status='running' ORDER BY id").fetchall():
        st = _call("GET", f"/supersearch-enrichment/{p['resource_id']}")
        if st.get("in_progress"):
            continue
        rows, after = [], None
        for _ in range(50):                                   # up to 5,000 leads per import
            body = {"list_id": p["resource_id"], "limit": 100}
            if after:
                body["starting_after"] = after
            page = _call("POST", "/leads/list", body)
            items = page.get("items") or []
            rows += [_row(x) for x in items]
            after = page.get("next_starting_after")
            if not after or not items:
                break
        added, skipped = import_rows(con, rows, "active", source="Instantly SuperSearch")
        con.execute("UPDATE instantly_pulls SET status='done', finished_at=?, found=?, imported=?, skipped=? WHERE id=?",
                    (iso(now_utc()), len(rows), added, json.dumps(skipped), p["id"]))
        con.commit()
        added_total += added
        log.info("Instantly: list %s finished, %d leads found, %d added", p["resource_id"], len(rows), added)
    return added_total


def days_of_leads(cfg, con):
    cap = sum(int(sv.get("daily_limit") or 0) for sv in cfg.get("senders") or [] if sv.get("email") and "example" not in sv.get("email", ""))
    fresh = con.execute("SELECT COUNT(*) FROM leads WHERE status='active' AND step=0").fetchone()[0]
    return (fresh / cap) if cap else None


def maybe_pull(cfg, con):
    """Called on each bot run: collect finished imports; start a new one when leads are running low. Never raises."""
    ic = settings(cfg)
    if not env(KEY):
        return ""
    msgs = []
    try:
        n = collect(cfg, con)
        if n:
            msgs.append(f"Instantly: {n} new lead(s) added.")
        low = float(ic.get("auto_below_days") or 0)
        if ic.get("auto") and low > 0:
            left = days_of_leads(cfg, con)
            running = con.execute("SELECT 1 FROM instantly_pulls WHERE status='running'").fetchone()
            recent = con.execute("SELECT 1 FROM instantly_pulls WHERE started_at>?", (iso(now_utc() - timedelta(hours=12)),)).fetchone()
            if left is not None and left < low and not running and not recent:
                start_pull(cfg, con)
                msgs.append(f"Instantly: fewer than {low:g} days of leads left, started a new import.")
    except InstantlyError as e:
        msgs.append(f"PROBLEM: {e}")
    except Exception as e:
        msgs.append(f"PROBLEM: Instantly import failed ({e}).")
    return " ".join(msgs)
