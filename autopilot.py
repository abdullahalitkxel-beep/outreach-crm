"""Autopilot: the rules the BSO Bot runs by, so outreach carries on around the clock without anyone pressing Run.

- Clients and sending domains: every campaign belongs to one client and sends from one or more of that client's
  domains (all the inboxes under them). A domain belongs to exactly one client, never shared between clients.
- Daily limit per inbox set by reputation (bounces, unsubscribes, blocklists, spam-placement tests, authentication),
  between a floor and a ceiling. A new (warmed-up) inbox sends at half volume for its first 5 working days.
- First emails arrive in a morning window in the lead's own time zone, on the lead's working days (no public
  holidays); follow-ups go out in normal working hours. A gap of 6-20 minutes between two emails from one inbox.
- Limits per prospect company, per recipient domain, across clients (90 days in the same line of business;
  never two first emails within 7 days to one person).
- Quality check with a confidence score; per-campaign autonomy (assisted / supervised / autonomous); spot checks.
- Days of lead stock, counting every follow-up the inboxes still owe; the bot only starts as many new leads a day
  as it can also follow up on time.
- Kill switch, and a plain-words answer to "why not more emails today?".
"""
import json
import math
import random
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import bot
from bot import iso, now_utc

DEFAULTS = {
    "tick_minutes": 3,
    "gap_minutes": [6, 20],
    "floor": 5, "ceiling": 30, "start_limit": 10, "half_days": 5,
    "window": "08:45-10:30",
    "hours": [9, 17],
    "recipient_domain_per_day": 2,
    "company_active_sequences": 1, "company_people_30d": 3,
    "cross_client_days": 90, "first_email_gap_days": 7,
    "stock_alert_days": 10, "stock_urgent_days": 3, "alert_repeat_days": 2,
    "owner": "omar@blacswanken.com", "owner_tz": "Asia/Karachi",
    "alert_hour": 6, "digest_hour": 8, "quiet": [23, 7],
    "bounce_warn": 0.02, "bounce_red": 0.03, "unsub_warn": 0.01, "unsub_red": 0.02,
    "default_stop_rate": 0.10,
    "spot_supervised": 20, "spot_autonomous": 50,
    "write_workers": 6, "send_workers": 12, "check_workers": 8, "live_max": 200,
    "supervised_min": 85, "autonomous_min": 60,
}
AUTONOMY = ("assisted", "supervised", "autonomous")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE, line TEXT, facts TEXT, signature TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sending_domains (            -- a domain belongs to exactly one client
    domain TEXT PRIMARY KEY, client_id INTEGER, cap INTEGER DEFAULT 0, added_at TEXT
);
CREATE TABLE IF NOT EXISTS inbox_rep (                  -- each inbox's reputation and today's limit
    email TEXT PRIMARY KEY, started_at TEXT, full_limit INTEGER, rating TEXT, reason TEXT, streak INTEGER DEFAULT 0,
    rated_on TEXT, next_ok_at TEXT, ready INTEGER DEFAULT 0, ready_detail TEXT, ready_at TEXT, half_until TEXT,
    seed_at TEXT, seed_result TEXT
);
CREATE TABLE IF NOT EXISTS spot_checks (
    id INTEGER PRIMARY KEY, lead_id INTEGER, lead_email TEXT, campaign_id INTEGER, sender TEXT, subject TEXT, body TEXT,
    score INTEGER, at TEXT, status TEXT DEFAULT 'open', note TEXT
);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, kind TEXT, key TEXT, at TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS intake_log (
    id INTEGER PRIMARY KEY, at TEXT, frm TEXT, subject TEXT, files TEXT, added INTEGER, rejected INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS auto_log (                   -- every automatic decision, and why
    id INTEGER PRIMARY KEY, at TEXT, kind TEXT, lead_id INTEGER, campaign_id INTEGER, detail TEXT
);
"""


def ensure(con, add_column):
    con.executescript(SCHEMA)
    for col, decl in (("client_id", "INTEGER"), ("domains", "TEXT DEFAULT '[]'"), ("weight", "INTEGER DEFAULT 1"),
                      ("autonomy", "TEXT DEFAULT 'assisted'"), ("arrive", "TEXT"), ("info_mode", "TEXT DEFAULT 'off'"),
                      ("info_text", "TEXT"), ("code", "TEXT"), ("ends_at", "TEXT"), ("allow_roles", "INTEGER DEFAULT 0")):
        add_column(con, "campaigns", col, decl)
    for col, decl in (("quality", "INTEGER"), ("quality_notes", "TEXT"), ("orig_body", "TEXT")):
        add_column(con, "outbox", col, decl)
    for r in con.execute("SELECT id, name FROM campaigns WHERE code IS NULL OR code=''").fetchall():
        con.execute("UPDATE campaigns SET code=? WHERE id=?", (make_code(con, r["name"], r["id"]), r["id"]))


def make_code(con, name, cid=None, client=""):
    base = "-".join(w[:4].upper() for w in re.findall(r"[A-Za-z0-9]+", f"{client} {name}")[:3]) or "CAMP"
    code, n = base, 2
    while con.execute("SELECT 1 FROM campaigns WHERE upper(code)=? AND id IS NOT ?", (code, cid)).fetchone():
        code, n = f"{base}-{n}", n + 1
    return code


def conf(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("autopilot") or {}).items() if v is not None})
    return out


def active(cfg):
    """The autopilot rules apply on the BSO Bot (or when switched on in Settings)."""
    ap = cfg.get("autopilot") or {}
    if ap.get("enabled") is not None:
        return bool(ap["enabled"])
    try:
        import team
        return team.works_as_bot()
    except Exception:
        return False


def note(con, kind, detail, lead_id=None, campaign_id=None):
    con.execute("INSERT INTO auto_log(at, kind, lead_id, campaign_id, detail) VALUES (?,?,?,?,?)",
                (iso(now_utc()), kind, lead_id, campaign_id, str(detail)[:500]))


# ---------------------------------------------------------------- kill switch

def killed(con):
    """(stopped?, why). Stopped from the Master Console or by an email STOP ALL from the owner."""
    r = con.execute("SELECT v FROM app_mode WHERE k='kill'").fetchone()
    if r and r[0]:
        return True, "Stopped by STOP ALL (" + r[0] + "). Send START ALL or press Start in the Master Console."
    try:
        import team
        k = team.state().get("kill")
        if k:
            return True, "Stopped from the Master Console" + (f" ({k})" if isinstance(k, str) else "") + "."
    except Exception:
        pass
    return False, ""


def set_kill(con, on, why=""):
    if on:
        con.execute("INSERT INTO app_mode(k, v) VALUES ('kill', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                    ((why or "stopped") + " " + iso(now_utc())[:16],))
    else:
        con.execute("DELETE FROM app_mode WHERE k='kill'")
    note(con, "kill", "all sending stopped" if on else "sending started again", None)
    con.commit()


# ---------------------------------------------------------------- clients and domains

def domain_of(email):
    return str(email or "").rsplit("@", 1)[-1].lower()


def add_client(con, name, line=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the client a name.")
    con.execute("INSERT INTO clients(name, line, created_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET line=COALESCE(NULLIF(excluded.line,''), line)",
                (name, (line or "").strip(), iso(now_utc())))
    return con.execute("SELECT id FROM clients WHERE name=?", (name,)).fetchone()["id"]


def assign_domain(con, domain, client_id, cap=0):
    """A domain belongs to exactly one client. Raises ValueError when it already belongs to another."""
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain or "." not in domain:
        raise ValueError("Enter a domain like getneosofttech.com.")
    cur = con.execute("SELECT client_id FROM sending_domains WHERE domain=?", (domain,)).fetchone()
    if cur and cur["client_id"] and int(cur["client_id"]) != int(client_id):
        other = con.execute("SELECT name FROM clients WHERE id=?", (cur["client_id"],)).fetchone()
        raise ValueError(f"{domain} belongs to {other['name'] if other else 'another client'}. "
                         "A domain is never shared between clients.")
    con.execute("INSERT INTO sending_domains(domain, client_id, cap, added_at) VALUES (?,?,?,?) "
                "ON CONFLICT(domain) DO UPDATE SET client_id=excluded.client_id, cap=excluded.cap", (domain, client_id, int(cap or 0), iso(now_utc())))
    return domain


def set_campaign_domains(con, cid, domains, weight=None):
    c = con.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not c:
        raise ValueError("No such campaign.")
    ds = sorted({d.strip().lower() for d in domains if d and d.strip()})
    for d in ds:
        r = con.execute("SELECT client_id FROM sending_domains WHERE domain=?", (d,)).fetchone()
        if not r:
            raise ValueError(f"{d} isn't set up as a sending domain yet.")
        if c["client_id"] and r["client_id"] and int(r["client_id"]) != int(c["client_id"]):
            raise ValueError(f"{d} belongs to another client. A domain is never shared between clients.")
    con.execute("UPDATE campaigns SET domains=?, weight=COALESCE(?, weight) WHERE id=?", (json.dumps(ds), weight, cid))


def campaign_domains(c):
    try:
        return [d.lower() for d in json.loads((c["domains"] if c else None) or "[]")]
    except (ValueError, TypeError):
        return []


def campaign_senders(con, c, senders):
    """The inboxes a campaign sends from: every inbox under its chosen domains (or its picked inboxes, as before)."""
    ds = campaign_domains(c)
    if ds:
        return [s for s in senders if domain_of(s["email"]) in ds]
    pick = bot.campaign_inboxes(con, c["id"] if c else 1)
    if not pick and con.execute("SELECT 1 FROM sending_domains LIMIT 1").fetchone():
        return []          # once sending domains are set up, a campaign sends only from the domains it chose
    return [s for s in senders if not pick or s["email"].lower() in pick]


def client_of(con, cid):
    r = con.execute("SELECT cl.* FROM campaigns c JOIN clients cl ON cl.id=c.client_id WHERE c.id=?", (cid or 1,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- lead time zones and working days

COUNTRY = {   # name or code -> (ISO code, time zone, weekend days as isoweekday numbers)
    "united states": ("US", "America/New_York"), "usa": ("US", "America/New_York"), "us": ("US", "America/New_York"),
    "united kingdom": ("GB", "Europe/London"), "uk": ("GB", "Europe/London"), "gb": ("GB", "Europe/London"), "england": ("GB", "Europe/London"),
    "scotland": ("GB", "Europe/London"), "wales": ("GB", "Europe/London"), "ireland": ("IE", "Europe/Dublin"),
    "canada": ("CA", "America/Toronto"), "australia": ("AU", "Australia/Sydney"), "new zealand": ("NZ", "Pacific/Auckland"),
    "germany": ("DE", "Europe/Berlin"), "france": ("FR", "Europe/Paris"), "netherlands": ("NL", "Europe/Amsterdam"),
    "belgium": ("BE", "Europe/Brussels"), "spain": ("ES", "Europe/Madrid"), "italy": ("IT", "Europe/Rome"), "portugal": ("PT", "Europe/Lisbon"),
    "switzerland": ("CH", "Europe/Zurich"), "austria": ("AT", "Europe/Vienna"), "sweden": ("SE", "Europe/Stockholm"),
    "norway": ("NO", "Europe/Oslo"), "denmark": ("DK", "Europe/Copenhagen"), "finland": ("FI", "Europe/Helsinki"),
    "poland": ("PL", "Europe/Warsaw"), "czech republic": ("CZ", "Europe/Prague"), "czechia": ("CZ", "Europe/Prague"),
    "romania": ("RO", "Europe/Bucharest"), "greece": ("GR", "Europe/Athens"), "turkey": ("TR", "Europe/Istanbul"), "türkiye": ("TR", "Europe/Istanbul"),
    "united arab emirates": ("AE", "Asia/Dubai"), "uae": ("AE", "Asia/Dubai"), "saudi arabia": ("SA", "Asia/Riyadh"), "ksa": ("SA", "Asia/Riyadh"),
    "qatar": ("QA", "Asia/Qatar"), "kuwait": ("KW", "Asia/Kuwait"), "bahrain": ("BH", "Asia/Bahrain"), "oman": ("OM", "Asia/Muscat"),
    "egypt": ("EG", "Africa/Cairo"), "israel": ("IL", "Asia/Jerusalem"), "jordan": ("JO", "Asia/Amman"),
    "pakistan": ("PK", "Asia/Karachi"), "india": ("IN", "Asia/Kolkata"), "singapore": ("SG", "Asia/Singapore"),
    "malaysia": ("MY", "Asia/Kuala_Lumpur"), "hong kong": ("HK", "Asia/Hong_Kong"), "japan": ("JP", "Asia/Tokyo"),
    "china": ("CN", "Asia/Shanghai"), "south korea": ("KR", "Asia/Seoul"), "philippines": ("PH", "Asia/Manila"),
    "indonesia": ("ID", "Asia/Jakarta"), "thailand": ("TH", "Asia/Bangkok"), "vietnam": ("VN", "Asia/Ho_Chi_Minh"),
    "south africa": ("ZA", "Africa/Johannesburg"), "nigeria": ("NG", "Africa/Lagos"), "kenya": ("KE", "Africa/Nairobi"),
    "brazil": ("BR", "America/Sao_Paulo"), "mexico": ("MX", "America/Mexico_City"), "argentina": ("AR", "America/Argentina/Buenos_Aires"),
    "chile": ("CL", "America/Santiago"), "colombia": ("CO", "America/Bogota"), "peru": ("PE", "America/Lima"),
}
WEEKEND = {"SA": (5, 6), "QA": (5, 6), "KW": (5, 6), "BH": (5, 6), "OM": (5, 6), "EG": (5, 6), "JO": (5, 6), "IL": (5, 6)}
CITY_TZ = {
    "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "san diego": "America/Los_Angeles",
    "san jose": "America/Los_Angeles", "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles", "las vegas": "America/Los_Angeles",
    "palo alto": "America/Los_Angeles", "irvine": "America/Los_Angeles", "sacramento": "America/Los_Angeles",
    "denver": "America/Denver", "salt lake city": "America/Denver", "boulder": "America/Denver", "phoenix": "America/Phoenix",
    "scottsdale": "America/Phoenix", "chicago": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago",
    "austin": "America/Chicago", "san antonio": "America/Chicago", "minneapolis": "America/Chicago", "kansas city": "America/Chicago",
    "nashville": "America/Chicago", "st. louis": "America/Chicago", "milwaukee": "America/Chicago", "new orleans": "America/Chicago",
    "vancouver": "America/Vancouver", "calgary": "America/Edmonton", "edmonton": "America/Edmonton", "winnipeg": "America/Winnipeg",
    "halifax": "America/Halifax", "perth": "Australia/Perth", "brisbane": "Australia/Brisbane", "adelaide": "Australia/Adelaide",
    "melbourne": "Australia/Melbourne", "auckland": "Pacific/Auckland",
}


def lead_place(lead):
    """(country code, time zone) for a lead, from its country and city columns."""
    x = lead.get("x") if isinstance(lead, dict) and "x" in lead else None
    if x is None:
        try:
            x = json.loads((lead.get("extra") if isinstance(lead, dict) else lead["extra"]) or "{}")
        except (ValueError, TypeError, KeyError):
            x = {}
    country = str(x.get("country") or "").strip().lower()
    city = str(x.get("city") or "").strip().lower()
    code, tz = COUNTRY.get(country, (country.upper()[:2] if len(country) == 2 else "", None))
    if city in CITY_TZ and code in ("US", "CA", "AU", "NZ", ""):
        tz = CITY_TZ[city]
    if x.get("timezone"):
        try:
            ZoneInfo(str(x["timezone"]))
            tz = str(x["timezone"])
        except Exception:
            pass
    return code, tz


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(y, month, (h + l - 7 * m + 114) % 31 + 1)


def _nth(y, month, weekday, n):
    """n-th weekday (0=Mon) of a month; n=-1 for the last."""
    if n > 0:
        d = date(y, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _builtin_holidays(code, y):
    out = {date(y, 1, 1), date(y, 12, 25)}
    e = _easter(y)
    if code == "US":
        out |= {_nth(y, 1, 0, 3), _nth(y, 2, 0, 3), _nth(y, 5, 0, -1), date(y, 6, 19), date(y, 7, 4), _nth(y, 9, 0, 1),
                _nth(y, 11, 3, 4), date(y, 11, 11)}
    elif code == "GB":
        out |= {e - timedelta(days=2), e + timedelta(days=1), _nth(y, 5, 0, 1), _nth(y, 5, 0, -1), _nth(y, 8, 0, -1), date(y, 12, 26)}
    elif code in ("IE", "DE", "FR", "NL", "BE", "AT", "CH", "SE", "NO", "DK", "FI", "AU", "NZ", "CA", "IT", "ES", "PT", "PL"):
        out |= {e - timedelta(days=2), e + timedelta(days=1), date(y, 12, 26)}
        if code == "CA":
            out |= {_nth(y, 9, 0, 1), date(y, 7, 1)}
    elif code in ("AE", "SA", "QA", "KW", "BH", "OM", "PK", "EG", "JO"):
        out = {date(y, 1, 1)} if code in ("AE", "PK") else set()
    return out


_hcache = {}


def is_holiday(code, d):
    if not code:
        return False
    key = (code, d.year)
    if key not in _hcache:
        days = None
        try:
            import holidays as _h          # full calendars (Eid, Diwali, regional days) when the library is available
            days = set(_h.country_holidays(code, years=d.year).keys())
        except Exception:
            days = _builtin_holidays(code, d.year)
        _hcache[key] = days
    return d in _hcache[key]


def working_day(code, d):
    return d.isoweekday() not in WEEKEND.get(code, (6, 7)) and not is_holiday(code, d)


def _hm(s, default):
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return default


def window_of(cfg, c):
    raw = (c["arrive"] if c and "arrive" in c.keys() and c["arrive"] else None) or conf(cfg)["window"]
    a, _, b = str(raw).partition("-")
    return _hm(a, dtime(8, 45)), _hm(b, dtime(10, 30))


def lead_tz(cfg, lead):
    code, tz = lead_place(lead)
    if not tz:
        tz = (cfg.get("schedule") or {}).get("timezone") or "America/New_York"
    return code, ZoneInfo(tz)


def in_window(cfg, c, lead, when=None):
    code, tz = lead_tz(cfg, lead)
    t = (when or now_utc()).astimezone(tz)
    a, b = window_of(cfg, c)
    return working_day(code, t.date()) and a <= t.time() < b


def in_hours(cfg, lead, when=None):
    code, tz = lead_tz(cfg, lead)
    t = (when or now_utc()).astimezone(tz)
    h0, h1 = conf(cfg)["hours"]
    return working_day(code, t.date()) and h0 <= t.hour < h1


def plan_arrival(cfg, c, lead, when=None, rng=random):
    """When the first email should land: a random minute inside the next arrival window in the lead's time zone."""
    code, tz = lead_tz(cfg, lead)
    now = (when or now_utc()).astimezone(tz)
    a, b = window_of(cfg, c)
    d = now.date()
    for _ in range(30):
        if working_day(code, d):
            start = datetime.combine(d, a, tz)
            end = datetime.combine(d, b, tz)
            if d == now.date():
                start = max(start, now + timedelta(minutes=2))
            if end - start >= timedelta(minutes=5):
                span = int((end - start).total_seconds() // 60)
                return (start + timedelta(minutes=rng.randint(0, max(0, span - 3)))).astimezone(timezone.utc).replace(microsecond=0)
        d += timedelta(days=1)
    return (now + timedelta(days=1)).astimezone(timezone.utc)


def lands_text(cfg, lead, when_iso):
    """'lands Tue 09:12 (London)' for the lead page and Review."""
    try:
        code, tz = lead_tz(cfg, lead)
        t = datetime.fromisoformat(when_iso).astimezone(tz)
        place = str(tz.key).split("/")[-1].replace("_", " ")
        return f"lands {t.strftime('%a %d %b %H:%M')} ({place})"
    except Exception:
        return ""


# ---------------------------------------------------------------- inbox limits from reputation

def rep(con, email):
    e = email.lower()
    r = con.execute("SELECT * FROM inbox_rep WHERE email=?", (e,)).fetchone()
    if not r:
        con.execute("INSERT OR IGNORE INTO inbox_rep(email) VALUES (?)", (e,))
        r = con.execute("SELECT * FROM inbox_rep WHERE email=?", (e,)).fetchone()
    return r


def _working_days_between(a, b):
    n, d = 0, a
    while d < b:
        if d.isoweekday() <= 5:
            n += 1
        d += timedelta(days=1)
    return n


def limit_for(cfg, con, s, today=None):
    """(today's limit, reason) for one inbox."""
    ap = conf(cfg)
    r = rep(con, s["email"])
    if not r["ready"]:
        return 0, r["ready_detail"] or "readiness check not done yet"
    today = today or date.today()
    full = int(r["full_limit"] or min(int(s.get("full_limit") or s.get("daily_limit") or ap["start_limit"]), ap["ceiling"]))
    full = max(ap["floor"], min(ap["ceiling"], full))
    why = r["reason"] or "healthy"
    try:
        started = date.fromisoformat((r["started_at"] or "")[:10])
    except ValueError:
        started = today
    if _working_days_between(started, today) < int(ap["half_days"]):
        return max(1, full // 2), f"first {ap['half_days']} working days at half volume"
    if r["half_until"] and r["half_until"] >= today.isoformat():
        return max(1, full // 2), f"half volume after a pause, until {r['half_until']}"
    return full, why


def gap_ok(con, email, when=None):
    r = rep(con, email)
    return not r["next_ok_at"] or r["next_ok_at"] <= iso(when or now_utc())


def after_send(cfg, con, email):
    lo, hi = conf(cfg)["gap_minutes"]
    con.execute("UPDATE inbox_rep SET next_ok_at=? WHERE email=?", (iso(now_utc() + timedelta(minutes=random.uniform(lo, hi))), email.lower()))


def rate_inbox(cfg, con, s, signals):
    """Daily health rating. signals: bounce_rate, unsub_rate, blocklisted, auth_ok, spam_tests (inbox/spam counts).
    Returns (rating, reason, new full limit)."""
    ap = conf(cfg)
    r = rep(con, s["email"])
    full = int(r["full_limit"] or min(int(s.get("full_limit") or s.get("daily_limit") or ap["start_limit"]), ap["ceiling"]))
    streak = int(r["streak"] or 0)
    reds, warns = [], []
    b = signals.get("bounce_rate")
    if b is not None:
        (reds if b > ap["bounce_red"] else warns if b >= ap["bounce_warn"] else []).append(f"{b * 100:.1f}% bounces")
    u = signals.get("unsub_rate")
    if u is not None:
        (reds if u > ap["unsub_red"] else warns if u >= ap["unsub_warn"] else []).append(f"{u * 100:.1f}% unsubscribes/angry replies")
    if signals.get("blocklisted"):
        reds.append("blocklisted: " + str(signals["blocklisted"]))
    if signals.get("auth_ok") is False:
        reds.append("SPF/DKIM/DMARC or login check failing")
    sp = signals.get("spam_tests") or {}
    if sp.get("spam", 0) >= 2:
        reds.append(f"{sp['spam']} spam-placement tests landed in spam")
    elif sp.get("spam", 0) == 1:
        warns.append("1 spam-placement test landed in spam")
    pause = bool(signals.get("blocklisted") or signals.get("auth_ok") is False)
    if reds:
        rating, reason, full, streak = "red", "cut: " + "; ".join(reds), max(ap["floor"], full // 2), 0
    elif warns:
        rating, reason, streak = "warning", "held: " + "; ".join(warns), 0
    else:
        streak += 1
        rating, reason = "healthy", "healthy"
        if streak >= 3:
            new = min(ap["ceiling"], full + max(1, round(full * 0.2)))
            if new > full:
                reason = f"raised from {full} to {new} after 3 healthy days"
            full, streak = new, 0
    con.execute("UPDATE inbox_rep SET rating=?, reason=?, full_limit=?, streak=?, rated_on=? WHERE email=?",
                (rating, reason, full, streak, date.today().isoformat(), s["email"].lower()))
    if pause:
        bot.guard_pause(cfg, con, s["email"], reason[5:] + ".")
    note(con, "reputation", f"{s['email']}: {rating}, limit {full} ({reason})")
    return rating, reason, full


def signals_from_history(con, email, days=7):
    """Bounce and unsubscribe rates from the inbox's own last week."""
    since = iso(now_utc() - timedelta(days=days))
    e = email.lower()
    sent = con.execute("SELECT COUNT(*) FROM events WHERE type='sent' AND lower(sender)=? AND at>=? AND detail NOT LIKE '%(test)%'", (e, since)).fetchone()[0]
    if sent < 10:
        return {}
    bounced = con.execute("SELECT COUNT(*) FROM events WHERE type='bounce' AND lower(sender)=? AND at>=?", (e, since)).fetchone()[0]
    unsub = con.execute("SELECT COUNT(*) FROM events WHERE type='reply' AND detail IN ('unsubscribe') AND lower(sender)=? AND at>=?", (e, since)).fetchone()[0]
    return {"bounce_rate": bounced / sent, "unsub_rate": unsub / sent}


def auto_resume(cfg, con):
    """Inboxes paused for bounces or a failed login try again after 24 hours, at half volume for 3 days.
    A pause for blocklisting or authentication (the whole domain) waits for the owner."""
    out = []
    for r in con.execute("SELECT * FROM inbox_health WHERE paused=1").fetchall():
        why = (r["reason"] or "").lower()
        if "blocklist" in why or "spf" in why or "dkim" in why or "domain" in why:
            continue
        if r["at"] and r["at"] <= iso(now_utc() - timedelta(hours=24)):
            con.execute("UPDATE inbox_health SET paused=0, login_failures=0, resumed_at=? WHERE email=?", (iso(now_utc()), r["email"]))
            rep(con, r["email"])
            con.execute("UPDATE inbox_rep SET half_until=? WHERE email=?", ((date.today() + timedelta(days=3)).isoformat(), r["email"]))
            note(con, "resume", f"{r['email']} resumed on its own after 24 hours, at half volume for 3 days")
            out.append(r["email"])
    con.commit()
    return out


# ---------------------------------------------------------------- campaign shares of shared domains

def campaign_allowance(cfg, con, limits, senders):
    """{campaign id: emails it may send today}. A domain serving several campaigns of one client splits its
    capacity by weight; the per-inbox and per-domain limits always hold across all campaigns."""
    camps = [c for c in con.execute("SELECT * FROM campaigns WHERE COALESCE(status,'active')='active'").fetchall()]
    users = {}
    for c in camps:
        for s in campaign_senders(con, c, senders):
            users.setdefault(s["email"].lower(), []).append(c)
    out = {c["id"]: 0.0 for c in camps}
    for e, cs in users.items():
        total_w = sum(max(1, int(c["weight"] or 1)) for c in cs)
        for c in cs:
            out[c["id"]] += limits.get(e, 0) * max(1, int(c["weight"] or 1)) / total_w
    return {k: int(round(v)) for k, v in out.items()}


def sent_today_campaign(con, cid):
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    return con.execute("SELECT COUNT(*) FROM sent_log WHERE campaign_id=? AND at>=? AND test=0 AND kind IN ('step','reconnect','info')",
                       (cid, iso(start))).fetchone()[0]


def sent_today_domain(con, domain):
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    return con.execute("SELECT COUNT(*) FROM events WHERE type='sent' AND lower(sender) LIKE ? AND at>=?", ("%@" + domain, iso(start))).fetchone()[0]


# ---------------------------------------------------------------- limits on the people we write to

def first_email_hold(cfg, con, lead, c):
    """(None, None) when the first email may go now, else (reason, the earliest time to try again)."""
    import convo
    ap = conf(cfg)
    email = lead["email"].lower()
    now = now_utc()
    # never two first emails to one person within 7 days, whatever the client
    r = con.execute("SELECT MAX(at) FROM sent_log WHERE lower(lead_email)=? AND step=0 AND test=0", (email,)).fetchone()[0]
    if r and r > iso(now - timedelta(days=int(ap["first_email_gap_days"]))):
        return f"got a first email from another campaign on {r[:10]}", datetime.fromisoformat(r) + timedelta(days=int(ap["first_email_gap_days"]))
    # the same person for another client in the same line of business: 90 days
    me = client_of(con, c["id"]) if c else None
    if me and me.get("line"):
        for t in con.execute("SELECT s.at, cl.name, cl.line FROM sent_log s JOIN campaigns c ON c.id=s.campaign_id "
                             "JOIN clients cl ON cl.id=c.client_id WHERE lower(s.lead_email)=? AND s.test=0 AND cl.id!=? ORDER BY s.at DESC",
                             (email, me["id"])).fetchall():
            if (t["line"] or "").strip().lower() == me["line"].strip().lower() and t["at"] > iso(now - timedelta(days=int(ap["cross_client_days"]))):
                return (f"contacted for {t['name']} (same line of business) on {t['at'][:10]}",
                        datetime.fromisoformat(t["at"]) + timedelta(days=int(ap["cross_client_days"])))
    dom = convo.company_domain(email)
    if dom:
        n_today = con.execute("SELECT COUNT(*) FROM sent_log WHERE domain=? AND test=0 AND at>=?",
                              (dom, iso(now.replace(hour=0, minute=0, second=0)))).fetchone()[0]
        if n_today >= int(ap["recipient_domain_per_day"]):
            return f"{n_today} emails to @{dom} today already", now + timedelta(hours=20)
        live = con.execute("SELECT COUNT(*) FROM leads WHERE lower(email) LIKE ? AND id!=? AND status='active' AND step>0",
                           ("%@" + dom, lead["id"])).fetchone()[0]
        if live >= int(ap["company_active_sequences"]):
            return f"another person at {dom} is already in a sequence", now + timedelta(days=3)
        people = con.execute("SELECT COUNT(DISTINCT lower(lead_email)) FROM sent_log WHERE domain=? AND step=0 AND test=0 AND at>=?",
                             (dom, iso(now - timedelta(days=30)))).fetchone()[0]
        if people >= int(ap["company_people_30d"]):
            return f"{people} people at {dom} contacted in the last 30 days", now + timedelta(days=7)
    return None, None


# ---------------------------------------------------------------- quality check and autonomy

def quality(cfg, con, ccfg, lead, subject, body, ai=True, sender_name=""):
    """(score 0-100, [reasons]). Passing means the email may go without a person reading it first.
    The figures check applies to AI-written emails; the owner's own templates are approved text."""
    score, why = 100, []
    hits = bot.find_avoid(ccfg, subject, body)
    if hits:
        score -= 50
        why.append("avoided phrases: " + ", ".join(hits))
    if re.search(r"\{[a-z_]+\}|\[[A-Z ]{3,}\]|\{\{", subject + body):
        score -= 40
        why.append("a placeholder wasn't filled in")
    words = len(re.findall(r"\w+", body))
    if words < 30 or words > 190:
        score -= 15
        why.append(f"{words} words (aim for 40-160)")
    if len(re.findall(r"https?://", body)) > 1:
        score -= 15
        why.append("more than one link")
    if body.count("!") > 1:
        score -= 10
        why.append("several exclamation marks")
    if len([w for w in re.findall(r"\b[A-Z]{4,}\b", body + " " + subject) if w not in ("CMMI", "NASA", "HTTP", "GDPR", "HIPAA", "SaaS")]) > 2:
        score -= 10
        why.append("shouting capitals")
    fn = (lead.get("first_name") or "").strip()
    if fn and fn.lower() not in body[:200].lower():
        score -= 10
        why.append("doesn't greet the person by name")
    try:
        import writer
        b = writer.brief(ccfg, con, lead.get("campaign_id"))
        bad = writer.unsupported_facts(body.replace(sender_name, "") if sender_name else body, b) if ai else []
        if bad:
            score -= min(45, 30 * len(bad))
            why.append("figures not in the approved facts: " + ", ".join(bad[:3]))
        if writer.too_similar(con, body):
            score -= 20
            why.append("too similar to a recent email")
    except Exception:
        pass
    if not subject.strip():
        score -= 20
        why.append("no subject")
    return max(0, score), why


def autonomy_of(c):
    a = (c["autonomy"] if c and "autonomy" in c.keys() else None) or "assisted"
    return a if a in AUTONOMY else "assisted"


def may_send_alone(cfg, c, score):
    ap = conf(cfg)
    a = autonomy_of(c)
    if a == "autonomous":
        return score >= int(ap["autonomous_min"])
    if a == "supervised":
        return score >= int(ap["supervised_min"])
    return False


def maybe_spot_check(cfg, con, c, lead, sender, subject, body, score, rng=random):
    ap = conf(cfg)
    every = {"supervised": int(ap["spot_supervised"]), "autonomous": int(ap["spot_autonomous"])}.get(autonomy_of(c))
    if every and rng.randint(1, every) == 1:
        con.execute("INSERT INTO spot_checks(lead_id, lead_email, campaign_id, sender, subject, body, score, at) VALUES (?,?,?,?,?,?,?,?)",
                    (lead["id"], lead["email"], c["id"], sender, subject, body, score, iso(now_utc())))
        return True
    return False


def suggest_level_up(con, c):
    """Assisted campaigns: suggest Supervised after 50 approved emails nobody had to edit."""
    if autonomy_of(c) != "assisted":
        return None
    n = con.execute("SELECT COUNT(*) FROM outbox o JOIN leads l ON l.id=o.lead_id WHERE COALESCE(l.campaign_id,1)=? "
                    "AND o.status IN ('approved','sent') AND o.orig_body IS NOT NULL AND o.body=o.orig_body", (c["id"],)).fetchone()[0]
    if n >= 50:
        return f"{n} approved emails needed no edits: consider moving \"{c['name']}\" to Supervised."
    return None


# ---------------------------------------------------------------- days of stock

def stop_rate(cfg, con, cid, steps):
    """Share of leads that stop at each step (replied, bounced, unsubscribed), from the campaign's own history."""
    started = con.execute("SELECT COUNT(DISTINCT lead_id) FROM sent_log WHERE campaign_id=? AND step=0 AND test=0", (cid,)).fetchone()[0]
    if started < 100 or steps < 2:
        return float(conf(cfg)["default_stop_rate"])
    rates = []
    for k in range(steps - 1):
        a = con.execute("SELECT COUNT(DISTINCT lead_id) FROM sent_log WHERE campaign_id=? AND step=? AND test=0", (cid, k)).fetchone()[0]
        b = con.execute("SELECT COUNT(DISTINCT lead_id) FROM sent_log WHERE campaign_id=? AND step=? AND test=0", (cid, k + 1)).fetchone()[0]
        if a >= 30:
            rates.append(max(0.0, min(0.9, 1 - b / a)))
    return sum(rates) / len(rates) if rates else float(conf(cfg)["default_stop_rate"])


def stock(cfg, con, c, limits, senders, allowance=None):
    """Days of stock for one campaign, counting follow-ups first. Returns a dict for reports and the alert."""
    ap = conf(cfg)
    ccfg = bot.campaign_cfg(cfg, con, c["id"])
    steps = len(ccfg.get("sequence") or []) or 1
    allowance = allowance if allowance is not None else campaign_allowance(cfg, con, limits, senders)
    cap = float(allowance.get(c["id"], 0))
    horizon = 30
    wd = sum(1 for i in range(horizon) if (date.today() + timedelta(days=i)).isoweekday() <= 5) or 1
    until = iso(now_utc() + timedelta(days=horizon))
    committed = con.execute("SELECT COUNT(*) FROM leads WHERE COALESCE(campaign_id,1)=? AND status='active' AND step>0 AND step<? "
                            "AND (next_send_at IS NULL OR next_send_at<=?)", (c["id"], steps, until)).fetchone()[0]
    committed += con.execute("SELECT COUNT(*) FROM leads WHERE COALESCE(campaign_id,1)=? AND status='nurture' AND check_back IS NOT NULL "
                             "AND check_back<=?", (c["id"], until[:10])).fetchone()[0]
    per_day_committed = committed / wd
    p = stop_rate(cfg, con, c["id"], steps)
    cost = sum((1 - p) ** k for k in range(steps))
    new = max(0.0, cap - per_day_committed) / cost if cost else 0.0
    a, b = window_of(cfg, c)
    win_min = max(0, (datetime.combine(date.today(), b) - datetime.combine(date.today(), a)).total_seconds() / 60)
    n_inboxes = len(campaign_senders(con, c, senders))
    slots = n_inboxes * win_min / (sum(ap["gap_minutes"]) / 2) if n_inboxes else 0
    new = min(new, slots)
    ready = con.execute("SELECT COUNT(*) FROM leads WHERE COALESCE(campaign_id,1)=? AND status='active' AND step=0", (c["id"],)).fetchone()[0]
    days = round(ready / new, 1) if new > 0 else None
    return {"campaign_id": c["id"], "campaign": c["name"], "code": c["code"], "capacity": round(cap, 1), "committed_per_day": round(per_day_committed, 1),
            "cost_per_lead": round(cost, 2), "stop_rate": round(p, 3), "new_per_day": round(new, 1), "ready": ready, "days": days,
            "window_slots": int(slots)}


def new_today_quota(st):
    n = st["new_per_day"]
    return 0 if n <= 0 else max(1, int(round(n)))


def first_emails_today(con, cid):
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    return con.execute("SELECT COUNT(*) FROM sent_log WHERE campaign_id=? AND step=0 AND test=0 AND at>=?", (cid, iso(start))).fetchone()[0]


# ---------------------------------------------------------------- "why not more emails today?"

def why_not(cfg, con, senders=None):
    """Plain-words reasons sending is held back right now."""
    out = []
    k, why = killed(con)
    if k:
        out.append(("stopped", why))
    if senders is None:
        senders = [s for s in bot.load_senders(cfg) if s.get("email") and "example" not in s["email"]]
    paused = bot.guard_paused(con)
    for e, w in paused.items():
        out.append(("paused", f"{e} is paused: {w}"))
    limits, per = {}, []
    full_n = 0
    for s in senders:
        lim, w = limit_for(cfg, con, s)
        limits[s["email"].lower()] = lim
        if lim == 0 and s["email"].lower() not in paused:
            per.append(("not ready", f"{s['email']}: {w}"))
        elif w != "healthy":
            per.append(("limit", f"{s['email']}: {lim} a day ({w})"))
        if lim and bot.sent_today(con, s["email"], cfg) >= lim:
            full_n += 1
            if len(senders) <= 20:
                per.append(("limit", f"{s['email']} has sent its {lim} emails for today"))
    if len(per) > 15:                                   # hundreds of inboxes: a summary instead of a line each
        kinds = {}
        for k, w in per:
            kinds.setdefault(k, []).append(w)
        for k, ws in kinds.items():
            out.append((k, f"{len(ws)} inboxes {'not ready yet' if k == 'not ready' else 'below their full limit'}, e.g. " + "; ".join(ws[:3])))
    else:
        out += per
    if full_n and len(senders) > 20:
        out.append(("limit", f"{full_n} of {len(senders)} inboxes have sent their emails for today"))
    allowance = campaign_allowance(cfg, con, limits, senders)
    for c in con.execute("SELECT * FROM campaigns WHERE COALESCE(status,'active')='active'").fetchall():
        st = stock(cfg, con, c, limits, senders, allowance)
        if st["ready"] == 0:
            out.append(("stock", f"\"{c['name']}\" has no leads waiting for a first email"))
        elif first_emails_today(con, c["id"]) >= new_today_quota(st):
            out.append(("pace", f"\"{c['name']}\": today's {new_today_quota(st)} new lead(s) started; the rest of the capacity is kept for follow-ups"))
        if not campaign_senders(con, c, senders):
            out.append(("setup", f"\"{c['name']}\" has no inboxes: choose its sending domains"))
        waiting = con.execute("SELECT COUNT(*) FROM outbox o JOIN leads l ON l.id=o.lead_id WHERE o.status='pending' AND COALESCE(l.campaign_id,1)=?",
                              (c["id"],)).fetchone()[0]
        if waiting:
            out.append(("review", f"\"{c['name']}\": {waiting} email(s) waiting for approval in Review ({autonomy_of(c)})"))
        nxt = con.execute("SELECT MIN(next_send_at) FROM leads WHERE COALESCE(campaign_id,1)=? AND status='active' AND step=0 AND next_send_at>?",
                          (c["id"], iso(now_utc()))).fetchone()[0]
        if nxt:
            out.append(("window", f"\"{c['name']}\": first emails are queued for the arrival window; next one {nxt[:16].replace('T', ' ')} UTC"))
    held = con.execute("SELECT detail, COUNT(*) n FROM auto_log WHERE kind='hold' AND at>=? GROUP BY detail ORDER BY n DESC LIMIT 5",
                       (iso(now_utc() - timedelta(hours=24)),)).fetchall()
    for h in held:
        out.append(("rules", f"{h['n']} first email(s) held back: {h['detail']}"))
    return out


# ---------------------------------------------------------------- the report the console mirrors

def report(cfg, con):
    senders = [s for s in bot.load_senders(cfg) if s.get("email") and "example" not in s["email"]]
    limits, inboxes = {}, []
    paused = bot.guard_paused(con)
    for s in senders:
        lim, why = limit_for(cfg, con, s)
        limits[s["email"].lower()] = lim
        r = rep(con, s["email"])
        inboxes.append({"email": s["email"], "domain": domain_of(s["email"]), "limit": lim, "why": why, "rating": r["rating"] or "",
                        "sent_today": bot.sent_today(con, s["email"], cfg), "ready": bool(r["ready"]), "ready_detail": r["ready_detail"] or "",
                        "paused": paused.get(s["email"].lower(), ""), "seed": r["seed_result"] or ""})
    allowance = campaign_allowance(cfg, con, limits, senders)
    camps = []
    for c in con.execute("SELECT * FROM campaigns ORDER BY id").fetchall():
        st = stock(cfg, con, c, limits, senders, allowance) if (c["status"] or "active") == "active" else {}
        cl = client_of(con, c["id"]) or {}
        camps.append({"id": c["id"], "name": c["name"], "status": c["status"], "code": c["code"], "client": cl.get("name", ""),
                      "client_id": c["client_id"], "domains": campaign_domains(c), "weight": c["weight"] or 1, "autonomy": autonomy_of(c),
                      "window": "-".join(t.strftime("%H:%M") for t in window_of(cfg, c)), "info_mode": c["info_mode"] or "off",
                      "info_text": (c["info_text"] or "")[:2000], "stock": st, "sent_today": sent_today_campaign(con, c["id"]),
                      "suggest": suggest_level_up(con, c), "ends_at": c["ends_at"] or ""})
    doms = []
    for d in con.execute("SELECT d.*, cl.name client FROM sending_domains d LEFT JOIN clients cl ON cl.id=d.client_id ORDER BY d.domain").fetchall():
        doms.append({"domain": d["domain"], "client": d["client"] or "", "client_id": d["client_id"], "cap": d["cap"] or 0,
                     "inboxes": [i["email"] for i in inboxes if i["domain"] == d["domain"]], "sent_today": sent_today_domain(con, d["domain"]),
                     "campaigns": [c["name"] for c in camps if d["domain"] in c["domains"]]})
    k, kwhy = killed(con)
    return {"at": iso(now_utc()), "killed": k, "killed_why": kwhy, "clients": [dict(r) for r in con.execute("SELECT id, name, line FROM clients ORDER BY name")],
            "campaigns": camps, "domains": doms, "inboxes": inboxes, "why_not": why_not(cfg, con, senders),
            "spot_checks": [dict(r) for r in con.execute("SELECT id, lead_email, campaign_id, sender, subject, body, score, at, status FROM spot_checks "
                                                         "WHERE status='open' ORDER BY id DESC LIMIT 30")],
            "intake": [dict(r) for r in con.execute("SELECT at, frm, subject, files, added, rejected, detail FROM intake_log ORDER BY id DESC LIMIT 20")],
            "activity": [dict(r) for r in con.execute("SELECT at, kind, detail FROM auto_log ORDER BY id DESC LIMIT 60")],
            "alerts": [dict(r) for r in con.execute("SELECT at, kind, text FROM alerts ORDER BY id DESC LIMIT 20")],
            "breakers": _safety().open_breakers(con), "backup": _safety().status(con),
            "queue": [dict(r) for r in con.execute("SELECT l.email, l.next_send_at, c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=COALESCE(l.campaign_id,1) "
                                                   "WHERE l.status='active' AND l.step=0 AND l.next_send_at>? ORDER BY l.next_send_at LIMIT 30", (iso(now_utc()),))]}


def _safety():
    import safety
    return safety


# ---------------------------------------------------------------- commands from the Master Console

def apply_command(cfg, con, cmd):
    """Apply one change the owner made in the Master Console. Returns a short description."""
    kind = cmd.get("cmd")
    if kind == "kill":
        set_kill(con, bool(cmd.get("on")), "Master Console")
        return "stopped" if cmd.get("on") else "started"
    if kind == "client":
        cid = add_client(con, cmd.get("name"), cmd.get("line", ""))
        con.commit()
        return f"client {cmd.get('name')} saved ({cid})"
    if kind == "domain":
        cl = con.execute("SELECT id FROM clients WHERE id=? OR name=?", (cmd.get("client_id"), cmd.get("client"))).fetchone()
        if not cl:
            raise ValueError("Unknown client.")
        d = assign_domain(con, cmd.get("domain"), cl["id"], cmd.get("cap") or 0)
        con.commit()
        return f"domain {d} set up for client {cl['id']}"
    if kind == "campaign":
        cid = cmd.get("id")
        if not cid:
            cid = con.execute("INSERT INTO campaigns(name, status, created_at) VALUES (?, 'active', ?)",
                              (cmd.get("name") or "New campaign", iso(now_utc()))).lastrowid
        c = con.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not c:
            raise ValueError("No such campaign.")
        sets = {}
        if cmd.get("client"):
            cl = con.execute("SELECT id FROM clients WHERE name=? OR id=?", (cmd["client"], cmd["client"])).fetchone()
            if cl:
                sets["client_id"] = cl["id"]
        for k in ("name", "status", "arrive", "info_mode", "info_text", "ends_at", "offer"):
            if cmd.get(k) is not None:
                sets[k] = cmd[k]
        if cmd.get("autonomy") in AUTONOMY:
            sets["autonomy"] = cmd["autonomy"]
        if cmd.get("weight"):
            sets["weight"] = max(1, int(cmd["weight"]))
        if cmd.get("brief") is not None:
            sets["brief"] = json.dumps(cmd["brief"]) if not isinstance(cmd["brief"], str) else cmd["brief"]
        if cmd.get("sequence") is not None:
            sets["sequence"] = json.dumps(cmd["sequence"])
        if sets:
            con.execute(f"UPDATE campaigns SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), cid))
        if cmd.get("domains") is not None:
            set_campaign_domains(con, cid, cmd["domains"])
        if not (c["code"] or "").strip() or cmd.get("code"):
            cl = client_of(con, cid) or {}
            con.execute("UPDATE campaigns SET code=? WHERE id=?", (cmd.get("code") or make_code(con, cmd.get("name") or c["name"], cid, cl.get("name", "")), cid))
        note(con, "console", f"campaign \"{cmd.get('name') or c['name']}\" changed from the Master Console: {', '.join(sorted(set(sets) | ({'domains'} if cmd.get('domains') is not None else set())))}", None, cid)
        con.commit()
        return f"campaign {cid} saved"
    if kind == "limits":
        rows = cmd.get("inboxes") or {}
        for e, v in rows.items():
            rep(con, e)
            con.execute("UPDATE inbox_rep SET full_limit=? WHERE email=?", (max(1, int(v)), e.lower()))
        con.commit()
        return "limits saved"
    if kind == "spot":
        con.execute("UPDATE spot_checks SET status=?, note=? WHERE id=?", (cmd.get("status") or "ok", cmd.get("note") or "", cmd.get("id")))
        con.commit()
        return "spot check recorded"
    if kind == "resume_breaker":
        if not _safety().resume_breaker(con, int(cmd.get("id") or 0), "Master Console"):
            raise ValueError("That pause was already lifted.")
        return "safety breaker pause lifted"
    if kind == "resume_inbox":
        con.execute("UPDATE inbox_health SET paused=0, login_failures=0, resumed_at=? WHERE email=?", (iso(now_utc()), str(cmd.get("email", "")).lower()))
        con.commit()
        return "inbox resumed"
    raise ValueError(f"unknown command {kind}")
