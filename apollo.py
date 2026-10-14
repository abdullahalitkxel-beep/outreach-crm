"""
Apollo.io lead sourcing.

- Search (mixed_people/api_search) is free: it finds people but returns no emails.
- Enrichment (people/bulk_match) reveals the work email and costs ~1 credit per person.

So the bot searches first, skips anyone it has already seen, enriches only new people
in batches of 10, and keeps only leads whose email Apollo marks as verified.
"""
import logging
import os
import time

import requests

from bot import env

API = "https://api.apollo.io/api/v1"
log = logging.getLogger("outreach")


class ApolloError(Exception):
    pass


class Apollo:
    def __init__(self, api_key):
        if not api_key:
            raise ApolloError("APOLLO_API_KEY is not set in .env")
        self.s = requests.Session()
        self.s.headers.update({
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        })

    def _post(self, path, body=None, params=None):
        for attempt in range(5):
            r = self.s.post(API + path, json=body, params=params, timeout=30)
            if r.status_code == 429:  # rate limited: back off and retry
                wait = int(r.headers.get("Retry-After") or 5 * 2 ** attempt)
                log.warning("Apollo rate limit hit, waiting %ss", wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise ApolloError(f"Apollo {path} returned {r.status_code}: {r.text[:300]}")
            return r.json()
        raise ApolloError("Apollo kept rate-limiting; try again later")

    def search(self, filters, page=1, per_page=100):
        return self._post("/mixed_people/api_search", {**filters, "page": page, "per_page": per_page})

    def enrich(self, person_ids):
        assert len(person_ids) <= 10, "Apollo bulk enrichment takes at most 10 people"
        return self._post("/people/bulk_match",
                          {"details": [{"id": i} for i in person_ids]},
                          params={"reveal_personal_emails": "false"})


# ---------------------------------------------------------------- helpers

def _people(data):
    return data.get("people") or data.get("contacts") or []


def _total(data):
    return data.get("total_entries") or (data.get("pagination") or {}).get("total_entries") or 0


def _filters(cfg, search):
    merged = {**(cfg["apollo"].get("common_filters") or {}), **(search.get("filters") or {})}
    # accept both "person_titles" and "person_titles[]" spellings in config
    return {k.rstrip("[]"): v for k, v in merged.items() if v not in (None, [], "")}


def _lead_row(person, search_name):
    org = person.get("organization") or {}
    return {
        "email": person.get("email") or "",
        "first_name": person.get("first_name") or "",
        "company": org.get("name") or "",
        "website": org.get("primary_domain") or org.get("website_url") or "",
        "last_name": person.get("last_name") or "",
        "title": person.get("title") or "",
        "industry": org.get("industry") or "",
        "city": person.get("city") or "",
        "country": person.get("country") or "",
        "linkedin": person.get("linkedin_url") or "",
        "source": f"apollo: {search_name}",
        "apollo_id": person.get("id") or "",
    }


def active_searches(cfg):
    off = set(cfg["apollo"].get("disabled_searches") or [])
    return [s for s in cfg["apollo"]["searches"] if s["name"] not in off]


# ---------------------------------------------------------------- commands

def preview(cfg):
    """Free: show how many people each search matches, with a few examples. Uses no credits."""
    api = Apollo(env("APOLLO_API_KEY"))
    for search in active_searches(cfg):
        data = api.search(_filters(cfg, search), page=1, per_page=5)
        print(f"\n▶ {search['name']}: {_total(data):,} people match")
        for p in _people(data):
            org = (p.get("organization") or {}).get("name") or ""
            last = p.get("last_name") or p.get("last_name_obfuscated") or ""
            print(f"   - {p.get('first_name', '')} {last} · {p.get('title', '')} · {org}")
        time.sleep(1)
    print("\nAdjust the filters in config.yaml until the samples look right, then run apollo-pull.")


def pull(cfg, con, max_new, add_lead):
    """Find up to max_new new leads with verified emails and add them to the database."""
    from bot import iso, now_utc  # local import avoids a circular import at load time

    ac = cfg["apollo"]
    api = Apollo(env("APOLLO_API_KEY"))
    allowed = set(ac.get("allowed_email_statuses", ["verified"]))
    searches = active_searches(cfg)
    if not searches:
        raise ApolloError("all searches are switched off in Settings → Targeting")
    per_search = max(1, -(-max_new // len(searches)))  # ceil division
    added = credits = checked = 0

    for search in searches:
        name = search["name"]
        got = 0
        row = con.execute("SELECT page FROM apollo_progress WHERE search=?", (name,)).fetchone()
        page = row["page"] if row else 1
        exhausted_once = False

        while got < per_search and added < max_new:
            data = api.search(_filters(cfg, search), page=page, per_page=100)
            people = _people(data)
            if not people:
                if exhausted_once or page == 1:
                    log.info("Search '%s' has no more results", name)
                    break
                page, exhausted_once = 1, True  # wrap around once to catch anyone new
                continue

            fresh = [p["id"] for p in people
                     if p.get("id") and p.get("has_email", True) is not False
                     and not con.execute("SELECT 1 FROM apollo_seen WHERE apollo_id=?", (p["id"],)).fetchone()]

            i = 0
            while i < len(fresh):
                need = min(per_search - got, max_new - added)
                if need <= 0:
                    break
                batch = fresh[i:i + min(10, need)]  # never pay to enrich more people than we still need
                i += len(batch)
                res = api.enrich(batch)
                credits += float(res.get("credits_consumed") or 0)
                for pid, match in zip(batch, res.get("matches") or []):
                    checked += 1
                    outcome = "no_match"
                    if match:
                        status = (match.get("email_status") or "").lower()
                        if match.get("email") and status in allowed:
                            if add_lead(con, _lead_row(match, name)):
                                added += 1
                                got += 1
                                outcome = "added"
                            else:
                                outcome = "duplicate_or_suppressed"
                        else:
                            outcome = f"email_{status or 'missing'}"
                    con.execute("INSERT OR IGNORE INTO apollo_seen(apollo_id, result, at) VALUES (?,?,?)",
                                (pid, outcome, iso(now_utc())))
                con.commit()
                time.sleep(1)

            if got < per_search and added < max_new:
                page += 1
            con.execute("INSERT OR REPLACE INTO apollo_progress(search, page) VALUES (?,?)", (name, page))
            con.commit()
            time.sleep(1)

        log.info("Search '%s': added %d", name, got)

    print(f"Added {added} new lead(s) from Apollo; enriched {checked} people, "
          f"used ~{credits:g} credits.")
    return added
