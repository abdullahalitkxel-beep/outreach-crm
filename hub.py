"""BSO master hub: the console where a team owner sets up users, sets their limits and targets,
approves their drafts and watches their activity. Team members keep BSO on their own laptops; each laptop
signs in to this hub with the ID and password the admin created for them (see team.py).

Run it on a computer or server the team can reach:
    BSO.exe --hub                 (Windows app)      or      python hub.py --host 0.0.0.0 --port 8780
Put it behind HTTPS (a reverse proxy or a tunnel) before exposing it to the internet; see HUB_GUIDE.md.

What the hub stores about a member: their name, email and role, which laptop is connected and when it
last checked in, a log of send/reply/draft events (lead email, company, step), and, only if the manager
requires approval, the draft emails waiting for approval. It never receives passwords, API keys, inbox
credentials, the text of replies or the lead list itself.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from email.message import EmailMessage
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from jinja2 import DictLoader
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import brand
import relay

ROLES = ("owner", "manager", "member", "supervisor", "bot")
MAX_INBOXES = 2000          # inboxes one BSO (the bot) can be set up with
EVENT_TYPES = ("sent", "reply", "drafted", "approved", "rejected", "bounce", "error", "handover", "handover_in", "prospect_stage", "settings", "app_start", "paused")
ONLINE_MINUTES = 10
GRACE_HOURS = 72
DEFAULT_PUBLIC_URL = ""          # set in Settings if you run this console as a server for the team
_lock = threading.Lock()


# ---------------------------------------------------------------------------------------------- storage

def data_dir():
    base = os.getenv("HUB_HOME")
    if base:
        p = Path(base)
    else:
        try:
            import bot
            p = Path(bot.DATA) / "hub"
        except Exception:
            p = Path.home() / ".bso-hub"
    p.mkdir(parents=True, exist_ok=True)
    return p


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, username TEXT, role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'invited',          -- invited | active | disabled
    pw_hash TEXT, daily_cap INTEGER DEFAULT 0, weekly_target INTEGER DEFAULT 0,
    approval TEXT DEFAULT 'member',                  -- member | master (the master approves every draft)
    last_seen TEXT, app_version TEXT, created_at TEXT, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY, user_id INTEGER, code_hash TEXT UNIQUE, expires_at TEXT, used_at TEXT
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, name TEXT, created_at TEXT,
    last_seen TEXT, revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, at TEXT, type TEXT, detail TEXT,
    sender TEXT, lead_email TEXT, company TEXT, step INTEGER, variant TEXT,
    UNIQUE(user_id, local_id)
);
CREATE INDEX IF NOT EXISTS ev_user_at ON events(user_id, at);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, user_id INTEGER, local_id INTEGER, lead_email TEXT, company TEXT, step INTEGER,
    sender TEXT, subject TEXT, body TEXT, created_at TEXT,
    status TEXT DEFAULT 'pending',                   -- pending | approved | rejected
    decided_by TEXT, decided_at TEXT, delivered INTEGER DEFAULT 0,
    UNIQUE(user_id, local_id)
);
CREATE TABLE IF NOT EXISTS handovers (
    id INTEGER PRIMARY KEY, from_user_id INTEGER, lead_local_id INTEGER, supervisor_id INTEGER, label TEXT, at TEXT,
    lead_email TEXT, lead_name TEXT, company TEXT, title TEXT, payload TEXT,
    delivered INTEGER DEFAULT 0, status TEXT DEFAULT 'new', status_at TEXT,
    UNIQUE(from_user_id, lead_local_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY, uid TEXT UNIQUE, user_id INTEGER, author_name TEXT, lead_email TEXT, lead_name TEXT, company TEXT,
    text TEXT, at TEXT, to_ TEXT
);
CREATE TABLE IF NOT EXISTS notices (                      -- items for one person, when there is no Google relay to carry them
    id INTEGER PRIMARY KEY, user_id INTEGER, item TEXT, delivered INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transfers (                    -- a removed person's leads, uploaded by their laptop, waiting to be shared out
    id INTEGER PRIMARY KEY, user_id INTEGER, part INTEGER, leads TEXT, at TEXT, UNIQUE(user_id, part)
);
CREATE TABLE IF NOT EXISTS data_audit (                   -- who looked at or exported how much data, reported by each BSO
    id INTEGER PRIMARY KEY, user_id INTEGER, uid TEXT UNIQUE, kind TEXT, detail TEXT, n INTEGER, at TEXT
);
CREATE TABLE IF NOT EXISTS canaries (                     -- trap contacts: never emailed by BSO; if one gets mail, that person's list leaked
    email TEXT PRIMARY KEY, user_id INTEGER, name TEXT, company TEXT, at TEXT, hit_at TEXT, hit_detail TEXT
);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS reset_tokens (id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, expires_at TEXT, used_at TEXT);
CREATE TABLE IF NOT EXISTS throttle (key TEXT PRIMARY KEY, n INTEGER, first_at REAL);
"""


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def db():
    if "db" not in g:
        con = sqlite3.connect(str(DB_PATH), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        g.db = con
    return g.db


def init_db():
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "username" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN username TEXT")
        con.execute("UPDATE users SET username=lower(email) WHERE username IS NULL")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)")
    hcols = {r[1] for r in con.execute("PRAGMA table_info(handovers)")}
    for col, decl in (("deal_value", "REAL"), ("lost_reason", "TEXT")):
        if col not in hcols:
            con.execute(f"ALTER TABLE handovers ADD COLUMN {col} {decl}")
    for col, decl in (("relay_pw", "TEXT"), ("tv", "INTEGER DEFAULT 1"), ("bot_status", "TEXT"), ("away_until", "TEXT"),
                      ("totp_secret", "TEXT"), ("recovery_codes", "TEXT"), ("wipe", "TEXT"),
                      ("setup_sent", "TEXT"), ("setup_applied", "TEXT")):
        if col not in {r[1] for r in con.execute("PRAGMA table_info(users)")}:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
    import vis as _vis
    _vis.ensure(con)
    import hub_bot
    hub_bot.ensure(con)
    con.execute("UPDATE users SET role='supervisor' WHERE role='member'")     # cold email runs on the bot: members are supervisors now
    con.row_factory = sqlite3.Row
    try:            # the console's key pair, for the sealed reports from every BSO (made once, ~2 seconds)
        _vis.keypair(_secret_store(), lambda k, d=None: _m_get(con, k, d), lambda k, v: _m_set(con, k, v))
    except Exception as e:
        print("visibility key:", e)
    con.commit()
    con.close()


def meta_get(key, default=None):
    r = db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(key, value):
    db().execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db().commit()


def audit(action, detail=""):
    actor = (getattr(g, "me", None) or {}).get("email") if getattr(g, "me", None) else "system"
    db().execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), actor, action, detail[:500]))
    db().commit()


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- app

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
DB_PATH = None


class _Sessions(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):          # Secure only when the request really came over HTTPS
        return request.is_secure

    def get_cookie_samesite(self, app):
        return "Lax"


app.session_interface = _Sessions()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_NAME="bso_hub", PERMANENT_SESSION_LIFETIME=timedelta(hours=12))


@app.teardown_appcontext
def _close(_):
    con = g.pop("db", None)
    if con:
        con.close()


def throttled(key, limit, window_s):
    """True when this key has used up its attempts in the window."""
    t = time.time()
    r = db().execute("SELECT n, first_at FROM throttle WHERE key=?", (key,)).fetchone()
    if not r or t - r["first_at"] > window_s:
        return False
    return r["n"] >= limit


def throttle_hit(key, window_s):
    t = time.time()
    r = db().execute("SELECT n, first_at FROM throttle WHERE key=?", (key,)).fetchone()
    if not r or t - r["first_at"] > window_s:
        db().execute("INSERT OR REPLACE INTO throttle(key, n, first_at) VALUES(?,1,?)", (key, t))
    else:
        db().execute("UPDATE throttle SET n=n+1 WHERE key=?", (key,))
    db().commit()


def throttle_clear(key):
    db().execute("DELETE FROM throttle WHERE key=?", (key,))
    db().commit()


@app.before_request
def _load():
    if not app.secret_key:
        app.secret_key = meta_get("secret_key")
    g.me = None
    uid = session.get("uid")
    if uid:
        u = db().execute("SELECT * FROM users WHERE id=? AND status NOT IN ('disabled','removed','removing') AND role IN ('owner','manager')", (uid,)).fetchone()
        g.me = dict(u) if u else None
        if not g.me:
            session.clear()
    if request.method == "POST" and not request.path.startswith("/api/"):
        tok = session.get("csrf")
        sent = request.form.get("_csrf", "")
        if not tok or not hmac.compare_digest(tok, sent):
            abort(400, "The form expired. Go back and try again.")


@app.after_request
def _headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def need(role="manager"):
    """Decorator: owner-only or any console user (owner/manager)."""
    def deco(fn):
        def wrapper(*a, **kw):
            if not g.me:
                return redirect(url_for("login", next=request.path))
            if role == "owner" and g.me["role"] != "owner":
                abort(403, "Only the owner can do this.")
            return fn(*a, **kw)
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def render(name, **kw):
    kw.setdefault("me", g.me)
    kw.setdefault("org", meta_get("org_name", "Your team"))
    kw["csrf"] = csrf()
    return render_template(name, **kw)


def fmt_dt(ts):
    d = parse(ts)
    return d.strftime("%d %b %H:%M UTC") if d else "never"


def ago(ts):
    d = parse(ts)
    if not d:
        return "never"
    s = int((now() - d).total_seconds())
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def is_online(ts):
    d = parse(ts)
    return bool(d and now() - d < timedelta(minutes=ONLINE_MINUTES))


app.jinja_env.filters.update(dt=fmt_dt, ago=ago)
app.jinja_env.globals.update(online=is_online, logo=brand.logo_svg, logo_css=brand.MARK_CSS)


# ---------------------------------------------------------------------------------------------- first-run setup and login

def set_password(con, uid, pw, sign_out=False):
    """Console password + the team-relay check (so this person can sign in to BSO on any laptop)."""
    con.execute("UPDATE users SET pw_hash=?, relay_pw=? WHERE id=?",
                (generate_password_hash(pw), json.dumps(relay.new_password_record(pw)), uid))
    if sign_out:
        con.execute("UPDATE users SET tv=COALESCE(tv,1)+1 WHERE id=?", (uid,))
        con.execute("UPDATE devices SET revoked=1 WHERE user_id=?", (uid,))


def has_owner():
    return db().execute("SELECT 1 FROM users WHERE role='owner' LIMIT 1").fetchone() is not None


def local_request():
    """True only for a browser on this very computer (not through a proxy or tunnel)."""
    orig = request.environ.get("werkzeug.proxy_fix.orig", {}).get("REMOTE_ADDR") or request.environ.get("REMOTE_ADDR", "")
    return orig in ("127.0.0.1", "::1") and not request.headers.get("X-Forwarded-For") and not request.headers.get("Forwarded")


def strong(pw):
    return len(pw or "") >= 10


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if has_owner():
        return redirect(url_for("login"))
    err = None
    if request.method == "POST":
        f = request.form
        key = f.get("setup_key", "").strip()
        ok = local_request() or hmac.compare_digest(sha(key), meta_get("setup_key_hash", ""))
        ip = request.remote_addr or "?"
        if throttled("setup:" + ip, 8, 900):
            err = "Too many attempts. Wait 15 minutes."
        elif not ok:
            throttle_hit("setup:" + ip, 900)
            err = "That setup key is wrong. It is shown in the window or log where you started the hub."
        elif not strong(f.get("password")) or f.get("password") != f.get("password2"):
            err = "The password must be at least 10 characters and typed twice the same."
        elif "@" not in f.get("email", "") or not f.get("name", "").strip():
            err = "Enter your name and email."
        else:
            db().execute("INSERT INTO users(name,email,username,role,status,pw_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                         (f["name"].strip(), f["email"].strip().lower(), f["email"].strip().lower(), "owner", "active", generate_password_hash(f["password"]), iso()))
            meta_set("org_name", f.get("org", "").strip() or "Your team")
            set_password(db(), db().execute("SELECT id FROM users WHERE role='owner'").fetchone()["id"], f["password"])
            db().commit()
            try:
                (data_dir() / "setup-key.txt").unlink()
            except OSError:
                pass
            session.clear()
            u = db().execute("SELECT id FROM users WHERE role='owner'").fetchone()
            session["uid"] = u["id"]
            g.me = dict(db().execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())
            audit("owner_created", f["email"])
            return redirect(url_for("dashboard"))
    return render("setup.html", err=err, local=local_request(), owner_email=meta_get("owner_email", "omar@blacswanken.com"))


# ---------------------------------------------------------------------------------------------- 2-step sign-in

def totp(secret_b32, at=None, step=30, digits=6):
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((at or time.time()) // step)
    h = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((int.from_bytes(h[o:o + 4], "big") & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp_ok(secret_b32, code):
    code = re.sub(r"\s", "", code or "")
    return bool(secret_b32 and code.isdigit()) and any(hmac.compare_digest(totp(secret_b32, time.time() + d * 30), code) for d in (-1, 0, 1))


def use_recovery(con, u, code):
    codes = json.loads(u["recovery_codes"] or "[]")
    h = sha(re.sub(r"[\s-]", "", (code or "").upper()))
    if h in codes:
        codes.remove(h)
        con.execute("UPDATE users SET recovery_codes=? WHERE id=?", (json.dumps(codes), u["id"]))
        con.commit()
        return True
    return False


@app.route("/login/code", methods=["GET", "POST"])
def login_code():
    uid_ = session.get("pending_uid")
    if not uid_ or time.time() - session.get("pending_at", 0) > 300:
        session.clear()
        return redirect(url_for("login"))
    err = None
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE id=?", (uid_,)).fetchone()
        ip = request.remote_addr or "?"
        k = f"totp:{uid_}"
        code = request.form.get("code", "")
        if throttled(k, 6, 900):
            err = "Too many wrong codes. Wait 15 minutes."
        elif u and (totp_ok(u["totp_secret"], code) or use_recovery(db(), u, code)):
            throttle_clear(k)
            nxt = session.get("pending_next", "")
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            g.me = dict(u)
            audit("login (2-step)")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        else:
            throttle_hit(k, 900)
            err = "That code isn't right. Use the 6-digit code from your authenticator app, or one of your recovery codes."
    return render("login_code.html", err=err)


@app.route("/security/2fa", methods=["GET", "POST"])
@need()
def twofa():
    msg, new_codes = None, None
    if request.method == "POST":
        f = request.form
        if f.get("action") == "start":
            session["totp_new"] = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        elif f.get("action") == "confirm" and session.get("totp_new"):
            if totp_ok(session["totp_new"], f.get("code")):
                codes = ["-".join(secrets.token_hex(2).upper() for _ in range(3)) for _ in range(8)]
                db().execute("UPDATE users SET totp_secret=?, recovery_codes=? WHERE id=?",
                             (session.pop("totp_new"), json.dumps([sha(c.replace("-", "")) for c in codes]), g.me["id"]))
                db().commit()
                audit("2-step sign-in switched on")
                new_codes = codes
            else:
                msg = "That code didn't match. Check the time on your phone is set automatically, and try the newest code."
        elif f.get("action") == "off":
            me_ = db().execute("SELECT * FROM users WHERE id=?", (g.me["id"],)).fetchone()
            if totp_ok(me_["totp_secret"], f.get("code")):
                db().execute("UPDATE users SET totp_secret=NULL, recovery_codes=NULL WHERE id=?", (g.me["id"],))
                db().commit()
                audit("2-step sign-in switched off")
                msg = "2-step sign-in is off."
            else:
                msg = "Enter a current code from your authenticator app to switch it off."
    me_ = db().execute("SELECT * FROM users WHERE id=?", (g.me["id"],)).fetchone()
    pending = session.get("totp_new")
    uri = f"otpauth://totp/BSO%20Console:{me_['email']}?secret={pending}&issuer=BSO%20Console" if pending else None
    return render("twofa.html", on=bool(me_["totp_secret"]), pending=pending, uri=uri, msg=msg, new_codes=new_codes, tab="settings")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not has_owner():
        return redirect(url_for("setup"))
    err = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = request.remote_addr or "?"
        k1, k2 = f"login:{ip}:{email}", f"loginip:{ip}"
        if throttled(k1, 5, 900) or throttled(k2, 30, 900):
            err = "Too many failed attempts. Wait 15 minutes and try again."
        else:
            u = db().execute("SELECT * FROM users WHERE (username=? OR email=?) AND status NOT IN ('disabled','removed','removing') AND role IN ('owner','manager')", (email, email)).fetchone()
            if u and u["pw_hash"] and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                throttle_clear(k1)
                session.clear()
                if u["totp_secret"]:                          # second step: the code from their phone
                    session.update(pending_uid=u["id"], pending_at=time.time(), pending_next=request.args.get("next", ""))
                    return redirect(url_for("login_code"))
                session.permanent = True
                session["uid"] = u["id"]
                g.me = dict(u)
                audit("login")
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            throttle_hit(k1, 900)
            throttle_hit(k2, 900)
            err = "Email or password is wrong."
    return render("login.html", err=err)


# ---------------------------------------------------------------------------------------------- password reset by email

RESET_MINUTES = 30


def _secret_store():
    from secretstore import SecretStore
    return SecretStore(data_dir())


def smtp_conf():
    c = {k: meta_get("smtp_" + k, "") for k in ("host", "port", "user", "from")}
    try:
        c["password"] = _secret_store().load().get("HUB_SMTP_PASSWORD", "")
    except Exception:
        c["password"] = ""
    return c


def send_mail(to, subject, body):
    """Send through the mail account set in Settings. Raises RuntimeError with a plain message."""
    c = smtp_conf()
    if not (c["host"] and c["from"]):
        raise RuntimeError("Outgoing email is not set up in the hub's Settings.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = c["from"], to, subject
    msg.set_content(body)
    port = int(c["port"] or 587)
    try:
        if port == 465:
            srv = smtplib.SMTP_SSL(c["host"], port, timeout=20, context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(c["host"], port, timeout=20)
            srv.starttls(context=ssl.create_default_context())
        with srv:
            if c["user"]:
                srv.login(c["user"], c["password"])
            srv.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise RuntimeError(f"Could not send: {e}")


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    done = False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = request.remote_addr or "?"
        done = True                                   # same answer whether or not the address exists
        k1, k2 = f"forgot:{ip}", f"forgote:{email}"
        if not throttled(k1, 6, 3600) and not throttled(k2, 3, 3600):
            throttle_hit(k1, 3600)
            throttle_hit(k2, 3600)
            u = db().execute("SELECT * FROM users WHERE (email=? OR username=?) AND status NOT IN ('disabled','removed','removing') AND role IN ('owner','manager')", (email, email)).fetchone()
            if u:
                token = secrets.token_urlsafe(32)
                db().execute("INSERT INTO reset_tokens(user_id, token_hash, expires_at) VALUES (?,?,?)",
                             (u["id"], sha(token), iso(now() + timedelta(minutes=RESET_MINUTES))))
                db().commit()
                base = (meta_get("public_url", DEFAULT_PUBLIC_URL) or request.host_url).rstrip("/")
                try:
                    send_mail(u["email"], "Reset your BSO master password",
                              f"Hello {u['name']},\n\nSomeone asked to reset the password for your BSO master console account.\n"
                              f"Open this link within {RESET_MINUTES} minutes to choose a new password:\n\n{base}/reset/{token}\n\n"
                              "If it wasn't you, ignore this email. Nothing changes until the link is used.")
                    g.me = None
                    audit("reset_requested", u["email"])
                except RuntimeError as e:
                    app.logger.warning("reset email failed: %s", e)
    return render("forgot.html", done=done)


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    t = db().execute("SELECT * FROM reset_tokens WHERE token_hash=? AND used_at IS NULL", (sha(token),)).fetchone()
    if not t or (parse(t["expires_at"]) or now()) < now():
        return render("reset.html", bad=True, err=None), 400
    err = None
    if request.method == "POST":
        f = request.form
        if not strong(f.get("password")) or f.get("password") != f.get("password2"):
            err = "The password must be at least 10 characters and typed twice the same."
        else:
            set_password(db(), t["user_id"], f["password"])
            db().execute("UPDATE reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (iso(), t["user_id"]))
            db().commit()
            session.clear()
            g.me = None
            u = db().execute("SELECT email FROM users WHERE id=?", (t["user_id"],)).fetchone()
            db().execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), u["email"], "password_reset_by_email", ""))
            db().commit()
            return redirect(url_for("login", reset=1))
    return render("reset.html", bad=False, err=err)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------------------------- stats

def user_stats(days=7):
    """Per-user numbers for the dashboard."""
    since = iso(now() - timedelta(days=days))
    wk = iso(now() - timedelta(days=7))
    out = []
    for u in db().execute("SELECT * FROM users WHERE role IN ('member','manager','owner','supervisor','bot') AND status!='removed' ORDER BY name").fetchall():
        c = lambda t, s=since: db().execute("SELECT COUNT(*) FROM events WHERE user_id=? AND type=? AND at>=?", (u["id"], t, s)).fetchone()[0]
        sent, replies, bounces = c("sent"), c("reply"), c("bounce")
        replies -= db().execute("SELECT COUNT(*) FROM events WHERE user_id=? AND type='reply' AND detail='out_of_office' AND at>=?", (u["id"], since)).fetchone()[0]
        interested = db().execute("SELECT COUNT(*) FROM events WHERE user_id=? AND type='reply' AND at>=? AND detail IN ('interested','question','meeting_request')",
                                  (u["id"], since)).fetchone()[0]
        today = db().execute("SELECT COUNT(*) FROM events WHERE user_id=? AND type='sent' AND at>=?", (u["id"], iso(now().replace(hour=0, minute=0, second=0)))).fetchone()[0]
        pending = db().execute("SELECT COUNT(*) FROM drafts WHERE user_id=? AND status='pending'", (u["id"],)).fetchone()[0]
        week_sent = c("sent", wk)
        out.append(dict(u=dict(u), sent=sent, replies=replies, bounces=bounces, interested=interested, today=today, pending=pending,
                        rate=(f"{replies * 100 / sent:.1f}%" if sent else "–"), week_sent=week_sent,
                        pct=min(100, int(week_sent * 100 / u["weekly_target"])) if u["weekly_target"] else None,
                        online=is_online(u["last_seen"])))
    return out


def daily_series(days=14):
    start = (now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db().execute("SELECT substr(at,1,10) d, COUNT(*) n FROM events WHERE type='sent' AND at>=? GROUP BY d", (iso(start),)).fetchall()
    by = {r["d"]: r["n"] for r in rows}
    return [((start + timedelta(days=i)).strftime("%d %b"), by.get((start + timedelta(days=i)).strftime("%Y-%m-%d"), 0)) for i in range(days)]


# ---------------------------------------------------------------------------------------------- console pages

@app.route("/")
@need()
def dashboard():
    stats = user_stats()
    members = [s for s in stats if s["u"]["status"] != "invited" or s["sent"]]
    totals = dict(sent=sum(s["sent"] for s in stats), replies=sum(s["replies"] for s in stats),
                  interested=sum(s["interested"] for s in stats), pending=sum(s["pending"] for s in stats),
                  online=sum(1 for s in stats if s["online"]),
                  handed=db().execute("SELECT COUNT(*) FROM handovers WHERE at>=?", (iso(now() - timedelta(days=7)),)).fetchone()[0])
    series = daily_series()
    mx = max([n for _, n in series] + [1])
    bots = []
    for u in db().execute("SELECT * FROM users WHERE role='bot' ORDER BY name").fetchall():
        st = json.loads(u["bot_status"] or "{}")
        warn = []
        if u["status"] == "disabled":
            warn.append("Paused in the console: it isn't sending.")
        if not is_online(u["last_seen"]):
            warn.append(f"Not connected since {ago(u['last_seen'])}. Is its computer on, awake and online, with BSO open?")
        lr = parse((st.get("last_run") or "").replace("+00:00", "Z")[:19] + "Z") if st.get("last_run") else None
        if st.get("autorun") and lr and now() - lr > timedelta(minutes=(st.get("every_minutes") or 60) * 2 + 15):
            warn.append(f"Hasn't run since {ago(iso(lr))}.")
        if not st.get("autorun") and st:
            warn.append("Automatic runs are off on the bot (BSO → Settings → Safety & automation).")
        if st.get("last_run_status") == "failed":
            warn.append("The last run reported a problem: " + (st.get("last_run_line") or "see Bot runs in BSO."))
        if st.get("days_of_leads") is not None and st["days_of_leads"] < 3:
            warn.append(f"Only about {st['days_of_leads']} days of new leads left: import more.")
        if st.get("test_mode"):
            warn.append("Test mode is on: nothing goes to real leads yet.")
        for e in st.get("paused_inboxes") or []:
            warn.append(f"Inbox {e} was paused by the deliverability guard (bounces or failed logins). Check it in BSO → Settings → Email inboxes.")
        bots.append(dict(u=dict(u), st=st, warn=warn, online=is_online(u["last_seen"]), last_run=ago(iso(lr)) if lr else "never"))
    hot = [h for h in hot_leads(db()) if not h["answered"]][:30]
    watch = [w for w in supervisor_watch(db()) if w["warn"]]
    return render("dashboard.html", bots=bots, stats=stats, totals=totals, series=series, mx=mx, tab="dashboard", hot=hot, watch=watch, icons=TEMP_ICON,
                  open_incidents=db().execute("SELECT COUNT(*) FROM incidents i LEFT JOIN incident_reviews r ON r.incident_id=i.id WHERE r.incident_id IS NULL").fetchone()[0])


# ------------------------------------------------------------------ full visibility: conversations, archive, hot leads

TEMP_ICON = {"hot": "🔥", "warm": "☀", "cold": "❄", "closed": "⛔"}


def hot_leads(con, days=30):
    """Hot and warm handovers, who has them, and whether they've been answered in time."""
    import convo
    pb_escalate = {"hot": 4, "warm": 18}
    out = []
    since = iso(now() - timedelta(days=days))
    for h in con.execute("SELECT h.*, s.name sup_name, s.email sup_email FROM handovers h LEFT JOIN users s ON s.id=h.supervisor_id "
                         "WHERE h.at>=? ORDER BY h.at DESC", (since,)).fetchall():
        p = json.loads(h["payload"] or "{}")
        temp = p.get("temperature") or ("hot" if h["label"] == "meeting_request" else "warm")
        if temp not in ("hot", "warm"):
            continue
        first = con.execute("SELECT MIN(at) FROM vis_messages WHERE lead_email=? AND direction='out' AND user_id=? AND at>=?",
                            (h["lead_email"], h["supervisor_id"], h["at"])).fetchone()[0]
        moved = (h["status"] or "new") != "new"
        try:
            t0 = datetime.fromisoformat(h["at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except Exception:
            t0 = datetime.now()
        deadline = convo.add_working_hours(t0, int(p.get("respond_hours") or {"hot": 2, "warm": 9}[temp]))
        escalate = convo.add_working_hours(t0, pb_escalate[temp])
        answered = first or (h["status_at"] if moved else None)
        late = not answered and datetime.now() > escalate
        out.append({"h": dict(h), "temp": temp, "reason": p.get("reason") or "", "answered": answered, "deadline": deadline, "late": late,
                    "overdue": not answered and datetime.now() > deadline})
    return out


def supervisor_watch(con):
    """Supervisors whose prospect conversations BSO can't see right now."""
    out = []
    for u in con.execute("SELECT * FROM users WHERE role='supervisor' AND status NOT IN ('removed') ORDER BY name").fetchall():
        st = json.loads(u["bot_status"] or "{}")
        warn = []
        if not st:
            warn.append("hasn't reported yet (BSO not opened since this version)")
        elif not st.get("mailbox_connected"):
            warn.append("work mailbox not connected in BSO: their prospect emails aren't tracked")
        else:
            chk = st.get("mailbox_checked")
            if not chk or chk < iso(now() - timedelta(days=1)):
                warn.append(f"mailbox not checked since {ago(chk) if chk else 'ever'} (BSO closed or laptop off)")
        if u["last_seen"] and u["last_seen"] < iso(now() - timedelta(days=3)):
            warn.append(f"no check-in since {ago(u['last_seen'])}")
        for o in (st.get("overdue") or [])[:5]:
            warn.append(f"overdue: {o.get('subject', '')}")
        out.append({"u": dict(u), "st": st, "warn": warn})
    return out


@app.route("/conversations")
@need()
def conversations():
    con, a = db(), request.args
    users_ = {u["id"]: u for u in con.execute("SELECT id, name, role FROM users").fetchall()}
    camps = con.execute("SELECT l.user_id, COALESCE(l.campaign, 'Main campaign') campaign, COUNT(*) leads, "
                        "SUM(l.temperature='hot') hot, SUM(l.temperature='warm') warm, SUM(l.temperature='cold') cold, "
                        "SUM(l.handed_to IS NOT NULL) handed, SUM(l.status='replied' OR l.status='nurture') replied, "
                        "(SELECT COUNT(*) FROM vis_sent s WHERE s.user_id=l.user_id AND s.kind='step' AND s.lead_email IN "
                        " (SELECT email FROM vis_leads x WHERE x.user_id=l.user_id AND COALESCE(x.campaign,'Main campaign')=COALESCE(l.campaign,'Main campaign'))) sent "
                        "FROM vis_leads l GROUP BY l.user_id, COALESCE(l.campaign, 'Main campaign') ORDER BY l.user_id").fetchall()
    where, args = ["1=1"], []
    if a.get("user", type=int):
        where.append("l.user_id=?")
        args.append(a.get("user", type=int))
    if a.get("campaign"):
        where.append("COALESCE(l.campaign,'Main campaign')=?")
        args.append(a["campaign"])
    what = a.get("what", "")
    cond = {"sent": "EXISTS (SELECT 1 FROM vis_sent s WHERE s.lead_email=l.email AND s.user_id=l.user_id)",
            "replied": "EXISTS (SELECT 1 FROM vis_received r WHERE r.lead_email=l.email)", "hot": "l.temperature='hot'", "warm": "l.temperature='warm'",
            "cold": "l.temperature='cold'", "handed": "l.handed_to IS NOT NULL", "nurture": "l.status='nurture'",
            "flagged": "EXISTS (SELECT 1 FROM incidents i WHERE i.lead_email=l.email)"}.get(what)
    if cond:
        where.append(cond)
    if a.get("q"):
        where.append("(l.email LIKE ? OR l.company LIKE ? OR l.first_name LIKE ? OR EXISTS (SELECT 1 FROM vis_received r WHERE r.lead_email=l.email AND r.body LIKE ?) "
                     "OR EXISTS (SELECT 1 FROM vis_messages m WHERE m.lead_email=l.email AND m.body LIKE ?))")
        args += [f"%{a['q']}%"] * 5
    page = max(1, a.get("page", 1, type=int))
    sql = ("SELECT l.*, (SELECT MAX(at) FROM (SELECT at FROM vis_sent WHERE lead_email=l.email UNION ALL SELECT at FROM vis_received WHERE lead_email=l.email "
           "UNION ALL SELECT at FROM vis_messages WHERE lead_email=l.email)) last_at, "
           "(SELECT COUNT(*) FROM incidents i WHERE i.lead_email=l.email) flags FROM vis_leads l WHERE " + " AND ".join(where))
    total = con.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
    items = con.execute(sql + f" ORDER BY flags>0 DESC, last_at DESC LIMIT 100 OFFSET {(page - 1) * 100}", args).fetchall()
    return render("conversations.html", camps=camps, users_=users_, items=items, total=total, page=page, pages=max(1, (total + 99) // 100),
                  what=what, tab="conversations", icons=TEMP_ICON,
                  ulist=con.execute("SELECT id, name FROM users WHERE status!='removed' ORDER BY name").fetchall(),
                  clist=[r[0] for r in con.execute("SELECT DISTINCT COALESCE(campaign,'Main campaign') FROM vis_leads ORDER BY 1")])


@app.route("/conversation")
@need()
def conversation():
    con = db()
    e = request.args.get("email", "").strip().lower()
    if not e:
        return redirect(url_for("conversations"))
    users_ = {u["id"]: u["name"] for u in con.execute("SELECT id, name FROM users")}
    items = []
    for r in con.execute("SELECT * FROM vis_sent WHERE lead_email=?", (e,)):
        items.append(dict(dir="out", who=users_.get(r["user_id"], "?"), at=r["at"], frm=r["sender"], to=r["to_"], cc=r["cc"], bcc=r["bcc"], subject=r["subject"],
                          body=r["body"], tag=("email " + str((r["step"] or 0) + 1)) if r["kind"] == "step" else r["kind"], extra=f"campaign {r['campaign_id']}"
                          + (f" · version {r['variant']}" if r["variant"] else "")))
    for r in con.execute("SELECT * FROM vis_received WHERE lead_email=?", (e,)):
        items.append(dict(dir="in", who=users_.get(r["user_id"], "?"), at=r["at"], frm=r["frm"], to=r["to_"] or r["inbox"], cc=r["cc"], bcc="", subject=r["subject"],
                          body=r["body"], tag=f"{TEMP_ICON.get(r['temperature'], '')} {r['temperature'] or ''} {(r['label'] or '').replace('_', ' ')}",
                          extra=f"matched by {r['how']}" + (f" · reached via {' → '.join(json.loads(r['path']))}" if r["path"] else "")))
    for r in con.execute("SELECT * FROM vis_messages WHERE lead_email=?", (e,)):
        items.append(dict(dir=r["direction"], who=users_.get(r["user_id"], "?"), at=r["at"], frm=r["frm"], to=r["to_"], cc=r["cc"], bcc=r["bcc"], subject=r["subject"],
                          body=r["body"], tag="supervisor mailbox" if r["via"] != "archive" else "company archive mailbox",
                          extra=(f"{'moved to Trash' if r['deleted_how'] == 'trashed' else 'DELETED'} by the supervisor {ago(r['deleted_at'])}" if r["deleted_at"] else "")))
    items.sort(key=lambda x: x["at"] or "")
    leads_ = con.execute("SELECT * FROM vis_leads WHERE email=?", (e,)).fetchall()
    notes = con.execute("SELECT * FROM vis_notes WHERE lead_email=? ORDER BY at", (e,)).fetchall()
    tasks_ = con.execute("SELECT * FROM vis_tasks WHERE lead_email=? ORDER BY due_date", (e,)).fetchall()
    temps = con.execute("SELECT * FROM vis_temps WHERE lead_email=? ORDER BY at", (e,)).fetchall()
    incs = con.execute("SELECT * FROM incidents WHERE lead_email=? ORDER BY id", (e,)).fetchall()
    arch = con.execute("SELECT * FROM archive WHERE lead_email=? ORDER BY at", (e,)).fetchall()
    return render("conversation.html", e=e, items=items, leads_=leads_, notes=notes, tasks_=tasks_, temps=temps, incs=incs, arch=arch, users_=users_,
                  tab="conversations", icons=TEMP_ICON, kinds=__import__("vis").INCIDENT_KINDS)


# ------------------------------------------------------------------ insights and the strategy profile

def _learn_cfg(con):
    """The AI key and models for the console's own analysis: the key from BSO setup, if one is set."""
    t = team_setup()
    if t.get("anthropic_key"):
        os.environ.setdefault("ANTHROPIC_API_KEY", t["anthropic_key"])
    return {"models": {"personalize": "claude-haiku-5-5", "write": "claude-sonnet-5-5"}}


def strategy_cycle(con, force=False):
    """Once a week (or on request): rebuild the strategy profile and propose it; auto-approve small changes if allowed."""
    import learn
    last = float(_m_get(con, "strategy_ts", "0") or 0)
    if not force and time.time() - last < 7 * 86400:
        return None
    _m_set(con, "strategy_ts", str(time.time()))
    active = json.loads(_m_get(con, "strategy_active", "") or "{}")
    new = learn.build(con, _learn_cfg(con), active)
    lines, small = learn.diff(active, new)
    if not lines and active:
        return None
    if small and _m_get(con, "strategy_auto_small", "0") == "1" and active:
        new["approved_by"], new["approved_at"] = "auto (small changes)", iso()
        _m_set(con, "strategy_active", json.dumps(new))
        _m_set(con, "strategy_pending", "")
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), "system", "strategy_auto_approved", "; ".join(lines)[:300]))
    else:
        _m_set(con, "strategy_pending", json.dumps(dict(new, changes=lines)))
    con.commit()
    return new


@app.route("/insights", methods=["GET", "POST"])
@need()
def insights():
    import learn
    con, msg = db(), None
    if request.method == "POST":
        act = request.form.get("action")
        if act == "recompute":
            strategy_cycle(con, force=True)
            msg = "Recomputed from the latest results."
        elif act in ("approve", "reject") and g.me["role"] == "owner":
            pend = json.loads(meta_get("strategy_pending", "") or "{}")
            if pend and act == "approve":
                pend["approved_by"], pend["approved_at"] = g.me["name"], iso()
                meta_set("strategy_active", json.dumps(pend))
                audit("strategy_approved", "; ".join(pend.get("changes") or [])[:300])
                threading.Thread(target=relay_cycle, daemon=True).start()
                msg = "Approved: every BSO uses the new strategy from its next check-in."
            else:
                audit("strategy_rejected")
                msg = "Kept the current strategy."
            meta_set("strategy_pending", "")
        elif act == "auto" and g.me["role"] == "owner":
            meta_set("strategy_auto_small", "1" if request.form.get("auto") else "0")
            msg = "Saved."
    period = request.args.get("period", "all")
    since = {"30d": iso(now() - timedelta(days=30)), "90d": iso(now() - timedelta(days=90))}.get(period)
    js = learn.journeys(con, since)
    fun = learn.funnel(js)
    by = request.args.get("by", "campaign")
    users_ = {u["id"]: u["name"] for u in con.execute("SELECT id, name FROM users")}
    for j in js:
        j["person"] = users_.get(j["user_id"], "?")
        j["strategy"] = (j.get("arm") or "template").replace("|", " / ")
    keymap = {"campaign": "campaign", "person": "person", "supervisor": "supervisor", "strategy": "strategy", "segment": "segment", "inbox": "inbox"}
    rows_ = learn.breakdown(js, keymap.get(by, "campaign"))
    active = json.loads(meta_get("strategy_active", "") or "{}")
    pending = json.loads(meta_get("strategy_pending", "") or "{}")
    return render("insights.html", tab="insights", msg=msg, fun=fun, svg=learn.svg_funnel(fun), rows=rows_, by=by, period=period,
                  timings=learn.timings(js), active=active, pending=pending, steps=learn.next_steps(con, js)[:60],
                  won_value=sum(j["deal_value"] or 0 for j in js if "won" in j["stages"]), auto=meta_get("strategy_auto_small", "0") == "1",
                  weights=sorted(((k, v) for k, v in (active.get("scoring") or {}).items() if k != "bias"), key=lambda x: -abs(x[1]))[:12])


@app.route("/archive")
@need()
def archive_page():
    q = request.args.get("q", "").strip()
    rows_ = []
    if q:
        rows_ = db().execute("SELECT a.*, u.name uname FROM archive a LEFT JOIN users u ON u.id=a.user_id WHERE a.text LIKE ? OR a.lead_email LIKE ? "
                             "ORDER BY a.at DESC LIMIT 300", (f"%{q}%", f"%{q}%")).fetchall()
    counts = db().execute("SELECT u.name, a.kind, COUNT(*) n FROM archive a LEFT JOIN users u ON u.id=a.user_id GROUP BY a.user_id, a.kind ORDER BY u.name").fetchall()
    return render("archive.html", q=q, rows=rows_, counts=counts, tab="conversations")


@app.route("/users")
@need()
def users():
    return render("users.html", stats=user_stats(30), tab="users", new_creds=session.pop("new_creds", None))


def _valid_int(v, lo=0, hi=100000):
    try:
        return max(lo, min(hi, int(v or 0)))
    except ValueError:
        return 0


def gen_password():
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(12))


def clean_username(v):
    return re.sub(r"[^a-z0-9._@-]", "", (v or "").strip().lower())[:60]


def creds_note(name, username, password):
    return {"name": name, "username": username, "password": password,
            "url": (meta_get("public_url", DEFAULT_PUBLIC_URL) or request.host_url).rstrip("/")}


# ------------------------------------------------------------------ BSO setup sent from the console
# The master enters the mail server, API keys and each person's inboxes once, here. Their BSO receives it at first sign-in.
# It travels encrypted: a random key per person is wrapped with that person's sign-in password (which only the master
# and that person know), and the setup is sealed with that key. Google Drive and the relay only ever hold ciphertext.

import sealbox  # noqa: E402

SETUP_TEAM_DEFAULTS = {"smtp_host": "", "smtp_port": 465, "imap_host": "", "daily_limit": 10, "ramp": True, "autorun_minutes": 30,
                       "live": True, "anthropic_key": "", "instantly_key": ""}


def team_setup():
    try:
        d = json.loads(_secret_store().load().get("SETUP_TEAM", "") or "{}")
    except Exception:
        d = {}
    return dict(SETUP_TEAM_DEFAULTS, **d)


def user_inboxes(uid):
    try:
        return json.loads(_secret_store().load().get(f"SETUP_INBOXES_{uid}", "") or "[]")
    except Exception:
        return []


def parse_inbox_lines(text):
    """'email, name, password' per line. Returns (inboxes, problems)."""
    out, bad, seen = [], [], set()
    for ln, line in enumerate((text or "").splitlines(), 1):
        parts = [p.strip() for p in re.split(r"[,;\t]", line) if p.strip()]
        if not parts:
            continue
        if len(parts) < 3 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", parts[0]):
            bad.append(f"line {ln}: expected email, name, password")
            continue
        e = parts[0].lower()
        if e in seen:
            continue
        seen.add(e)
        out.append({"email": e, "name": parts[1], "password": ",".join(parts[2:])})
    return out[:MAX_INBOXES], bad


def setup_payload(uid):
    t, ib = team_setup(), user_inboxes(uid)
    keys = {k: v for k, v in (("ANTHROPIC_API_KEY", t["anthropic_key"]), ("INSTANTLY_API_KEY", t["instantly_key"])) if v}
    if not ib and not keys and not t["smtp_host"]:
        return None
    senders = [dict(x, smtp_host=x.get("smtp_host") or t["smtp_host"], smtp_port=int(x.get("smtp_port") or t["smtp_port"] or 465),
                    imap_host=x.get("imap_host") or t["imap_host"], daily_limit=int(t["daily_limit"] or 10), ramp=bool(t["ramp"])) for x in ib]
    return {"v": iso(), "senders": senders, "keys": keys,
            "options": {"autorun_minutes": int(t["autorun_minutes"] or 0), "live": bool(t["live"])}}


def _supersede(con, uid, action):
    con.execute("UPDATE notices SET delivered=1 WHERE user_id=? AND delivered=0 AND item LIKE ?", (uid, f'{{"action": "{action}"%'))


def deliver_setup_key(con, uid, username, password):
    """Called whenever the master sets a person's password: lets their BSO open setups sent to it."""
    st = _secret_store()
    k = st.load().get(f"SETUP_KEY_{uid}")
    if not k:
        k = secrets.token_hex(32)
        st.set(f"SETUP_KEY_{uid}", k)
    _supersede(con, uid, "setup_key")
    con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)",
                (uid, json.dumps({"action": "setup_key", "wrap": sealbox.wrap(bytes.fromhex(k), username, password)})))


def push_setup(con, uid):
    """(Re)send this person's setup. Returns False when there's nothing to send or no key yet."""
    k = _secret_store().load().get(f"SETUP_KEY_{uid}")
    data = setup_payload(uid)
    if not (k and data):
        return False
    _supersede(con, uid, "setup")
    con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)",
                (uid, json.dumps({"action": "setup", "v": data["v"], "box": sealbox.seal(bytes.fromhex(k), json.dumps(data))})))
    con.execute("UPDATE users SET setup_sent=? WHERE id=?", (data["v"], uid))
    return True


def push_setup_all(con):
    n = 0
    for u in con.execute("SELECT id FROM users WHERE role IN ('member','manager','bot') AND status NOT IN ('disabled','removed','removing')").fetchall():
        n += push_setup(con, u["id"])
    con.commit()
    return n


@app.route("/bso-setup", methods=["GET", "POST"])
@need("owner")
def bso_setup():
    msg = None
    t = team_setup()
    if request.method == "POST":
        f = request.form
        t.update(smtp_host=f.get("smtp_host", "").strip(), smtp_port=_valid_int(f.get("smtp_port"), 1, 65535) or 465,
                 imap_host=f.get("imap_host", "").strip(), daily_limit=_valid_int(f.get("daily_limit"), 1, 100) or 10,
                 ramp=bool(f.get("ramp")), autorun_minutes=_valid_int(f.get("autorun_minutes"), 0, 1440), live=f.get("mode") == "live")
        for k in ("anthropic_key", "instantly_key"):
            if f.get(k, "").strip():
                t[k] = f[k].strip()
            if f.get("clear_" + k):
                t[k] = ""
        _secret_store().set("SETUP_TEAM", json.dumps(t))
        n = push_setup_all(db())
        audit("bso_setup_changed", f"mail={t['smtp_host']} autorun={t['autorun_minutes']} live={t['live']}")
        threading.Thread(target=relay_cycle, daemon=True).start()
        msg = f"Saved. Sent to {n} person(s); each BSO applies it at its next check-in." if n else "Saved. It goes to each person's BSO with their inboxes (on their user page)."
    people = []
    for u in db().execute("SELECT * FROM users WHERE role IN ('member','manager','bot') AND status NOT IN ('removed') ORDER BY name").fetchall():
        people.append({"id": u["id"], "name": u["name"], "role": u["role"], "inboxes": len(user_inboxes(u["id"])),
                       "sent": u["setup_sent"], "applied": u["setup_applied"]})
    return render("bso_setup.html", t=t, msg=msg, people=people,
                  senders=db().execute("SELECT id, name, role FROM users WHERE role IN ('member','manager','bot') AND status NOT IN ('removed','removing') ORDER BY role!='bot', name").fetchall(), mask=lambda v: ("•" * 8 + v[-4:]) if v else "", tab="bso_setup")


# ---- import inboxes (and the people they belong to) from one spreadsheet

IMPORT_COLS = {
    "email": ("inbox", "inbox email", "email", "email address", "mailbox", "address", "sender email", "from email"),
    "name": ("sender name", "name prospects see", "display name", "from name", "name", "sender"),
    "password": ("password", "inbox password", "mailbox password", "pass", "pwd"),
    "user": ("user", "owner", "assigned to", "belongs to", "sign-in id", "sign in id", "login", "id", "user id", "username"),
    "user_name": ("user full name", "user name", "person", "person name", "full name", "owner name"),
    "user_email": ("user email", "owner email", "person email"),
    "role": ("role",),
}
_CRED_FILES = {}


def read_sheet(storage):
    name = (storage.filename or "").lower()
    data = storage.read()
    if name.endswith((".xlsx", ".xlsm")):
        import io as _io
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
        it = wb.active.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in (next(it, None) or [])]
        return [dict(zip(header, ["" if v is None else str(v).strip() for v in r])) for r in it if any(v not in (None, "") for v in r)]
    import csv as _csv
    import io as _io
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = _csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except _csv.Error:
        dialect = _csv.excel
    return [{str(k or "").strip(): str(v or "").strip() for k, v in r.items()} for r in _csv.DictReader(_io.StringIO(text), dialect=dialect)]


def map_columns(header):
    low = {h.lower().strip(): h for h in header if h}
    out = {}
    for field, names in IMPORT_COLS.items():
        for n_ in names:
            if n_ in low and low[n_] not in out.values():
                out[field] = low[n_]
                break
    return out


def import_inboxes(con, rows, assign_to=None, role_default="member"):
    """Group inbox rows by person; create missing people; add or update their inboxes and send their setup.
    Returns (report rows, new sign-ins, problems)."""
    cols = map_columns(list(rows[0].keys()) if rows else [])
    problems, groups = [], {}
    if not {"email", "password"} <= set(cols):
        return [], [], ["The sheet needs at least an inbox email column and a password column (see the template)."]
    if not assign_to and "user" not in cols and "user_email" not in cols:
        return [], [], ["Choose who the inboxes belong to, or add a 'user' column saying whose each inbox is."]
    for i, r in enumerate(rows, 2):
        e = (r.get(cols["email"]) or "").strip().lower()
        pw = r.get(cols["password"]) or ""
        if not e and not pw:
            continue
        if pw == "replace-with-password":
            problems.append(f"row {i} ({e}): still the example password; skipped")
            continue
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) or not pw:
            problems.append(f"row {i}: needs a valid inbox email and its password")
            continue
        who = assign_to or (r.get(cols.get("user", "")) or r.get(cols.get("user_email", "")) or "").strip().lower()
        if not who:
            problems.append(f"row {i} ({e}): no user given")
            continue
        g_ = groups.setdefault(who, {"inboxes": [], "user_name": "", "user_email": "", "role": ""})
        name = (r.get(cols.get("name", "")) or "").strip() or e.split("@")[0].replace(".", " ").title()
        g_["inboxes"].append({"email": e, "name": name, "password": pw})
        g_["user_name"] = g_["user_name"] or (r.get(cols.get("user_name", "")) or "").strip()
        g_["user_email"] = g_["user_email"] or (r.get(cols.get("user_email", "")) or "").strip().lower()
        g_["role"] = g_["role"] or (r.get(cols.get("role", "")) or "").strip().lower()
    report, creds = [], []
    for who, g_ in groups.items():
        u = con.execute("SELECT * FROM users WHERE (username=? OR email=? OR lower(name)=?) AND status!='removed'", (who, who, who)).fetchone()
        new_pw = None
        if not u:
            username = clean_username(who.split("@")[0] if "@" in who else who)
            email = g_["user_email"] or (who if "@" in who else g_["inboxes"][0]["email"])
            role = g_["role"] if g_["role"] in ("member", "manager", "bot") else role_default
            if len(username) < 3 or con.execute("SELECT 1 FROM users WHERE username=? OR email=?", (username, email)).fetchone():
                problems.append(f"{who}: can't create this person (the ID or email is already used, or the ID is shorter than 3 characters)")
                continue
            new_pw = gen_password()
            name = g_["user_name"] or (who.split("@")[0] if "@" in who else who).replace(".", " ").title()
            con.execute("INSERT INTO users(name,email,username,role,status,pw_hash,approval,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (name, email, username, role, "invited", generate_password_hash(new_pw), "member", iso()))
            u = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            set_password(con, u["id"], new_pw)
            creds.append({"name": name, "username": username, "password": new_pw, "role": role})
        if u["role"] not in ("member", "manager", "bot"):
            problems.append(f"{u['name']} is a {u['role']}; only members, managers and the BSO Bot send emails")
            continue
        have = {x["email"]: x for x in user_inboxes(u["id"])}
        added = sum(1 for x in g_["inboxes"] if x["email"] not in have)
        for x in g_["inboxes"]:
            have[x["email"]] = x
        if len(have) > MAX_INBOXES:
            problems.append(f"{u['name']}: only the first {MAX_INBOXES} inboxes are kept")
        _secret_store().set(f"SETUP_INBOXES_{u['id']}", json.dumps(list(have.values())[:MAX_INBOXES]))
        if new_pw:
            deliver_setup_key(con, u["id"], u["username"], new_pw)
        sent = push_setup(con, u["id"])
        report.append({"id": u["id"], "name": u["name"], "username": u["username"], "new": bool(new_pw), "added": added,
                       "updated": len(g_["inboxes"]) - added, "total": min(len(have), 50), "sent": sent})
    con.commit()
    return report, creds, problems


@app.post("/bso-setup/import")
@need("owner")
def bso_import():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("bso_setup", err="Choose the spreadsheet first."))
    try:
        rows = read_sheet(f)
    except Exception as e:
        return redirect(url_for("bso_setup", err=f"Couldn't read that file: {e}"))
    assign = request.form.get("assign_to", "")
    who = None
    if assign.isdigit():
        u = db().execute("SELECT * FROM users WHERE id=?", (int(assign),)).fetchone()
        who = (u["username"] or u["email"]) if u else None
    report, creds, problems = import_inboxes(db(), rows, assign_to=who, role_default=request.form.get("role", "member"))
    audit("inboxes_imported", f"{sum(r['added'] for r in report)} added, {len(creds)} new user(s), from {f.filename}")
    threading.Thread(target=relay_cycle, daemon=True).start()
    token = None
    if creds:
        token = secrets.token_urlsafe(16)
        _CRED_FILES[token] = (time.time(), creds)
    return render("bso_import.html", report=report, creds=creds, problems=problems, token=token, filename=f.filename, tab="bso_setup")


@app.get("/bso-setup/credentials/<token>.csv")
@need("owner")
def bso_creds(token):
    at, creds = _CRED_FILES.get(token, (0, None))
    if not creds or time.time() - at > 3600:
        abort(404)
    lines = ["name,sign-in id,password,role"] + [f'"{c["name"]}",{c["username"]},{c["password"]},{c["role"]}' for c in creds]
    resp = app.response_class("\r\n".join(lines) + "\r\n", mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=bso-sign-ins.csv"
    return resp


TEMPLATE_HEADERS = ["Inbox Email", "Sender Name", "Inbox Password", "User ID", "User Full Name", "Role"]


def template_xlsx():
    """The import template: one row per inbox, plus a 'How to fill' sheet."""
    import io as _io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inboxes"
    ws.append(TEMPLATE_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1B1F24")
    for row in (["cole.reed@getneosofttech.com", "Cole Reed", "replace-with-password", "bso.bot", "BSO Bot", "bot"],
                ["emma.hart@getneosofttech.com", "Emma Hart", "replace-with-password", "bso.bot", "BSO Bot", "bot"],
                ["sam.lee@getneosofttech.com", "Sam Lee", "replace-with-password", "sam.lee", "Sam Lee", "member"]):
        ws.append(row)
    for col, w in zip("ABCDEF", (36, 20, 26, 16, 20, 10)):
        ws.column_dimensions[col].width = w
    dv = DataValidation(type="list", formula1='"bot,member,manager"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("F2:F500")
    ws.freeze_panes = "A2"
    h = wb.create_sheet("How to fill")
    for line in ("One row per sending inbox. Replace the example rows.",
                 "Inbox Email: the mailbox address BSO sends from (e.g. cole.reed@getneosofttech.com).",
                 "Sender Name: the name prospects see in their inbox.",
                 "Inbox Password: that mailbox's password (from SiteGround). Stored encrypted; never shown again.",
                 "User ID: the BSO sign-in of the person or bot who owns this inbox. Rows with the same User ID go to the same BSO.",
                 "    A new User ID creates that person, with a generated password you download after the import.",
                 "    All inboxes for the BSO Bot: put bso.bot (or your bot's ID) on every row, or leave the column empty and pick the bot when importing.",
                 "User Full Name and Role: only used when the person is new. Role is bot, member or manager (blank = member).",
                 "Importing again adds new inboxes and updates changed passwords; nothing is deleted.",
                 "Delete this file after importing: it contains passwords."):
        h.append([line])
    h.column_dimensions["A"].width = 120
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.get("/bso-setup/template.xlsx")
@need("owner")
def bso_template():
    resp = app.response_class(template_xlsx(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = "attachment; filename=BSO-inboxes-template.xlsx"
    return resp


@app.post("/users/new")
@need("owner")
def user_new():
    f = request.form
    name, email, role = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("role", "supervisor")
    username = clean_username(f.get("username")) or clean_username(email.split("@")[0])
    if not name or "@" not in email or role not in ROLES or role == "owner" or len(username) < 3:
        return redirect(url_for("users", err="Enter a name, an ID of at least 3 characters, a valid email and a role."))
    if db().execute("SELECT 1 FROM users WHERE email=? OR username=?", (email, username)).fetchone():
        return redirect(url_for("users", err="That ID or email is already used."))
    pw = f.get("password", "") or gen_password()
    if not strong(pw):
        return redirect(url_for("users", err="The password must be at least 10 characters (or leave it blank to generate one)."))
    db().execute("INSERT INTO users(name,email,username,role,status,pw_hash,daily_cap,weekly_target,approval,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (name, email, username, role, "invited", generate_password_hash(pw),
                  _valid_int(f.get("daily_cap")), _valid_int(f.get("weekly_target")),
                  "master" if f.get("approval") == "master" else "member", iso()))
    new_id = db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    set_password(db(), new_id, pw)
    inboxes, bad = parse_inbox_lines(f.get("inboxes", ""))
    if role in ("member", "manager", "bot"):
        if inboxes:
            _secret_store().set(f"SETUP_INBOXES_{new_id}", json.dumps(inboxes))
        deliver_setup_key(db(), new_id, username, pw)
        push_setup(db(), new_id)
    db().commit()
    audit("user_created", f"{username} ({role})" + (f", {len(inboxes)} inbox(es) set up" if inboxes else ""))
    session["new_creds"] = creds_note(name, username, pw)
    return redirect(url_for("users"))


@app.route("/users/<int:uid>")
@need()
def user_detail(uid):
    u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() or abort(404)
    st = next(s for s in user_stats(30) if s["u"]["id"] == uid)
    events = db().execute("SELECT * FROM events WHERE user_id=? ORDER BY at DESC, id DESC LIMIT 60", (uid,)).fetchall()
    devices = db().execute("SELECT * FROM devices WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    ib = user_inboxes(uid) if g.me["role"] == "owner" else []
    has_key = bool(_secret_store().load().get(f"SETUP_KEY_{uid}")) if g.me["role"] == "owner" else False
    return render("user.html", u=dict(u), st=st, events=events, devices=devices, tab="users", new_creds=session.pop("new_creds", None),
                  inbox_lines="\n".join(f"{x['email']}, {x['name']}, keep" for x in ib), has_key=has_key, t=team_setup() if g.me["role"] == "owner" else {})


@app.post("/users/<int:uid>")
@need("owner")
def user_save(uid):
    u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() or abort(404)
    f = request.form
    action = f.get("action", "save")
    if action == "save":
        role = f.get("role", u["role"]) if u["role"] != "owner" else "owner"
        if role not in ROLES:
            role = u["role"]
        db().execute("UPDATE users SET name=?, role=?, daily_cap=?, weekly_target=?, approval=?, note=?, away_until=? WHERE id=?",
                     (f.get("name", u["name"]).strip() or u["name"], role, _valid_int(f.get("daily_cap")), _valid_int(f.get("weekly_target")),
                      "master" if f.get("approval") == "master" else "member", f.get("note", "")[:300],
                      (f.get("away_until") or "").strip()[:10] or None, uid))
        audit("user_changed", f"{u['email']}: role={role} cap={f.get('daily_cap')} target={f.get('weekly_target')} approval={f.get('approval')}")
    elif action in ("disable", "enable"):
        if u["role"] == "owner":
            abort(400, "The owner can't be disabled.")
        new = "disabled" if action == "disable" else ("active" if db().execute("SELECT 1 FROM devices WHERE user_id=?", (uid,)).fetchone() or u["role"] != "member" else "invited")
        db().execute("UPDATE users SET status=? WHERE id=?", (new, uid))
        audit("user_" + action + "d", u["email"])
    elif action in ("remove", "remove_now"):
        if u["role"] == "owner" or u["id"] == g.me["id"]:
            abort(400, "The owner can't be removed.")
        try:
            moved, waiting = remove_user(db(), u, wait_for_leads=action == "remove")
        except relay.RelayError as e:
            return redirect(url_for("user_detail", uid=uid, err=f"Couldn't move their prospects: {e}"))
        audit("user_remove_started" if waiting else "user_removed", f"{u['email']}: {moved} prospect(s) shared out")
        threading.Thread(target=relay_cycle, daemon=True).start()
        msg = (f"{moved} open prospect(s) shared equally among the other supervisors. " if moved else "") + \
              ("Their BSO has stopped; at its next check-in it hands over its work (shared equally among the people who remain), erases its copy, and the account is removed." if waiting
               else "The account is removed.")
        return redirect(url_for("users", err=None, ok=msg))
    elif action == "reset2fa":
        db().execute("UPDATE users SET totp_secret=NULL, recovery_codes=NULL WHERE id=?", (uid,))
        audit("2-step sign-in reset", u["email"])
    elif action == "resetpw":
        pw = f.get("password", "") or gen_password()
        if not strong(pw):
            return redirect(url_for("user_detail", uid=uid, err="Password must be at least 10 characters (or leave blank to generate one)."))
        set_password(db(), uid, pw, sign_out=True)   # a new password signs the old laptops out
        if u["role"] in ("member", "manager", "bot"):
            deliver_setup_key(db(), uid, u["username"] or u["email"], pw)
            push_setup(db(), uid)
        session["new_creds"] = creds_note(u["name"], u["username"] or u["email"], pw)
        audit("password_reset", u["email"])
    elif action == "setup":
        inboxes, bad = parse_inbox_lines(f.get("inboxes", ""))
        old = {x["email"]: x for x in user_inboxes(uid)}
        for x in inboxes:                              # "keep" as the password keeps the one already saved
            if x["password"].lower() == "keep" and x["email"] in old:
                x["password"] = old[x["email"]]["password"]
        if bad:
            return redirect(url_for("user_detail", uid=uid, err="Inboxes: " + "; ".join(bad)))
        _secret_store().set(f"SETUP_INBOXES_{uid}", json.dumps(inboxes))
        if f.get("password"):
            if not check_password_hash(u["pw_hash"] or "", f["password"]):
                return redirect(url_for("user_detail", uid=uid, err="That isn't their current BSO password. Use Reset password instead if you don't know it."))
            deliver_setup_key(db(), uid, u["username"] or u["email"], f["password"])
        sent = push_setup(db(), uid)
        audit("bso_setup_changed", f"{u['email']}: {len(inboxes)} inbox(es)")
        db().commit()
        threading.Thread(target=relay_cycle, daemon=True).start()
        return redirect(url_for("user_detail", uid=uid, ok=("Setup sent. Their BSO applies it at its next check-in." if sent else
                                                           "Saved. To send it, enter their current BSO password once below (or reset it), so it can be encrypted for them.")))
    elif action == "wipe_revoke":
        request_wipe(db(), uid, then="disconnect")
        audit("laptop_erase_requested", u["email"])
    elif action == "revoke":
        db().execute("UPDATE devices SET revoked=1 WHERE user_id=?", (uid,))
        db().execute("UPDATE users SET tv=COALESCE(tv,1)+1 WHERE id=?", (uid,))
        audit("devices_revoked", u["email"])
    db().commit()
    return redirect(url_for("user_detail", uid=uid))


@app.route("/approvals")
@need()
def approvals():
    pend = db().execute("SELECT d.*, u.name uname FROM drafts d JOIN users u ON u.id=d.user_id WHERE d.status='pending' ORDER BY d.created_at").fetchall()
    done = db().execute("SELECT d.*, u.name uname FROM drafts d JOIN users u ON u.id=d.user_id WHERE d.status!='pending' ORDER BY d.decided_at DESC LIMIT 15").fetchall()
    return render("approvals.html", pend=pend, done=done, tab="approvals")


@app.post("/approvals/<int:did>")
@need()
def approval_action(did):
    d = db().execute("SELECT * FROM drafts WHERE id=? AND status='pending'", (did,)).fetchone()
    if not d:
        return redirect(url_for("approvals"))
    f = request.form
    action = f.get("action")
    subject = f.get("subject", d["subject"]).strip() or d["subject"]
    body = f.get("body", d["body"]).replace("\r\n", "\n").strip()
    if (d["step"] or 1) > 1:       # follow-ups keep "Re: ..." so they thread (step is 1-based)
        subject = d["subject"]
    if action == "save":
        db().execute("UPDATE drafts SET subject=?, body=? WHERE id=?", (subject, body, did))
    elif action in ("approve", "reject"):
        db().execute("UPDATE drafts SET status=?, subject=?, body=?, decided_by=?, decided_at=?, delivered=0 WHERE id=?",
                     ("approved" if action == "approve" else "rejected", subject, body, g.me["email"], iso(), did))
        audit("draft_" + action + "d", f"{d['lead_email']} (user {d['user_id']})")
    db().commit()
    return redirect(url_for("approvals") + f"#d{did}" if action == "save" else url_for("approvals"))


STAGES_BOARD = [("new", "New"), ("contacted", "Contacted"), ("meeting", "Meeting booked"), ("met", "Meeting held"), ("proposal", "Proposal sent"),
                ("negotiation", "Negotiation"), ("won", "Won"), ("lost", "Lost")]


def _ho_rows(where="", args=()):
    rows_ = db().execute("SELECT h.*, f.name fromname, s.name supname, s.away_until sup_away, "
                         "(SELECT COUNT(*) FROM comments c WHERE c.lead_email=h.lead_email) ncomments "
                         "FROM handovers h LEFT JOIN users f ON f.id=h.from_user_id LEFT JOIN users s ON s.id=h.supervisor_id " + where +
                         " ORDER BY h.at DESC LIMIT 500", args).fetchall()
    out = []
    for r in rows_:
        d = dict(r)
        p = json.loads(d.get("payload") or "{}")
        d["campaign"] = p.get("campaign", "")
        at = parse(d["at"])
        d["age"] = (now() - at).days if at else 0
        d["stale"] = d["status"] == "new" and at and now() - at > timedelta(hours=48)
        out.append(d)
    return out


@app.route("/board")
@need()
def board():
    sid = request.args.get("sup", type=int)
    camp = request.args.get("campaign", "")
    items = _ho_rows("WHERE h.supervisor_id=?" if sid else "", (sid,) if sid else ())
    if camp:
        items = [i for i in items if i["campaign"] == camp]
    cols = [(k, l, [i for i in items if (i["status"] or "new") == k]) for k, l in STAGES_BOARD]
    sups = db().execute("SELECT id, name FROM users WHERE role='supervisor' ORDER BY name").fetchall()
    camps = sorted({i["campaign"] for i in _ho_rows() if i["campaign"]})
    return render("board.html", cols=cols, sups=sups, sid=sid, camps=camps, camp=camp, tab="board")


def _mentioned_users(text):
    low = " " + (text or "").lower()
    out = []
    for u in db().execute("SELECT * FROM users WHERE status NOT IN ('disabled','removed','removing') AND role!='bot'").fetchall():
        names = {u["name"].lower(), u["name"].split()[0].lower(), (u["username"] or "").lower()}
        if any(n and re.search(r"@" + re.escape(n) + r"(?![\w.])", low) for n in names):
            out.append(u)
    return out


@app.route("/handovers/<int:hid>")
@need()
def handover_detail(hid):
    h = (_ho_rows("WHERE h.id=?", (hid,)) or [None])[0] or abort(404)
    p = json.loads(h["payload"] or "{}")
    comments = db().execute("SELECT * FROM comments WHERE lead_email=? ORDER BY at, id", (h["lead_email"],)).fetchall()
    sups = db().execute("SELECT id, name, away_until FROM users WHERE role='supervisor' AND status NOT IN ('disabled','removed','removing') ORDER BY name").fetchall()
    people = db().execute("SELECT name FROM users WHERE status NOT IN ('disabled','removed','removing') AND role!='bot' ORDER BY name").fetchall()
    return render("handover.html", h=h, p=p, comments=comments, sups=sups, people=people, tab="board")


@app.post("/handovers/<int:hid>/comment")
@need()
def handover_comment(hid):
    h = db().execute("SELECT * FROM handovers WHERE id=?", (hid,)).fetchone() or abort(404)
    text = request.form.get("text", "").strip()[:4000]
    if not text:
        return redirect(url_for("handover_detail", hid=hid))
    lead = json.loads(h["payload"] or "{}").get("lead") or {}
    to = {u["id"]: u for u in _mentioned_users(text)}
    if h["supervisor_id"]:
        to.setdefault(h["supervisor_id"], None)
    for r in db().execute("SELECT DISTINCT user_id FROM comments WHERE lead_email=?", (h["lead_email"],)):
        to.setdefault(r["user_id"], None)
    to.pop(g.me["id"], None)
    uid_ = "hub-" + secrets.token_hex(8)
    me_key = relay_key_for(g.me["username"] or g.me["email"])
    db().execute("INSERT INTO comments(uid, user_id, author_name, lead_email, lead_name, company, text, at, to_) VALUES (?,?,?,?,?,?,?,?,?)",
                 (uid_, g.me["id"], g.me["name"], h["lead_email"], h["lead_name"], h["company"], text, iso(), json.dumps(sorted(to))))
    item = {"action": "comment", "uid": uid_, "lead_email": h["lead_email"], "lead_name": h["lead_name"], "company": h["company"],
            "text": text, "author": me_key, "author_name": g.me["name"], "at": iso()}
    for user_id in to:
        db().execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (user_id, json.dumps(item)))
    db().commit()
    threading.Thread(target=relay_cycle, daemon=True).start()        # publish now rather than in a minute
    return redirect(url_for("handover_detail", hid=hid, _anchor="comments"))


@app.post("/handovers/<int:hid>/reassign")
@need()
def handover_reassign(hid):
    h = db().execute("SELECT * FROM handovers WHERE id=?", (hid,)).fetchone() or abort(404)
    new = db().execute("SELECT * FROM users WHERE id=? AND role='supervisor'", (request.form.get("to", type=int),)).fetchone()
    if not new or new["id"] == h["supervisor_id"]:
        return redirect(url_for("handover_detail", hid=hid))
    old = db().execute("SELECT * FROM users WHERE id=?", (h["supervisor_id"],)).fetchone()
    p = json.loads(h["payload"] or "{}")
    item = {"action": "handover", "lead": p.get("lead") or {}, "label": h["label"], "reply": p.get("reply", ""), "intro": p.get("intro", ""),
            "thread": (p.get("thread") or [])[:6], "from": db().execute("SELECT name FROM users WHERE id=?", (h["from_user_id"],)).fetchone()[0] if h["from_user_id"] else "",
            "at": h["at"], "stage": h["status"] or "new", "campaign": p.get("campaign", ""), "reassigned_from": old["name"] if old else ""}
    url, key = _m_get(db(), "relay_url", ""), relay_key()
    try:
        if url and key and relay_v3(db()) and old:
            relay.call(url, {"action": "admin_reassign", "admin_key": key, "from": relay_key_for(old["username"] or old["email"]),
                             "to": relay_key_for(new["username"] or new["email"]), "lead_email": h["lead_email"], "item": item})
        else:
            db().execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (new["id"], json.dumps(item)))
            if old:
                db().execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (old["id"], json.dumps(
                    {"action": "removed", "lead_email": h["lead_email"], "to_name": new["name"], "at": iso()})))
    except relay.RelayError as e:
        return redirect(url_for("handover_detail", hid=hid, err=f"Couldn't move it: {e}"))
    db().execute("UPDATE handovers SET supervisor_id=?, delivered=0 WHERE id=?", (new["id"], hid))
    db().commit()
    audit("prospect_reassigned", f"{h['lead_email']}: {old['name'] if old else '-'} -> {new['name']}")
    threading.Thread(target=relay_cycle, daemon=True).start()
    return redirect(url_for("handover_detail", hid=hid, err=None))


@app.route("/handovers")
@need()
def handovers_page():
    sid = request.args.get("sup", type=int)
    q = ("SELECT h.*, f.name fromname, s.name supname FROM handovers h LEFT JOIN users f ON f.id=h.from_user_id "
         "LEFT JOIN users s ON s.id=h.supervisor_id")
    rows = db().execute(q + (" WHERE h.supervisor_id=?" if sid else "") + " ORDER BY h.at DESC LIMIT 300", ((sid,) if sid else ())).fetchall()
    def hours(a, b):
        da, db_ = parse(a), parse(b)
        return round((db_ - da).total_seconds() / 3600, 1) if da and db_ else None
    items = [dict(r, wait=hours(r["at"], r["status_at"]) if r["status"] != "new" else None) for r in rows]
    sups = db().execute("SELECT u.id, u.name, u.status, u.away_until, (SELECT COUNT(*) FROM handovers h WHERE h.supervisor_id=u.id) n, "
                        "(SELECT COUNT(*) FROM handovers h WHERE h.supervisor_id=u.id AND h.status='new') open_n, "
                        "(SELECT COUNT(*) FROM handovers h WHERE h.supervisor_id=u.id AND h.status IN ('meeting','met','proposal','negotiation','won')) good "
                        "FROM users u WHERE u.role='supervisor' ORDER BY u.name").fetchall()
    return render("handovers.html", items=items, sups=sups, sid=sid, tab="handovers", today=now().strftime("%Y-%m-%d"))


@app.route("/activity")
@need()
def activity():
    uid = request.args.get("user", type=int)
    typ = request.args.get("type", "")
    q = "SELECT e.*, u.name uname FROM events e JOIN users u ON u.id=e.user_id WHERE 1=1"
    a = []
    if uid:
        q += " AND e.user_id=?"
        a.append(uid)
    if typ in EVENT_TYPES:
        q += " AND e.type=?"
        a.append(typ)
    rows = db().execute(q + " ORDER BY e.at DESC, e.id DESC LIMIT 200", a).fetchall()
    ulist = db().execute("SELECT id, name FROM users ORDER BY name").fetchall()
    return render("activity.html", rows=rows, ulist=ulist, uid=uid, typ=typ, types=EVENT_TYPES, tab="activity")


@app.route("/team-defaults.json")
@need("owner")
def team_defaults_file():
    """Drop this file next to BSO.exe to make that install sign-in-only and point it at this hub."""
    url = (meta_get("relay_url") or meta_get("public_url", DEFAULT_PUBLIC_URL) or request.host_url).rstrip("/")
    resp = app.response_class(json.dumps({"hub": url, "required": True}, indent=1), mimetype="application/json")
    resp.headers["Content-Disposition"] = "attachment; filename=team-defaults.json"
    return resp


@app.post("/shutdown")
@need("owner")
def hub_shutdown():
    audit("hub_stopped")
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return "<p style='font:16px system-ui;margin:40px'>The master console has stopped. Start it again from the Start menu: <b>BSO Master Console</b>.</p>"


@app.route("/audit")
@need("owner")
def audit_page():
    return render("audit.html", rows=db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall(), tab="audit")


# ------------------------------------------------------------------ security

CANARY_FIRST = ["Avery", "Jordan", "Riley", "Morgan", "Quinn", "Rowan", "Emerson", "Hollis", "Marlow", "Sutton", "Teagan", "Ellis"]
CANARY_LAST = ["Halvorsen", "Pemberton", "Okafor", "Lindqvist", "Marchetti", "Castellan", "Whitlock", "Ferreira", "Ashdown", "Kovalenko"]
CANARY_CO = ["Northgate", "Bluefin", "Harborline", "Copperleaf", "Stillwater", "Brightmoor", "Ironbridge", "Westvale"]


def ensure_canaries(con, per_user=2):
    """Give every sending account its own trap contacts (fake people at the canary domain).
    BSO never emails them; mail arriving for one means that person's list was copied out."""
    dom = security_cfg(con)["canary_domain"].strip().lower().lstrip("@")
    if not dom:
        return 0
    made = 0
    for u in con.execute("SELECT * FROM users WHERE role IN ('member','manager','bot') AND status NOT IN ('disabled','removed','removing')").fetchall():
        have = con.execute("SELECT COUNT(*) FROM canaries WHERE user_id=? AND email LIKE ?", (u["id"], "%@" + dom)).fetchone()[0]
        leads = []
        for _ in range(per_user - have):
            fn, ln, co = secrets.choice(CANARY_FIRST), secrets.choice(CANARY_LAST), secrets.choice(CANARY_CO)
            email = f"{fn.lower()}.{ln.lower()}{secrets.randbelow(900) + 100}@{dom}"
            company = f"{co} {secrets.choice(['Logistics', 'Partners', 'Analytics', 'Holdings', 'Systems'])}"
            con.execute("INSERT OR IGNORE INTO canaries(email, user_id, name, company, at) VALUES (?,?,?,?,?)", (email, u["id"], f"{fn} {ln}", company, iso()))
            leads.append({"email": email, "first_name": fn, "last_name": ln, "company": company,
                          "title": secrets.choice(["Operations Director", "Head of IT", "VP Engineering", "COO"])})
        if leads:
            con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (u["id"], json.dumps({"action": "canary", "leads": leads})))
            made += len(leads)
    if made:
        con.commit()
    return made


def canary_hit(con, address, detail):
    c = con.execute("SELECT * FROM canaries WHERE email=?", (address.strip().lower(),)).fetchone()
    if not c:
        return None
    if not c["hit_at"]:
        con.execute("UPDATE canaries SET hit_at=?, hit_detail=? WHERE email=?", (iso(), str(detail)[:300], c["email"]))
        u = con.execute("SELECT email FROM users WHERE id=?", (c["user_id"],)).fetchone()
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)",
                    (iso(), "system", "canary_hit", f"{c['email']} (planted for {u['email'] if u else '?'}): {detail}"[:300]))
        con.commit()
    return c


def canary_watch(con):
    """Look in the catch-all mailbox for anything sent to a trap address."""
    host, user = _m_get(con, "canary_imap_host", ""), _m_get(con, "canary_imap_user", "")
    if not (host and user):
        return 0
    try:
        pw = _secret_store().load().get("HUB_CANARY_IMAP_PASSWORD", "")
    except Exception:
        pw = ""
    import email as _email
    import imaplib
    traps = {r["email"] for r in con.execute("SELECT email FROM canaries WHERE hit_at IS NULL").fetchall()}
    if not traps:
        return 0
    hits = 0
    with imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=30) as m:
        m.login(user, pw)
        m.select("INBOX", readonly=True)
        since = (now() - timedelta(days=3)).strftime("%d-%b-%Y")
        _, data = m.search(None, "SINCE", since)
        for num in (data[0].split() if data and data[0] else [])[-500:]:
            _, msg = m.fetch(num, "(BODY.PEEK[HEADER.FIELDS (TO CC DELIVERED-TO X-ORIGINAL-TO FROM SUBJECT DATE)])")
            hdr = _email.message_from_bytes(msg[0][1]) if msg and msg[0] else None
            if not hdr:
                continue
            addrs = " ".join(str(hdr.get_all(k) or "") for k in ("To", "Cc", "Delivered-To", "X-Original-To")).lower()
            for t in list(traps):
                if t in addrs:
                    canary_hit(con, t, f"from {hdr.get('From', '?')} · {hdr.get('Subject', '')} · {hdr.get('Date', '')}")
                    traps.discard(t)
                    hits += 1
    return hits


def security_alerts(con):
    cfg, out = security_cfg(con), []
    for c in con.execute("SELECT c.*, u.name uname FROM canaries c LEFT JOIN users u ON u.id=c.user_id WHERE hit_at IS NOT NULL ORDER BY hit_at DESC").fetchall():
        out.append(("bad", f"Trap contact {c['email']} received mail. It was only ever on {c['uname'] or 'a removed account'}'s laptop, so that list was copied out.", c["hit_detail"]))
    since = iso(now() - timedelta(days=7))
    for a in con.execute("SELECT d.*, u.name uname FROM data_audit d JOIN users u ON u.id=d.user_id WHERE d.at>=? AND (d.kind IN ('export','open_folder','denied') OR d.n>=?) "
                         "ORDER BY d.at DESC LIMIT 30", (since, cfg["big_list"])).fetchall():
        lvl = "bad" if a["kind"] == "denied" else "warn"
        out.append((lvl, f"{a['uname']}: {a['kind'].replace('_', ' ')} · {a['n']} record(s)", f"{a['detail']} · {a['at'][:16].replace('T', ' ')}"))
    try:
        fails = json.loads(_m_get(con, "relay_fails", "{}") or "{}")
    except Exception:
        fails = {}
    tot = {}
    for day, v in fails.items():
        for name, n in (v or {}).items():
            tot[name] = tot.get(name, 0) + int(n or 0)
    for name, n in sorted(tot.items(), key=lambda x: -x[1]):
        if n >= 5:
            out.append(("warn", f"{n} wrong passwords for “{ident_from_relay(name)}” in the last 3 days", "Someone may be guessing. Reset the password if it wasn't them."))
    try:
        locked = json.loads(_m_get(con, "relay_locked", "{}") or "{}")
    except Exception:
        locked = {}
    for name, until in locked.items():
        out.append(("warn", f"“{ident_from_relay(name)}” is locked out until {until[11:16]} UTC", "After 5 wrong passwords sign-in pauses for 15 minutes, then 1 hour, then 6 hours."))
    for u in con.execute("SELECT * FROM users WHERE status NOT IN ('removed','disabled') AND bot_status IS NOT NULL").fetchall():
        try:
            st = json.loads(u["bot_status"] or "{}")
        except Exception:
            st = {}
        if st.get("disk_encrypted") is False:
            out.append(("warn", f"{u['name']}'s laptop drive is not encrypted (BitLocker off)", "If the laptop is lost or stolen, the BSO data on it can be read. Turn on BitLocker or Device encryption."))
    for u in con.execute("SELECT * FROM users WHERE wipe LIKE 'pending%' AND status!='removed'").fetchall():
        out.append(("warn", f"Waiting for {u['name']}'s BSO to erase its data", "This happens at its next check-in."))
    return out


def archive_box_cycle(con, force=False):
    """Read the company archive mailbox (if set up) and compare it with what the supervisors' BSOs reported."""
    import vis as _vis
    host, user = _m_get(con, "archive_box_host", ""), _m_get(con, "archive_box_user", "")
    if not (host and user):
        return 0
    last = float(_m_get(con, "archive_box_ts", "0") or 0)
    if not force and time.time() - last < 600:
        return 0
    _m_set(con, "archive_box_ts", str(time.time()))
    pw = _secret_store().load().get("HUB_ARCHIVE_BOX_PASSWORD", "")
    people = {r["email"].lower(): r["id"] for r in con.execute("SELECT id, email FROM users WHERE role='supervisor' AND status!='removed'")}
    allow = [d for d in security_cfg(con)["allow_domains"].split(",") if d]
    n = _vis.check_archive_box(con, {"host": host, "user": user}, pw, people, own_domains(con), allow)
    _m_set(con, "archive_box_checked", iso())
    return n


@app.route("/guide/workspace")
@need()
def workspace_guide():
    return render("workspace.html", tab="security")


@app.get("/security/incidents.csv")
@need("owner")
def incidents_csv():
    import csv as _csv
    import io as _io
    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(["id", "when", "person", "type", "lead", "what happened", "how caught", "review", "reviewed by", "note", "email (json)", "chain hash"])
    uid = request.args.get("user", type=int)
    for i in db().execute("SELECT i.*, u.name uname, r.status rstatus, r.by rby, r.note rnote FROM incidents i LEFT JOIN users u ON u.id=i.user_id "
                          "LEFT JOIN incident_reviews r ON r.incident_id=i.id" + (" WHERE i.user_id=?" if uid else "") + " ORDER BY i.id", (uid,) if uid else ()):
        w.writerow([i["id"], i["at"], i["uname"] or "console", i["kind"], i["lead_email"], i["detail"], i["how"], i["rstatus"] or "open", i["rby"] or "",
                    i["rnote"] or "", i["msg"] or "", i["hash"]])
    audit("incidents_exported", f"user={uid or 'all'}")
    resp = app.response_class(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=bso-incidents.csv"
    return resp


@app.route("/security", methods=["GET", "POST"])
@need("owner")
def security_page():
    con, msg = db(), None
    if request.method == "POST":
        f = request.form
        act = f.get("action")
        if act == "save":
            dom = f.get("canary_domain", "").strip().lower().lstrip("@")
            if dom and not re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", dom):
                msg = "The trap domain should look like traps.example.com."
            else:
                meta_set("sec_idle_minutes", str(_valid_int(f.get("idle_minutes"), 0, 240)))
                meta_set("sec_reauth_days", str(_valid_int(f.get("reauth_days"), 1, 90)))
                meta_set("sec_big_list", str(_valid_int(f.get("big_list"), 50, 100000)))
                meta_set("sec_export_roles", "owner,manager" + (",member" if f.get("members_export") else ""))
                meta_set("sec_canary_domain", dom)
                meta_set("sec_allow_domains", ",".join(d.strip().lower().lstrip("@") for d in re.split(r"[,;\s]+", f.get("allow_domains", "")) if "." in d))
                meta_set("sec_retention_months", str(_valid_int(f.get("retention_months"), 0, 1200)))
                meta_set("canary_imap_host", f.get("imap_host", "").strip())
                meta_set("canary_imap_user", f.get("imap_user", "").strip())
                if f.get("imap_password"):
                    _secret_store().set("HUB_CANARY_IMAP_PASSWORD", f.get("imap_password"))
                n = ensure_canaries(con)
                audit("security_settings", f"idle={f.get('idle_minutes')} reauth={f.get('reauth_days')} canary={dom}")
                threading.Thread(target=relay_cycle, daemon=True).start()
                msg = "Saved. Every BSO picks this up at its next check-in." + (f" {n} trap contact(s) planted." if n else "")
        elif act == "lookup":
            c = con.execute("SELECT c.*, u.name uname, u.email uemail FROM canaries c LEFT JOIN users u ON u.id=c.user_id WHERE c.email=?",
                            (f.get("address", "").strip().lower(),)).fetchone()
            if c:
                canary_hit(con, c["email"], f.get("note", "").strip() or "reported by hand")
                msg = f"{c['email']} is a trap contact planted only for {c['uname'] or 'a removed account'} ({c['uemail'] or ''}). Logged as a leak."
            else:
                msg = "That address isn't one of our trap contacts."
        elif act == "archive_box":
            meta_set("archive_box_host", f.get("box_host", "").strip())
            meta_set("archive_box_user", f.get("box_user", "").strip())
            if f.get("box_password"):
                _secret_store().set("HUB_ARCHIVE_BOX_PASSWORD", f.get("box_password"))
            audit("archive_mailbox_set", f.get("box_user", ""))
            try:
                n = archive_box_cycle(con, force=True)
                msg = f"Saved. Checked the company archive mailbox: {n} new incident(s)."
            except Exception as e:
                msg = f"Saved, but couldn't read the archive mailbox: {e}"
        elif act == "review":
            iid = _valid_int(f.get("incident_id"), 0, 10**9)
            con.execute("INSERT OR REPLACE INTO incident_reviews(incident_id, status, by, at, note) VALUES (?,?,?,?,?)",
                        (iid, f.get("status") or "reviewed", g.me["name"], iso(), f.get("note", "")[:1000]))
            if f.get("allow_domain"):
                cur_ = security_cfg(con)["allow_domains"]
                meta_set("sec_allow_domains", ",".join(sorted({*[d for d in cur_.split(",") if d], f["allow_domain"].strip().lower()})))
            con.commit()
            audit("incident_reviewed", f"#{iid}: {f.get('status')} {f.get('note', '')[:100]}")
            msg = "Review saved. The incident itself stays in the log unchanged."
        elif act == "check_now":
            try:
                msg = f"Checked the trap mailbox: {canary_watch(con)} new hit(s)."
            except Exception as e:
                msg = f"Couldn't check the trap mailbox: {e}"
    cfg = security_cfg(con)
    rows = con.execute("SELECT d.*, u.name uname FROM data_audit d JOIN users u ON u.id=d.user_id ORDER BY d.at DESC LIMIT 200").fetchall()
    traps = con.execute("SELECT c.*, u.name uname FROM canaries c LEFT JOIN users u ON u.id=c.user_id ORDER BY c.at DESC LIMIT 200").fetchall()
    enc = []
    for u in con.execute("SELECT * FROM users WHERE status NOT IN ('removed') AND role!='owner' ORDER BY name").fetchall():
        try:
            st = json.loads(u["bot_status"] or "{}")
        except Exception:
            st = {}
        enc.append({"name": u["name"], "enc": st.get("disk_encrypted"), "lock": st.get("idle_lock"), "seen": u["last_seen"], "wipe": u["wipe"]})
    import vis as _vis
    kind_f = request.args.get("kind", "")
    iq = "SELECT i.*, u.name uname, r.status rstatus, r.by rby, r.at rat, r.note rnote FROM incidents i LEFT JOIN users u ON u.id=i.user_id " \
         "LEFT JOIN incident_reviews r ON r.incident_id=i.id" + (" WHERE i.kind=?" if kind_f else "") + " ORDER BY (r.incident_id IS NULL) DESC, i.id DESC LIMIT 300"
    incs = con.execute(iq, (kind_f,) if kind_f else ()).fetchall()
    chain_ok, chain_bad = _vis.verify_chain(con)
    return render("security.html", msg=msg, cfg=cfg, alerts=security_alerts(con), rows=rows, traps=traps, enc=enc, incs=incs, kinds=_vis.INCIDENT_KINDS,
                  chain_ok=chain_ok, chain_bad=chain_bad, watch=supervisor_watch(con), kind_f=kind_f,
                  box_host=meta_get("archive_box_host", ""), box_user=meta_get("archive_box_user", ""), box_checked=meta_get("archive_box_checked", ""),
                  imap_host=meta_get("canary_imap_host", ""), imap_user=meta_get("canary_imap_user", ""), tab="security")


@app.route("/settings", methods=["GET", "POST"])
@need("owner")
def hub_settings():
    msg = None
    if request.method == "POST":
        f = request.form
        if f.get("action") == "org":
            meta_set("org_name", f.get("org", "").strip()[:80] or "Your team")
            url = f.get("public_url", "").strip().rstrip("/")
            if url and not re.match(r"^https?://[^\s]+$", url):
                msg = "The public address must start with https:// (or http:// on a local network)."
            else:
                meta_set("public_url", url)
                meta_set("grace_hours", str(_valid_int(f.get("grace_hours"), 0, 720) if f.get("grace_hours") != "" else GRACE_HOURS))
                audit("hub_settings", f"org={f.get('org')} url={url}")
                msg = "Saved."
        elif f.get("action") == "relay_url":
            url = f.get("relay_url", "").strip()
            if not relay.is_relay(url) or not (url.startswith("https://") or url.startswith("http://127.0.0.1:")):
                msg = "Paste the Web app URL from Apps Script. It starts with https://script.google.com/macros/s/ and ends with /exec."
            else:
                meta_set("relay_url", url)
                meta_set("relay_pub_hash", "")
                ok, msg = relay_cycle(db())
                msg = ("Connected. " + msg) if ok else msg
                audit("relay_connected" if ok else "relay_connect_failed", url[:120])
        elif f.get("action") == "relay_sync":
            ok, msg = relay_cycle(db())
        elif f.get("action") in ("smtp", "smtp_test"):
            if f.get("action") == "smtp":
                for k in ("host", "port", "user", "from"):
                    meta_set("smtp_" + k, f.get(k, "").strip())
                if f.get("password"):
                    _secret_store().set("HUB_SMTP_PASSWORD", f["password"])
                audit("smtp_settings", f.get("host", ""))
                msg = "Email settings saved."
            else:
                try:
                    send_mail(g.me["email"], "BSO hub test email", "Your BSO hub can send email. Password reset links will arrive like this.")
                    msg = f"Test email sent to {g.me['email']}."
                except RuntimeError as e:
                    msg = str(e)
        elif f.get("action") == "password":
            me = db().execute("SELECT * FROM users WHERE id=?", (g.me["id"],)).fetchone()
            if not check_password_hash(me["pw_hash"], f.get("old", "")):
                msg = "Your current password is wrong."
            elif not strong(f.get("new")) or f.get("new") != f.get("new2"):
                msg = "The new password must be at least 10 characters and typed twice the same."
            else:
                set_password(db(), g.me["id"], f["new"])
                db().commit()
                audit("password_changed")
                msg = "Password changed."
    return render("settings.html", msg=msg, smtp=smtp_conf(), relay_url=meta_get("relay_url", ""), relay_version=int(meta_get("relay_version", "1") or 1),
                  relay_latest=RELAY_LATEST, relay_ok=meta_get("relay_last_ok", ""),
                  relay_err=meta_get("relay_last_error", ""), relay_script=relay.script_text(relay_key(create=True)), public_url=meta_get("public_url", DEFAULT_PUBLIC_URL), grace=meta_get("grace_hours", str(GRACE_HOURS)), tab="settings")


# ---------------------------------------------------------------------------------------------- API used by team laptops

def api_user():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None, None
    dev = db().execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (sha(h[7:].strip()),)).fetchone()
    if not dev:
        return None, None
    u = db().execute("SELECT * FROM users WHERE id=?", (dev["user_id"],)).fetchone()
    return (dict(u) if u else None), dict(dev) if dev else None


def policy_for(u):
    return {"enabled": u["status"] == "active", "daily_cap": u["daily_cap"] or 0, "weekly_target": u["weekly_target"] or 0,
            "approval": u["approval"] or "member", "org": meta_get("org_name", "Your team"), "role": u["role"], "name": u["name"],
            "grace_hours": int(meta_get("grace_hours", str(GRACE_HOURS)) or 0), "server_time": iso(),
            "message": "Your account is paused by your manager." if u["status"] != "active" else ""}


@app.post("/api/v1/login")
def api_login():
    j = request.get_json(silent=True) or {}
    username = clean_username(j.get("username"))
    ip = request.remote_addr or "?"
    k1, k2 = f"alogin:{ip}:{username}", f"aloginip:{ip}"
    if throttled(k1, 5, 900) or throttled(k2, 40, 900):
        return jsonify(error="Too many failed attempts. Wait 15 minutes and try again."), 429
    u = db().execute("SELECT * FROM users WHERE username=? OR email=?", (username, username)).fetchone()
    if not u or not u["pw_hash"] or not check_password_hash(u["pw_hash"], str(j.get("password", ""))):
        throttle_hit(k1, 900)
        throttle_hit(k2, 900)
        return jsonify(error="ID or password is wrong."), 401
    if u["status"] in ("disabled", "removing", "removed"):
        return jsonify(error="This account is paused. Contact your manager."), 403
    throttle_clear(k1)
    token = secrets.token_urlsafe(32)
    device = str(j.get("device", "laptop"))[:80]
    prior = db().execute("SELECT approved FROM devices WHERE user_id=? AND name=? ORDER BY id DESC LIMIT 1", (u["id"], device)).fetchone()
    others = db().execute("SELECT COUNT(*) FROM devices WHERE user_id=? AND name!=? AND revoked=0 AND approved=1", (u["id"], device)).fetchone()[0]
    approved = 1 if (prior and prior["approved"]) or not others or meta_get("device_approval", "1") != "1" else 0
    db().execute("UPDATE devices SET revoked=1 WHERE user_id=? AND name=? AND revoked=0", (u["id"], device))
    db().execute("INSERT INTO devices(user_id, token_hash, name, created_at, last_seen, approved) VALUES(?,?,?,?,?,?)",
                 (u["id"], sha(token), device, iso(), iso(), approved))
    if not approved:
        audit("device_waiting", f"{u['username']} / {device}: a second laptop waits for approval")
    if u["status"] == "invited":
        db().execute("UPDATE users SET status='active' WHERE id=?", (u["id"],))
    db().commit()
    u = db().execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    g.me = None
    audit("device_signed_in", f"{u['username']} / {device}")
    return jsonify(token=token, policy=policy_for(dict(u)), username=u["username"])


def ingest(con, uid, j):
    """Store one report from a team laptop (events, drafts awaiting approval, delivered decisions). Returns events accepted."""
    n_ev = 0
    import hub_bot
    for e in (j.get("events") or [])[:600]:
        if hub_bot.ingest_extra(con, uid, e):
            continue
        if isinstance(e, dict) and e.get("type") == "__handover":
            store_handover(con, uid, e.get("data") or {})
            continue
        if isinstance(e, dict) and e.get("type") == "__comment":
            c = dict(e.get("data") or {})
            inner = open_team(c.get("text"))
            if isinstance(inner, dict):
                c.update(inner)
            if c.get("uid"):
                con.execute("INSERT OR IGNORE INTO comments(uid, user_id, author_name, lead_email, lead_name, company, text, at, to_) "
                            "VALUES (?,?,(SELECT name FROM users WHERE id=?),?,?,?,?,?,?)",
                            (str(c["uid"])[:80], uid, uid, str(c.get("lead_email") or "").lower()[:200], str(c.get("lead_name") or "")[:200],
                             str(c.get("company") or "")[:200], str(c.get("text") or "")[:4000], str(c.get("at") or iso())[:25], json.dumps(c.get("to") or [])))
                if not relay_v3(con):            # no Google relay v3: this console passes the comment on
                    for k in c.get("to") or []:
                        tu = con.execute("SELECT id FROM users WHERE username=? OR email=?", (ident_from_relay(k), ident_from_relay(k))).fetchone()
                        if tu and tu["id"] != uid:
                            con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (tu["id"], json.dumps(dict(
                                action="comment", uid=c["uid"], lead_email=c.get("lead_email"), lead_name=c.get("lead_name"), company=c.get("company"),
                                text=c.get("text"), author=relay_key_for(con.execute("SELECT COALESCE(username,email) FROM users WHERE id=?", (uid,)).fetchone()[0]),
                                author_name=con.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()[0], at=c.get("at")))))
            continue
        if isinstance(e, dict) and e.get("type") == "__transfer":
            d = e.get("data") or {}
            if d.get("box"):
                try:
                    import sealbox
                    d = json.loads(sealbox.open_(bytes.fromhex(_secret_store().load().get(f"VIS_KEY_{uid}", "")), d["box"]))
                except Exception as ex:
                    print("transfer:", ex)
                    continue
            con.execute("INSERT OR IGNORE INTO transfers(user_id, part, leads, at) VALUES (?,?,?,?)",
                        (uid, int(d.get("part") or 0), json.dumps({"leads": d.get("leads") or [], "suppress": d.get("suppress") or []}), iso()))
            if d.get("last"):
                finish_removal(con, uid)
            continue
        if isinstance(e, dict) and e.get("type") == "__audit":
            a = e.get("data") or {}
            con.execute("INSERT OR IGNORE INTO data_audit(user_id, uid, kind, detail, n, at) VALUES (?,?,?,?,?,?)",
                        (uid, str(a.get("uid") or "")[:80] or None, str(a.get("kind") or "")[:30], str(a.get("detail") or "")[:300],
                         int(a.get("n") or 0), str(a.get("at") or iso())[:25]))
            continue
        if isinstance(e, dict) and e.get("type") == "__vkey":
            import vis as _vis
            try:
                _, priv = _vis.keypair(_secret_store(), lambda k, d=None: _m_get(con, k, d), lambda k, v: _m_set(con, k, v))
                _vis.take_key(con, _secret_store(), uid, str((e.get("data") or {}).get("box") or ""), priv)
            except Exception as ex:
                print("vis key:", ex)
            continue
        if isinstance(e, dict) and e.get("type") == "__vis":
            import vis as _vis
            try:
                _vis.ingest(con, _secret_store(), uid, str((e.get("data") or {}).get("box") or ""))
            except Exception as ex:
                print("vis:", ex)
            continue
        if isinstance(e, dict) and e.get("type") == "__setup":
            con.execute("UPDATE users SET setup_applied=? WHERE id=?", (str((e.get("data") or {}).get("v") or "")[:25], uid))
            continue
        if isinstance(e, dict) and e.get("type") == "__wiped":
            wiped(con, uid)
            continue
        if isinstance(e, dict) and e.get("type") == "__away":
            con.execute("UPDATE users SET away_until=? WHERE id=?", (str((e.get("data") or {}).get("until") or "")[:10] or None, uid))
            continue
        if isinstance(e, dict) and e.get("type") == "__status":
            con.execute("UPDATE users SET bot_status=? WHERE id=?", (json.dumps(e.get("data") or {})[:5000], uid))
            continue
        try:
            con.execute("INSERT OR IGNORE INTO events(user_id, local_id, at, type, detail, sender, lead_email, company, step, variant) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uid, int(e["id"]), str(e.get("at", iso()))[:25], str(e.get("type", ""))[:30], str(e.get("detail", ""))[:300],
                         str(e.get("sender") or "")[:120], str(e.get("lead_email") or "")[:200], str(e.get("company") or "")[:200],
                         e.get("step"), str(e.get("variant") or "")[:80]))
            n_ev += 1
            if e.get("type") == "handover_in" and e.get("lead_email"):
                con.execute("UPDATE handovers SET delivered=1 WHERE supervisor_id=? AND lead_email=?", (uid, str(e["lead_email"]).lower()))
            if e.get("type") == "deal" and e.get("lead_email"):
                try:
                    dd = json.loads(e.get("detail") or "{}")
                except ValueError:
                    dd = {}
                con.execute("UPDATE handovers SET deal_value=COALESCE(?, deal_value), lost_reason=? WHERE supervisor_id=? AND lead_email=?",
                            (dd.get("value") or None, dd.get("reason") if dd.get("status") == "lost" else None, uid, str(e["lead_email"]).lower()))
            if e.get("type") == "prospect_stage" and e.get("lead_email"):
                con.execute("UPDATE handovers SET status=?, status_at=? WHERE supervisor_id=? AND lead_email=?",
                            (str(e.get("detail", ""))[:30], str(e.get("at", iso()))[:25], uid, str(e["lead_email"]).lower()))
        except (KeyError, ValueError, TypeError):
            continue
    for dr in (j.get("drafts") or [])[:200]:
        try:
            lid = int(dr["id"])
        except (KeyError, ValueError, TypeError):
            continue
        cur = con.execute("SELECT * FROM drafts WHERE user_id=? AND local_id=?", (uid, lid)).fetchone()
        vals = (str(dr.get("lead_email", ""))[:200], str(dr.get("company", ""))[:200], dr.get("step", 0), str(dr.get("sender", ""))[:120],
                str(dr.get("subject", ""))[:300], str(dr.get("body", ""))[:20000])
        if not cur:
            con.execute("INSERT INTO drafts(user_id, local_id, lead_email, company, step, sender, subject, body, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (uid, lid, *vals, iso()))
        elif cur["status"] == "pending":  # the member may still be editing; the manager has not decided
            con.execute("UPDATE drafts SET subject=?, body=? WHERE id=?", (vals[4], vals[5], cur["id"]))
    for lid in (j.get("acked") or [])[:600]:
        if isinstance(lid, str) and lid.startswith("ho:nt:"):
            try:
                nt = con.execute("SELECT item FROM notices WHERE id=? AND user_id=? AND delivered=0", (int(lid[6:]), uid)).fetchone()
                con.execute("UPDATE notices SET delivered=1 WHERE id=? AND user_id=?", (int(lid[6:]), uid))
                if nt and '"action": "dnc"' in nt["item"]:
                    import hub_bot
                    hub_bot.dnc_acked(con, uid, json.loads(nt["item"]))
            except ValueError:
                pass
            continue
        if isinstance(lid, str) and lid.startswith("ho:"):
            try:
                con.execute("UPDATE handovers SET delivered=1 WHERE id=? AND supervisor_id=?", (int(lid[3:]), uid))
            except ValueError:
                pass
            continue
        try:
            con.execute("UPDATE drafts SET delivered=1 WHERE user_id=? AND local_id=? AND status!='pending'", (uid, int(lid)))
        except (ValueError, TypeError):
            pass
    return n_ev


def team_key():
    st = _secret_store()
    k = st.load().get("HUB_TEAM_KEY")
    if not k:
        k = secrets.token_hex(32)
        st.set("HUB_TEAM_KEY", k)
    return bytes.fromhex(k)


def open_team(v):
    import sealbox
    if isinstance(v, str) and v.startswith("BSOSEAL:"):
        try:
            return json.loads(sealbox.open_(team_key(), v[8:]))
        except Exception:
            return None
    return None


def seal_team(obj):
    import sealbox
    return "BSOSEAL:" + sealbox.seal(team_key(), json.dumps(obj, default=str))


def store_handover(con, uid, h):
    """A prospect handed over from a team member's laptop: route it to the chosen supervisor."""
    inner = open_team(h.get("reply"))
    if isinstance(inner, dict):
        h = dict(inner, out_id=h.get("out_id"))
    lead = h.get("lead") or {}
    sid = str(h.get("supervisor") or "")
    sup = con.execute("SELECT id FROM users WHERE role IN ('supervisor','owner') AND (username=? OR email=?)", (sid, sid)).fetchone() \
        or con.execute("SELECT id FROM users WHERE role='supervisor' AND status NOT IN ('disabled','removed','removing') ORDER BY id LIMIT 1").fetchone()
    try:
        lid = int(h.get("lead_local_id"))
    except (TypeError, ValueError):
        return
    name = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p) or lead.get("email", "")
    con.execute("INSERT OR IGNORE INTO handovers(from_user_id, lead_local_id, supervisor_id, label, at, lead_email, lead_name, company, title, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, lid, sup["id"] if sup else None, str(h.get("label", ""))[:30], str(h.get("at") or iso())[:25],
                 str(lead.get("email", "")).lower()[:200], name[:200], str(lead.get("company") or "")[:200], str(lead.get("title") or "")[:200],
                 json.dumps(h)[:60000]))
    # the supervisor's own reports may have arrived first (nobody waits for anybody): apply them now
    if sup:
        email = str(lead.get("email", "")).lower()
        got = con.execute("SELECT 1 FROM events WHERE user_id=? AND type='handover_in' AND lower(lead_email)=?", (sup["id"], email)).fetchone()
        last = con.execute("SELECT detail, at FROM events WHERE user_id=? AND type='prospect_stage' AND lower(lead_email)=? ORDER BY at DESC, id DESC LIMIT 1",
                           (sup["id"], email)).fetchone()
        if got:
            con.execute("UPDATE handovers SET delivered=1 WHERE supervisor_id=? AND lead_email=?", (sup["id"], email))
        if last:
            con.execute("UPDATE handovers SET status=?, status_at=? WHERE supervisor_id=? AND lead_email=?", (last["detail"], last["at"], sup["id"], email))


def relay_v3(con):
    return bool(_m_get(con, "relay_url", "")) and int(_m_get(con, "relay_version", "1") or 1) >= 3


def is_away(u):
    return bool(u["away_until"] and u["away_until"] >= now().strftime("%Y-%m-%d"))


def share_out(people, items, load=None):
    """Spread items equally: each goes to whoever has the fewest so far (counting what they already have)."""
    load = dict(load or {p: 0 for p in people})
    out = {p: [] for p in people}
    for it in items:
        p = min(people, key=lambda x: (load.get(x, 0), x))
        out[p].append(it)
        load[p] = load.get(p, 0) + 1
    return out


def reassign_prospect(con, h, new, old):
    """Move one handed-over prospect to another supervisor (through Google when the relay is version 3)."""
    p = json.loads(h["payload"] or "{}")
    frm = con.execute("SELECT name FROM users WHERE id=?", (h["from_user_id"],)).fetchone() if h["from_user_id"] else None
    item = {"action": "handover", "lead": p.get("lead") or {}, "label": h["label"], "reply": p.get("reply", ""), "intro": p.get("intro", ""),
            "thread": (p.get("thread") or [])[:6], "from": frm["name"] if frm else "", "at": h["at"], "stage": h["status"] or "new",
            "campaign": p.get("campaign", ""), "reassigned_from": old["name"] if old else ""}
    url, key = _m_get(con, "relay_url", ""), relay_key()
    if url and key and relay_v3(con) and old:
        relay.call(url, {"action": "admin_reassign", "admin_key": key, "from": relay_key_for(old["username"] or old["email"]),
                         "to": relay_key_for(new["username"] or new["email"]), "lead_email": h["lead_email"], "item": item})
    else:
        con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (new["id"], json.dumps(item)))
        if old and old["status"] not in ("removed",):
            con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (old["id"], json.dumps(
                {"action": "removed", "lead_email": h["lead_email"], "to_name": new["name"], "at": iso()})))
    con.execute("UPDATE handovers SET supervisor_id=?, delivered=0 WHERE id=?", (new["id"], h["id"]))


def finish_removal(con, uid):
    """The person's leads (if any) have arrived: share them out equally, then remove the account for good."""
    u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u or u["status"] == "removed":
        return 0
    leads, supp = [], []
    for t in con.execute("SELECT leads FROM transfers WHERE user_id=? ORDER BY part", (uid,)).fetchall():
        data = json.loads(t["leads"] or "{}")
        leads += data.get("leads") or [] if isinstance(data, dict) else data
        supp += data.get("suppress") or [] if isinstance(data, dict) else []
    takers = con.execute("SELECT * FROM users WHERE id!=? AND role IN ('member','manager','bot','owner') AND status NOT IN ('disabled','removed','removing') "
                         "AND relay_pw IS NOT NULL ORDER BY role='owner', id", (uid,)).fetchall()
    senders = [t for t in takers if t["role"] != "owner"] or takers
    if (leads or supp) and senders:
        shares = share_out([t["id"] for t in senders], leads)
        for tid, chunk in shares.items():
            parts = [chunk[i:i + 300] for i in range(0, len(chunk), 300)] or [[]]
            for i, part in enumerate(parts):           # everyone gets the do-not-contact list
                con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)",
                            (tid, json.dumps({"action": "leads_in", "from_name": u["name"], "leads": part, "suppress": supp if i == 0 else []})))
    con.execute("DELETE FROM transfers WHERE user_id=?", (uid,))
    con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)",
                (iso(), "system", "user_work_shared", f"{u['email']}: {len(leads)} lead(s) shared among {len(senders)} person(s)"))
    if (u["wipe"] or "") == "pending:disconnect":     # erase-and-disconnect: their work is shared, the account stays
        pass
    elif u["wipe"] != "done" and has_laptop(con, uid):
        if not (u["wipe"] or "").startswith("pending"):
            request_wipe(con, uid, then="remove")      # erase their copy first; removal completes when it confirms
    else:
        complete_removal(con, uid)
    con.commit()
    return len(leads)


def has_laptop(con, uid):
    u = con.execute("SELECT last_seen FROM users WHERE id=?", (uid,)).fetchone()
    return bool(con.execute("SELECT 1 FROM devices WHERE user_id=? AND revoked=0", (uid,)).fetchone() or (u and u["last_seen"]))


def request_wipe(con, uid, then="remove"):
    """Tell the person's BSO to erase its leads, prospects, emails and saved passwords at its next check-in."""
    con.execute("UPDATE users SET wipe=? WHERE id=?", (f"pending:{then}", uid))
    con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (uid, json.dumps({"action": "wipe", "then": then})))
    con.commit()


def wiped(con, uid):
    u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return
    then = (u["wipe"] or "").split(":", 1)[-1]
    con.execute("UPDATE users SET wipe='done' WHERE id=?", (uid,))
    con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), "system", "laptop_erased", u["email"]))
    if then == "remove":
        complete_removal(con, uid)
    else:                                              # erase-and-disconnect: sign the laptops out
        con.execute("UPDATE users SET tv=COALESCE(tv,1)+1 WHERE id=?", (uid,))
        con.execute("UPDATE devices SET revoked=1 WHERE user_id=?", (uid,))
    con.commit()


def complete_removal(con, uid):
    u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u or u["status"] == "removed":
        return
    con.execute("UPDATE users SET status='removed', name=?, username=?, email=?, pw_hash=NULL, relay_pw=NULL, totp_secret=NULL, "
                "tv=COALESCE(tv,1)+1 WHERE id=?",
                (u["name"] + " (removed)", f"removed-{uid}-{u['username'] or u['email']}", f"removed-{uid}-{u['email']}", uid))
    con.execute("UPDATE devices SET revoked=1 WHERE user_id=?", (uid,))
    con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), "system", "user_removed", u["email"]))
    con.commit()
    try:
        st = _secret_store()
        for k in (f"SETUP_KEY_{uid}", f"SETUP_INBOXES_{uid}"):
            st.set(k, "")
    except Exception:
        pass


def remove_user(con, u, wait_for_leads=True):
    """Start removing someone: stop them sending, share their prospects out now, collect their leads at their next check-in."""
    moved = 0
    if u["role"] == "supervisor":
        others = [x for x in con.execute("SELECT * FROM users WHERE role='supervisor' AND id!=? AND status NOT IN ('disabled','removed','removing') "
                                         "ORDER BY id", (u["id"],)).fetchall()]
        others = [x for x in others if not is_away(x)] or others          # people on leave only if nobody else is there
        open_h = con.execute("SELECT * FROM handovers WHERE supervisor_id=? AND COALESCE(status,'new') NOT IN ('won','lost') ORDER BY at", (u["id"],)).fetchall()
        if others and open_h:
            load = {o["id"]: con.execute("SELECT COUNT(*) FROM handovers WHERE supervisor_id=? AND COALESCE(status,'new') NOT IN ('won','lost')",
                                         (o["id"],)).fetchone()[0] for o in others}
            by_id = {o["id"]: o for o in others}
            for oid, hs in share_out(list(by_id), open_h, load).items():
                for h in hs:
                    reassign_prospect(con, h, by_id[oid], u)
                    moved += 1
    laptop = has_laptop(con, u["id"])
    if wait_for_leads and u["role"] in ("member", "manager", "bot") and laptop:
        con.execute("UPDATE users SET status='removing' WHERE id=?", (u["id"],))
        con.execute("INSERT INTO notices(user_id, item) VALUES (?,?)", (u["id"], json.dumps({"action": "transfer_out"})))
        con.commit()
        return moved, True
    if wait_for_leads and laptop:
        con.execute("UPDATE users SET status='removing' WHERE id=?", (u["id"],))
        request_wipe(con, u["id"], then="remove")
        return moved, True
    finish_removal(con, u["id"])
    complete_removal(con, u["id"])
    return moved, False


def supervisor_config(con):
    sups = []
    for u in con.execute("SELECT * FROM users WHERE (role='supervisor' OR (role='owner' AND device_info LIKE '%\"acting_as\": \"supervisor\"%')) "
                         "AND status NOT IN ('disabled','removed','removing') ORDER BY id").fetchall():
        if is_away(u):
            continue
        n = con.execute("SELECT COUNT(*) FROM handovers WHERE supervisor_id=? AND at>=?", (u["id"], iso(now() - timedelta(days=30)))).fetchone()[0]
        sups.append({"id": u["username"] or u["email"], "name": u["name"], "email": u["email"], "count": n})
    last = con.execute("SELECT MAX(at) FROM handovers").fetchone()[0] or ""
    ppl = [{"key": relay_key_for(u["username"] or u["email"]), "name": u["name"], "role": u["role"]}
           for u in con.execute("SELECT * FROM users WHERE status NOT IN ('disabled','removed','removing') AND role!='bot' ORDER BY name").fetchall()]
    import hub_bot
    return dict({"action": "config", "local_id": None, "supervisors": sups, "people": ppl, "as_of": last, "security": security_cfg(con)},
                **hub_bot.config_extra(con))


SECURITY_DEFAULTS = {"idle_minutes": 15, "reauth_days": 7, "export_roles": "owner,manager", "big_list": 500, "canary_domain": "",
                     "allow_domains": "", "retention_months": 24}


def security_cfg(con):
    out = dict(SECURITY_DEFAULTS)
    for k in out:
        r = con.execute("SELECT value FROM meta WHERE key=?", (f"sec_{k}",)).fetchone()
        if r and r["value"] not in (None, ""):
            out[k] = int(r["value"]) if isinstance(SECURITY_DEFAULTS[k], int) else r["value"]
    pub = con.execute("SELECT value FROM meta WHERE key='vis_pub'").fetchone()
    if pub and pub["value"]:
        out["console_key"] = json.loads(pub["value"])
    out["own_domains"] = sorted(own_domains(con))
    return out


def own_domains(con):
    """Our company's domains: the people on the team and the sending inboxes."""
    ds = set()
    for r in con.execute("SELECT email FROM users WHERE status NOT IN ('removed')"):
        e = str(r["email"] or "")
        if "@" in e:
            ds.add(e.rsplit("@", 1)[-1].lower())
    return ds


def vis_keys():
    import vis as _vis
    return _vis.keypair(_secret_store(), meta_get, meta_set)


def decisions_for(con, uid, handovers=True):
    out = [{"local_id": d["local_id"], "action": "approve" if d["status"] == "approved" else "reject",
            "subject": d["subject"], "body": d["body"], "by": d["decided_by"]}
           for d in con.execute("SELECT * FROM drafts WHERE user_id=? AND status!='pending' AND delivered=0", (uid,)).fetchall()]
    out.append(supervisor_config(con))
    try:
        import vis as _vis
        import sealbox
        vk = _secret_store().load().get(f"VIS_KEY_{uid}")
        item = {"action": "vis_state", "local_id": None, "key_ok": bool(vk)}
        if vk:
            item["team_key"] = sealbox.seal(bytes.fromhex(vk), team_key().hex())
        out.append(item)
    except Exception:
        vk = None
    act = json.loads(_m_get(con, "strategy_active", "") or "{}")
    if act and vk:                                       # the approved strategy, sealed (it contains real example emails)
        out.append({"action": "strategy", "local_id": None, "version": act.get("version"),
                    "sealed": seal_team({k: act.get(k) for k in ("version", "arms", "explore", "examples", "lessons", "scoring")})})
    try:
        import hub_bot
        hub_bot.dnc_catch_up(con, uid)                   # the team's do-not-contact codes this person doesn't have yet
    except Exception as e:
        print("dnc:", e)
    for nt in con.execute("SELECT * FROM notices WHERE user_id=? AND delivered=0 ORDER BY id LIMIT 50", (uid,)).fetchall():
        it = json.loads(nt["item"])
        if vk and it.get("action") in ("leads_in", "comment", "removed", "transfer_out", "canary"):     # sealed for this person's BSO
            it = {"action": it["action"], "sealed": seal_team(it)}
        out.append(dict(it, local_id=None, handover_id=f"nt:{nt['id']}"))
    if not handovers:          # the Google relay (version 2+) delivers handovers to the supervisor itself
        return out
    for h in con.execute("SELECT h.*, u.name fromname FROM handovers h LEFT JOIN users u ON u.id=h.from_user_id "
                         "WHERE h.supervisor_id=? AND h.delivered=0 ORDER BY h.id LIMIT 25", (uid,)).fetchall():
        p = json.loads(h["payload"] or "{}")
        out.append({"action": "handover", "local_id": None, "handover_id": h["id"], "lead": p.get("lead") or {}, "label": h["label"],
                    "reply": p.get("reply", ""), "intro": p.get("intro", ""), "thread": (p.get("thread") or [])[:6],
                    "from": h["fromname"] or p.get("from_name", ""), "from_name": p.get("from_name", ""), "at": h["at"]})
    return out


@app.post("/api/v1/sync")
def api_sync():
    u, dev = api_user()
    if not u:
        return jsonify(error="unauthorized"), 401
    if dev and "approved" in dev and not dev["approved"]:
        db().execute("UPDATE devices SET last_seen=? WHERE id=?", (iso(), dev["id"]))
        db().commit()
        return jsonify(error="This laptop is waiting for your manager's approval (Master Console → Devices)."), 403
    j = request.get_json(silent=True) or {}
    con = db()
    n_ev = ingest(con, u["id"], j)
    con.execute("UPDATE users SET last_seen=?, app_version=? WHERE id=?", (iso(), str(j.get("version", ""))[:20], u["id"]))
    con.execute("UPDATE devices SET last_seen=? WHERE id=?", (iso(), dev["id"]))
    con.commit()
    return jsonify(policy=policy_for(u), accepted_events=n_ev, decisions=decisions_for(con, u["id"]))


# ---------------------------------------------------------------------------------------------- Google Drive team relay
# The master deploys relay/Code.gs once under their own Google account. Team laptops sign in and report through it;
# this console publishes users, limits and decisions to it and collects the reports, about once a minute.

RELAY_EVERY = 60
RELAY_LATEST = 5          # version of relay/Code.gs shipped with this BSO
_relay_lock = threading.Lock()


def _m_get(con, key, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _m_set(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
    con.commit()


def relay_key(create=False):
    store = _secret_store()
    k = store.load().get("HUB_RELAY_KEY", "")
    if not k and create:
        k = secrets.token_urlsafe(32)
        store.set("HUB_RELAY_KEY", k)
    return k


def relay_key_for(ident):
    return ident.replace(".", "~")


def ident_from_relay(key):
    return str(key or "").replace("~", ".")


def relay_doc(con):
    users = {}
    for u in con.execute("SELECT * FROM users WHERE status!='removed'").fetchall():
        if not u["relay_pw"]:
            continue
        rec = json.loads(u["relay_pw"])
        ident = u["username"] or u["email"]
        # relay keys can't contain dots (they're part of the sign-in token); the ID itself is matched via "email"
        users[relay_key_for(ident)] = dict(
            rec, name=u["name"], email=ident, mail=u["email"],
            role="supervisor" if u["role"] == "owner" and '"acting_as": "supervisor"' in (u["device_info"] or "") else u["role"],
            status="disabled" if u["status"] in ("disabled", "removing") else "active",
            tv=u["tv"] or 1, away_until=u["away_until"] or "", daily_cap=u["daily_cap"] or 0, weekly_target=u["weekly_target"] or 0, approval=u["approval"] or "member",
            decisions=decisions_for(con, u["id"], handovers=int(_m_get(con, "relay_version", "1") or 1) < 2))
    return {"org": _m_get(con, "org_name", "Your team"), "grace_hours": int(_m_get(con, "grace_hours", str(GRACE_HOURS)) or 0), "users": users}


def relay_cycle(con=None):
    """Collect reports from the relay, then publish users/limits/decisions. Returns (ok, message)."""
    own = con is None
    if own:
        con = sqlite3.connect(str(DB_PATH), timeout=30)
        con.row_factory = sqlite3.Row
    try:
        url, key = _m_get(con, "relay_url", ""), relay_key()
        if not (url and key):
            return False, "The Google Drive connection isn't set up yet."
        with _relay_lock:
            try:
                done, rounds, got = [], 0, 0
                while rounds < 10:
                    rounds += 1
                    res = relay.call(url, {"action": "admin_pull", "admin_key": key, "done": done})
                    done = []
                    if str(res.get("relay_version", "1")) != _m_get(con, "relay_version", "1"):
                        _m_set(con, "relay_version", str(res.get("relay_version", "1")))
                    for it in res.get("items") or []:
                        body = it.get("body") or {}
                        who = ident_from_relay(body.get("user"))
                        u = con.execute("SELECT id FROM users WHERE username=? OR email=?", (who, who)).fetchone()
                        if u:
                            ingest(con, u["id"], body.get("data") or {})
                            got += 1
                        done.append(it["id"])
                    con.commit()
                    for name, seen in (res.get("seen") or {}).items():
                        name = ident_from_relay(name)
                        at = str(seen.get("at", ""))[:19] + "Z"
                        con.execute("UPDATE users SET last_seen=?, app_version=COALESCE(NULLIF(?, ''), app_version), "
                                    "status=CASE WHEN status='invited' THEN 'active' ELSE status END "
                                    "WHERE (username=? OR email=?) AND (last_seen IS NULL OR last_seen<?)",
                                    (at, str(seen.get("version", ""))[:20], name, name, at))
                    if "failed_signins" in res:
                        _m_set(con, "relay_fails", json.dumps(res.get("failed_signins") or {})[:20000])
                        _m_set(con, "relay_locked", json.dumps(res.get("locked") or {})[:20000])
                    con.commit()
                    if not done:
                        break
                doc = relay_doc(con)
                h = sha(json.dumps(doc, sort_keys=True))
                last = float(_m_get(con, "relay_pub_at", "0") or 0)
                if h != _m_get(con, "relay_pub_hash") or time.time() - last > 600:
                    relay.call(url, {"action": "admin_publish", "admin_key": key, "doc": doc})
                    _m_set(con, "relay_pub_hash", h)
                    _m_set(con, "relay_pub_at", str(time.time()))
                _m_set(con, "relay_last_ok", iso())
                _m_set(con, "relay_last_error", "")
                return True, f"Synced with Google Drive ({got} report{'s' if got != 1 else ''} collected)."
            except relay.RelayError as e:
                msg = str(e)
                if e.code == 401:
                    msg = "Google Drive refused this console's key. Paste the latest script from this page into your Apps Script project and deploy a new version."
                _m_set(con, "relay_last_error", f"{iso()} {msg}")
                return False, msg
    finally:
        if own:
            con.close()


def _relay_loop():
    last_watch = 0
    while True:
        time.sleep(RELAY_EVERY)
        try:
            con = sqlite3.connect(str(DB_PATH), timeout=30)
            con.row_factory = sqlite3.Row
            try:
                ensure_canaries(con)
                try:
                    archive_box_cycle(con)
                except Exception as e:
                    print("archive mailbox:", e)
                try:
                    strategy_cycle(con)
                except Exception as e:
                    print("strategy:", e)
                if time.time() - float(_m_get(con, "purge_ts", "0") or 0) > 86400:
                    import vis as _vis
                    _vis.purge(con, security_cfg(con)["retention_months"])
                    _m_set(con, "purge_ts", str(time.time()))
                if time.time() - last_watch > 1800:
                    last_watch = time.time()
                    canary_watch(con)
                try:
                    import hub_bot
                    hub_bot.watch(con)
                except Exception as e:
                    print("bot watch:", e)
            finally:
                con.close()
        except Exception as e:
            print("security:", e)
        try:
            relay_cycle()
        except Exception as e:      # never let the background sync die
            print("relay:", e)


@app.route("/relay/Code.gs")
@need("owner")
def relay_script():
    resp = app.response_class(relay.script_text(relay_key(create=True)), mimetype="text/plain")
    resp.headers["Content-Disposition"] = "attachment; filename=Code.gs"
    return resp


@app.get("/hub.js")
def hub_js():
    js = ("document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){"
          "var t=document.getElementById(b.dataset.copy);t.select();try{navigator.clipboard.writeText(t.value)}catch(e){document.execCommand('copy')}"
          "b.textContent='Copied';setTimeout(function(){b.textContent='Copy script'},2000)})});")
    return app.response_class(js, mimetype="text/javascript")


@app.get("/api/v1/ping")
def api_ping():
    return jsonify(ok=True, product="BSO hub")


# ---------------------------------------------------------------------------------------------- templates

CSS = """
:root{--bg:#f3f3f3;--card:#fff;--ink:#181818;--muted:#5c5c5c;--line:#dddbda;--brand:#0176d3;--ok:#2e844a;--warn:#dd7a01;--bad:#ba0517}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
.pgbtn{display:flex;align-items:center;gap:6px;max-width:240px;padding:5px 12px;border:1px solid var(--line);border-radius:16px;background:#fff;color:var(--ink);text-decoration:none;font-size:13px;white-space:nowrap}.pgbtn span{overflow:hidden;text-overflow:ellipsis}.pgbtn:hover{background:#f3f3f3}
.top{background:#1B1F24;color:#fff;display:flex;align-items:center;gap:14px;padding:10px 20px}.top b{font-size:16px}.top .sp{flex:1}.top small{color:#b9c3c9}
.nav{background:#fff;border-bottom:1px solid var(--line);padding:0 20px;display:flex;gap:4px;overflow-x:auto}.nav a{padding:12px 14px;color:var(--ink);border-bottom:3px solid transparent;white-space:nowrap}
.nav a.on{border-color:var(--brand);font-weight:600}.wrap{max-width:1200px;margin:20px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;margin-bottom:16px}.card-h{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}.card-h h2{margin:0;font-size:15px}.card-b{padding:16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}.stat{background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px 16px}.stat small{color:var(--muted)}.stat div{font-size:26px;font-weight:300}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}.r{text-align:right}.muted{color:var(--muted)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#eef;color:#444}.b-ok{background:#d6f3e0;color:#194e2c}.b-bad{background:#fde1e1;color:#7b1018}.b-warn{background:#ffeccc;color:#704100}.b-off{background:#e5e5e5}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#bbb;margin-right:6px}.dot.on{background:#2e844a}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 3px}input,select,textarea{width:100%;padding:8px 10px;border:1px solid #c9c7c5;border-radius:4px;font:inherit;background:#fff}textarea{min-height:200px;font-family:ui-monospace,Consolas,monospace}
.btn{display:inline-block;padding:8px 14px;border:1px solid #c9c7c5;border-radius:4px;background:#fff;color:var(--brand);cursor:pointer;font:inherit}.btn.brand{background:var(--brand);border-color:var(--brand);color:#fff}.btn.danger{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}.bar{height:8px;background:#e5e5e5;border-radius:4px;overflow:hidden;min-width:80px}.bar i{display:block;height:100%;background:var(--brand)}
.note{background:#fff8e6;border:1px solid #f0d27a;border-radius:6px;padding:10px 14px;margin-bottom:14px}.err{background:#fde1e1;border:1px solid #e9a0a0;border-radius:6px;padding:10px 14px;margin-bottom:14px}
.code{font:600 15px ui-monospace,Consolas,monospace;background:#f3f3f3;padding:8px 10px;border-radius:4px;word-break:break-all;user-select:all}
.center{max-width:420px;margin:60px auto}.logo{display:flex;gap:12px;align-items:center;margin-bottom:18px}
"""

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}Master console{% endblock %} · BSO</title><style>""" + CSS + """{{ logo_css|safe }}</style></head><body>
{% if me %}<div class="top">{{ logo(30, tile=True, uid='tp', weight='b', label='BSO')|safe }}<b>BSO Master console</b><small>{{ org }}</small><span class="sp"></span>
<small>{{ me.name }} · {{ me.role }}</small><a href="{{ url_for('twofa') }}" style="color:#b9c3c9;font-size:12px">2-step sign-in</a><form method="post" action="{{ url_for('logout') }}" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" style="padding:4px 10px">Sign out</button></form></div>
<div class="nav">{% for k, l, ep in [('dashboard','Overview','dashboard'),('conversations','Conversations','conversations'),('insights','Insights','insights'),('users','Users','users'),('approvals','Approvals','approvals'),('board','Prospect board','board'),('handovers','Handovers','handovers_page'),('activity','Activity','activity'),('bot','BSO Bot','bot_page'),('clients','Clients','scope_list')] + ([('bso_setup','BSO setup','bso_setup'),('devices','Devices','devices_page'),('security','Security','security_page'),('audit','Audit log','audit_page'),('settings','Settings','hub_settings')] if me.role == 'owner' else []) %}<a class="{{ 'on' if tab == k }}" href="{{ url_for(ep) }}">{{ l }}</a>{% endfor %}</div>{% endif %}
<div class="wrap">{% if me %}<nav class="pgnav" style="display:flex;align-items:center;gap:8px;margin:0 0 12px;min-height:32px" aria-label="Page history"><a id="nav-back" class="pgbtn" href="#" title="Back (Alt + Left arrow)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" style="fill:currentColor;flex:none"><path d="M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z"/></svg><span></span></a><a id="nav-fwd" class="pgbtn" href="#" title="Forward (Alt + Right arrow)" style="display:none"><span></span><svg width="14" height="14" viewBox="0 0 24 24" style="fill:currentColor;flex:none"><path d="M4 11h12.2l-5.6-5.6L12 4l8 8-8 8-1.4-1.4 5.6-5.6H4z"/></svg></a></nav>{% endif %}{% if request.args.get('err') %}<div class="err">{{ request.args.get('err') }}</div>{% endif %}{% if request.args.get('ok') %}<div class="note">{{ request.args.get('ok') }}</div>{% endif %}{% block body %}{% endblock %}</div><script src="/hub.js"></script><script>(function(){ /* back / forward through the pages opened in this window, each labelled with the page's name */
 var name=(document.title||'').split(' · ')[0].trim()||'Page', here=location.pathname+location.search, t={l:[],i:-1};
 try{ t=JSON.parse(sessionStorage.getItem('bso_hist')||'') || t; }catch(e){}
 var l=t.l, i=t.i;
 if(i>=0 && l[i] && l[i].u===here){ }
 else if(i>0 && l[i-1].u===here){ i--; }
 else if(l[i+1] && l[i+1].u===here){ i++; }
 else { l=l.slice(0,i+1); l.push({u:here,n:name}); if(l.length>40){ l=l.slice(-40); } i=l.length-1; }
 l[i].n=name;
 try{ sessionStorage.setItem('bso_hist', JSON.stringify({l:l,i:i})); }catch(e){}
 function set(id, it, go){ var a=document.getElementById(id); if(!a) return; if(!it){ a.style.display='none'; return; }
  a.style.display='flex'; a.querySelector('span').textContent=it.n; a.title=(go<0?'Back to ':'Forward to ')+it.n; a.href=it.u;
  a.addEventListener('click',function(e){ e.preventDefault(); history.go(go); setTimeout(function(){ if(location.pathname+location.search===here) location.href=it.u; },400); }); }
 set('nav-back', i>0?l[i-1]:null, -1); set('nav-fwd', l[i+1]||null, 1);
})();</script></body></html>"""

T = {}
T["base.html"] = BASE
T["setup.html"] = """{% extends 'base.html' %}{% block title %}Set up{% endblock %}{% block body %}<div class="center card"><div class="card-b">
<div class="logo">{{ logo(56, tile=True, uid='su', animated=True)|safe }}<div><h2 style="margin:0">Set up your master console</h2><span class="muted">One time only</span></div></div>
{% if err %}<div class="err">{{ err }}</div>{% endif %}
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}">
{% if not local %}<label>Setup key (saved in <code>setup-key.txt</code> in the hub's data folder on the hub computer; not needed when you open this page on that computer)</label><input name="setup_key" autocomplete="off" required>{% endif %}
<label>Team or company name</label><input name="org" placeholder="e.g. NeoSOFT outreach team">
<label>Your name</label><input name="name" required><label>Your email (your sign-in, and where password-reset links go)</label><input name="email" type="email" required value="{{ owner_email }}">
<label>Password (at least 10 characters)</label><input name="password" type="password" autocomplete="new-password" required>
<label>Password again</label><input name="password2" type="password" autocomplete="new-password" required>
<p><button class="btn brand">Create the owner account</button></p></form></div></div>{% endblock %}"""
T["login.html"] = """{% extends 'base.html' %}{% block title %}Sign in{% endblock %}{% block body %}<div class="center card"><div class="card-b">
<div class="logo">{{ logo(56, tile=True, uid='lg', animated=True)|safe }}<div><h2 style="margin:0">BSO Master console</h2><span class="muted">BlacSwanken Outreach</span></div></div>
{% if err %}<div class="err">{{ err }}</div>{% endif %}{% if request.args.get('reset') %}<div class="note">Password changed. Sign in with the new one.</div>{% endif %}
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><label>ID or email</label><input name="email" required autofocus autocomplete="username">
<label>Password</label><input name="password" type="password" required><p><button class="btn brand">Sign in</button> <a href="{{ url_for('forgot') }}" style="margin-left:10px">Forgot password?</a></p></form></div></div>{% endblock %}"""
T["forgot.html"] = """{% extends 'base.html' %}{% block title %}Forgot password{% endblock %}{% block body %}<div class="center card"><div class="card-b">
<h2 style="margin-top:0">Reset your password</h2>
{% if done %}<div class="note">If that ID or email belongs to a master or manager account and email is set up, a reset link is on its way. It works once and expires in 30 minutes.</div><a href="{{ url_for('login') }}">Back to sign in</a>
{% else %}<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><label>Your email or ID</label><input name="email" required autofocus><p><button class="btn brand">Email me a reset link</button> <a href="{{ url_for('login') }}" style="margin-left:10px">Cancel</a></p></form>{% endif %}</div></div>{% endblock %}"""
T["reset.html"] = """{% extends 'base.html' %}{% block title %}Choose a new password{% endblock %}{% block body %}<div class="center card"><div class="card-b">
{% if bad %}<h2 style="margin-top:0">This link has expired</h2><p>Reset links work once and last 30 minutes.</p><a class="btn brand" href="{{ url_for('forgot') }}">Get a new link</a>
{% else %}<h2 style="margin-top:0">Choose a new password</h2>{% if err %}<div class="err">{{ err }}</div>{% endif %}
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><label>New password (at least 10 characters)</label><input name="password" type="password" autocomplete="new-password" required autofocus>
<label>Again</label><input name="password2" type="password" autocomplete="new-password" required><p><button class="btn brand">Save password</button></p></form>{% endif %}</div></div>{% endblock %}"""
T["login_code.html"] = """{% extends 'base.html' %}{% block title %}2-step sign-in{% endblock %}{% block body %}<div class="center card"><div class="card-b">
<h2 style="margin-top:0">Enter your code</h2><p class="muted">Open your authenticator app and type the 6-digit code for BSO Console, or one of your recovery codes.</p>
{% if err %}<div class="err">{{ err }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}">
<input name="code" inputmode="numeric" autocomplete="one-time-code" autofocus required placeholder="123456" style="font-size:22px;letter-spacing:4px"><p><button class="btn brand">Continue</button> <a href="{{ url_for('login') }}" style="margin-left:10px">Cancel</a></p></form></div></div>{% endblock %}"""
T["twofa.html"] = """{% extends 'base.html' %}{% block title %}2-step sign-in{% endblock %}{% block body %}
<p><a href="{{ url_for('hub_settings') if me.role == 'owner' else url_for('dashboard') }}">← Back</a></p>
<div class="card" style="max-width:640px"><div class="card-h"><h2>2-step sign-in</h2>{% if on %}<span class="badge b-ok">on</span>{% else %}<span class="badge b-warn">off</span>{% endif %}</div><div class="card-b">
{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
{% if new_codes %}<div class="note"><b>2-step sign-in is on.</b> Save these recovery codes somewhere safe (a password manager or a printout). Each one works once if you lose your phone. They're shown only now.
<div class="code" style="margin-top:8px">{% for c in new_codes %}{{ c }}<br>{% endfor %}</div></div>
{% elif on %}<p style="margin-top:0">Signing in asks for your password and then the 6-digit code from your authenticator app.</p>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="off"><label>To switch it off, enter a current code</label><input name="code" inputmode="numeric" style="max-width:200px"><p><button class="btn danger">Switch off</button></p></form>
{% elif pending %}<ol style="line-height:1.8;margin-top:0"><li>On your phone, open <b>Google Authenticator</b> or <b>Microsoft Authenticator</b> → <b>+</b> → <b>Enter a setup key</b>.</li>
<li>Account name: <b>BSO Console</b>. Key: <div class="code" style="margin:6px 0">{{ pending }}</div>Type: time-based. (Some apps can open this link instead: <a href="{{ uri }}">{{ uri[:46] }}…</a>)</li>
<li>Type the 6-digit code the app now shows:</li></ol>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="confirm"><input name="code" inputmode="numeric" autocomplete="one-time-code" required style="max-width:200px;font-size:20px;letter-spacing:3px" autofocus>
<p><button class="btn brand">Confirm and switch on</button></p></form><p class="muted">Nothing changes until the code matches.</p>
{% else %}<p style="margin-top:0">Protect the console with a code from your phone as well as your password. Anyone who learns your password still can't get in.</p>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn brand" name="action" value="start">Set up 2-step sign-in</button></form>{% endif %}
</div></div>{% endblock %}"""
T["dashboard.html"] = """{% extends 'base.html' %}{% block body %}
{% if open_incidents %}<div class="err"><b>{{ open_incidents }} incident(s) waiting for review</b>: client emails copied outside, blocked or deleted. <a href="{{ url_for('security_page') }}#incidents">Review them →</a></div>{% endif %}
{% if hot %}<div class="card" style="border-left:4px solid #ba0517"><div class="card-h"><h2>🔥 Hot and warm leads waiting for an answer</h2></div><table><tr><th>Lead</th><th>Supervisor</th><th>Handed over</th><th>Answer by</th><th></th></tr>
{% for x in hot %}<tr><td>{{ icons[x.temp] }} <a href="{{ url_for('conversation', email=x.h.lead_email) }}">{{ x.h.lead_name or x.h.lead_email }}</a> <span class="muted">{{ x.h.company or '' }}</span><br><small class="muted">{{ x.reason }}</small></td>
<td>{{ x.h.sup_name or '—' }}</td><td class="muted">{{ x.h.at|dt }}</td><td>{{ x.deadline.strftime('%a %d %b %H:%M') }}</td>
<td>{% if x.late %}<span class="badge b-bad">late: tell {{ (x.h.sup_name or '').split(' ')[0] }}</span>{% elif x.overdue %}<span class="badge b-warn">overdue</span>{% else %}<span class="badge">in time</span>{% endif %}</td></tr>{% endfor %}</table></div>{% endif %}
{% if watch %}<div class="card" style="border-left:4px solid #dd7a01"><div class="card-h"><h2>Supervisors BSO can't fully see</h2></div><div class="card-b">{% for w in watch %}<div><b>{{ w.u.name }}</b>: {{ w.warn|join('; ') }}</div>{% endfor %}</div></div>{% endif %}
<div class="stats"><div class="stat"><small>Sent (7 days)</small><div>{{ totals.sent }}</div></div><div class="stat"><small>Replies (7 days)</small><div>{{ totals.replies }}</div></div>
<div class="stat"><small>Interested replies</small><div>{{ totals.interested }}</div></div><div class="stat"><small>Drafts awaiting approval</small><div><a href="{{ url_for('approvals') }}">{{ totals.pending }}</a></div></div>
<div class="stat"><small>Laptops online now</small><div>{{ totals.online }}</div></div>
<div class="stat"><small>Prospects handed over (7 days)</small><div><a href="{{ url_for('handovers_page') }}">{{ totals.handed }}</a></div></div></div>
{% for b in bots %}<div class="card" style="border-left:4px solid {{ '#ba0517' if b.warn else '#2e844a' }}"><div class="card-h"><h2>🤖 {{ b.u.name }}</h2>
<span class="dot {{ 'on' if b.online }}"></span>{{ 'Online' if b.online else 'Offline' }}<span class="muted">· last run {{ b.last_run }}{% if b.st.autorun %} · runs every {{ b.st.every_minutes }} min{% endif %}{% if b.st.review %} · your approval required{% endif %}</span></div>
<div class="card-b"><div class="stats" style="margin-bottom:{{ '10px' if b.warn else '0' }}"><div class="stat"><small>Sent today</small><div>{{ b.st.sent_today or 0 }}</div></div><div class="stat"><small>Replies today</small><div>{{ b.st.replies_today or 0 }}</div></div>
<div class="stat"><small>Handed over today</small><div>{{ b.st.handovers_today or 0 }}</div></div><div class="stat"><small>Leads not yet emailed</small><div>{{ b.st.leads_not_started if b.st.leads_not_started is not none else '–' }}</div></div>
<div class="stat"><small>Days of leads left</small><div>{{ b.st.days_of_leads if b.st.days_of_leads is not none else '–' }}</div></div><div class="stat"><small>Errors today</small><div>{{ b.st.errors_today or 0 }}</div></div></div>
{% for w in b.warn %}<div class="err" style="margin-bottom:6px">⚠ {{ w }}</div>{% endfor %}</div></div>{% endfor %}
<div class="card"><div class="card-h"><h2>Emails sent per day, whole team</h2></div><div class="card-b">
<svg viewBox="0 0 {{ series|length * 40 }} 120" width="100%" height="140" role="img" aria-label="Emails sent per day">{% for d, n in series %}<g><rect x="{{ loop.index0 * 40 + 6 }}" y="{{ 96 - (n * 90 // mx) }}" width="28" height="{{ (n * 90 // mx) or 1 }}" rx="3" fill="#0176d3"/>
<text x="{{ loop.index0 * 40 + 20 }}" y="{{ 92 - (n * 90 // mx) }}" font-size="10" text-anchor="middle" fill="#444">{{ n if n }}</text><text x="{{ loop.index0 * 40 + 20 }}" y="112" font-size="9" text-anchor="middle" fill="#777">{{ d[:6] }}</text></g>{% endfor %}</svg></div></div>
<div class="card"><div class="card-h"><h2>Team, last 7 days</h2><span class="muted">A laptop reports each time it syncs, about every 2 minutes while BSO is open.</span></div>
<table><tr><th>User</th><th>Status</th><th class="r">Today</th><th class="r">Sent</th><th class="r">Replies</th><th class="r">Reply rate</th><th class="r">Interested</th><th class="r">Bounces</th><th>Weekly target</th><th class="r">Awaiting approval</th><th>Last seen</th></tr>
{% for s in stats %}<tr><td><a href="{{ url_for('user_detail', uid=s.u.id) }}"><b>{{ s.u.name }}</b></a><br><span class="muted">{{ s.u.role }}</span></td>
<td>{% if s.u.status == 'disabled' %}<span class="badge b-bad">Paused</span>{% elif s.u.status == 'invited' %}<span class="badge b-warn">Not signed in yet</span>{% else %}<span class="dot {{ 'on' if s.online }}"></span>{{ 'Online' if s.online else 'Offline' }}{% endif %}</td>
<td class="r">{{ s.today }}{% if s.u.daily_cap %} / {{ s.u.daily_cap }}{% endif %}</td><td class="r">{{ s.sent }}</td><td class="r">{{ s.replies }}</td><td class="r">{{ s.rate }}</td><td class="r">{{ s.interested }}</td><td class="r">{{ s.bounces }}</td>
<td>{% if s.pct is not none %}<div class="bar" title="{{ s.week_sent }} of {{ s.u.weekly_target }}"><i style="width:{{ s.pct }}%"></i></div><small class="muted">{{ s.week_sent }} / {{ s.u.weekly_target }}</small>{% else %}<span class="muted">none set</span>{% endif %}</td>
<td class="r">{% if s.pending %}<a href="{{ url_for('approvals') }}">{{ s.pending }}</a>{% else %}0{% endif %}</td><td class="muted">{{ s.u.last_seen|ago }}</td></tr>
{% else %}<tr><td colspan="11" class="muted">No users yet. Add one on the Users page.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["_invite.html"] = """{% if new_creds %}<div class="note"><b>Sign-in details for {{ new_creds.name }}</b> (shown only once. Give them to the person privately.)<br>
<div class="code" style="margin:8px 0">ID: {{ new_creds.username }}<br>Password: {{ new_creds.password }}</div>
<span class="muted">They install BSO, open it and sign in with these. {% if new_creds.url %}Hub address (if BSO asks): <b>{{ new_creds.url }}</b>.{% endif %}</span></div>{% endif %}"""
T["users.html"] = """{% extends 'base.html' %}{% block title %}Users{% endblock %}{% block body %}{% include '_invite.html' %}
<div class="card"><div class="card-h"><h2>Users</h2></div><table><tr><th>Name</th><th>Role</th><th>Status</th><th>Daily cap</th><th>Weekly target</th><th>Approval</th><th class="r">Sent (30 d)</th><th class="r">Replies</th><th>Last seen</th></tr>
{% for s in stats %}{% set u = s.u %}<tr><td><a href="{{ url_for('user_detail', uid=u.id) }}"><b>{{ u.name }}</b></a><br><span class="muted">ID: {{ u.username or u.email }}</span></td><td>{{ u.role }}</td>
<td><span class="badge {{ 'b-ok' if u.status == 'active' else 'b-bad' if u.status == 'disabled' else 'b-warn' }}">{{ u.status }}</span></td><td>{{ u.daily_cap or 'no cap' }}</td><td>{{ u.weekly_target or '–' }}</td>
<td>{{ 'Master approves' if u.approval == 'master' else 'Self' }}</td><td class="r">{{ s.sent }}</td><td class="r">{{ s.replies }}</td><td class="muted">{{ u.last_seen|ago }}</td></tr>{% endfor %}</table></div>
{% if me.role == 'owner' %}<div class="card"><div class="card-h"><h2>Add a user</h2></div><div class="card-b"><form method="post" action="{{ url_for('user_new') }}"><input type="hidden" name="_csrf" value="{{ csrf }}">
<div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Email</label><input name="email" type="email" required></div>
<div><label>Sign-in ID (blank = part of email before @)</label><input name="username" autocomplete="off"></div>
<div><label>Role</label><select name="role"><option value="supervisor">Supervisor: works the prospects the bot hands over</option><option value="manager">Manager: a supervisor who can also use this console (no owner pages)</option><option value="bot">BSO Bot: runs all cold outreach on an always-on computer; stays signed in</option></select></div>
<div><label>Password (10+ characters; blank = generate one)</label><input name="password" autocomplete="off"></div>
<div><label>Daily send cap (0 = no cap)</label><input name="daily_cap" type="number" min="0" value="0"></div><div><label>Weekly target (emails, 0 = none)</label><input name="weekly_target" type="number" min="0" value="0"></div>
<div><label>Who approves their emails</label><select name="approval"><option value="member">They approve their own</option><option value="master">The master approves every email</option></select></div></div>
<label>Their sending inboxes (members and the BSO Bot): one per line as <b>email, name prospects see, password</b>. Optional, you can add them later on their page.</label>
<textarea name="inboxes" style="min-height:90px" placeholder="cole.reed@getneosofttech.com, Cole Reed, mailbox-password&#10;emma.hart@getneosofttech.com, Emma Hart, mailbox-password" autocomplete="off"></textarea>
<p class="muted" style="margin:4px 0 0">Their BSO sets itself up at first sign-in: these inboxes plus the mail server, AI key and schedule from <a href="{{ url_for('bso_setup') }}">BSO setup</a>. Everything is encrypted with their sign-in password on the way.</p>
<p><button class="btn brand">Create user</button></p></form></div></div>{% endif %}{% endblock %}"""
T["user.html"] = """{% extends 'base.html' %}{% block title %}{{ u.name }}{% endblock %}{% block body %}{% include '_invite.html' %}
<p><a href="{{ url_for('users') }}">← All users</a></p>
<div class="stats"><div class="stat"><small>Sent (30 days)</small><div>{{ st.sent }}</div></div><div class="stat"><small>Replies</small><div>{{ st.replies }}</div></div><div class="stat"><small>Reply rate</small><div>{{ st.rate }}</div></div>
<div class="stat"><small>Interested</small><div>{{ st.interested }}</div></div><div class="stat"><small>App version</small><div style="font-size:18px">{{ u.app_version or '–' }}</div></div></div>
{% if me.role == 'owner' %}<div class="card"><div class="card-h"><h2>{{ u.name }} <span class="badge {{ 'b-ok' if u.status == 'active' else 'b-bad' if u.status == 'disabled' else 'b-warn' }}">{{ u.status }}</span></h2></div><div class="card-b">
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="save"><div class="grid">
<div><label>Name</label><input name="name" value="{{ u.name }}"></div><div><label>Email</label><input value="{{ u.email }}" disabled></div><div><label>Sign-in ID</label><input value="{{ u.username or u.email }}" disabled></div>
<div><label>Role</label><select name="role" {{ 'disabled' if u.role == 'owner' }}>{% for r in ['manager','supervisor','bot'] %}<option {{ 'selected' if u.role == r }}>{{ r }}</option>{% endfor %}{% if u.role == 'owner' %}<option selected>owner</option>{% endif %}</select></div>
<div><label>Daily send cap (0 = no cap)</label><input name="daily_cap" type="number" min="0" value="{{ u.daily_cap or 0 }}"></div><div><label>Weekly target</label><input name="weekly_target" type="number" min="0" value="{{ u.weekly_target or 0 }}"></div>
{% if u.role == 'supervisor' %}<div><label>Away until (skipped for new prospects; empty = available)</label><input name="away_until" type="date" value="{{ u.away_until or '' }}"></div>{% endif %}
<div><label>Who approves their emails</label><select name="approval"><option value="member" {{ 'selected' if u.approval != 'master' }}>They approve their own</option><option value="master" {{ 'selected' if u.approval == 'master' }}>The master approves every email</option></select></div>
</div>
<label>Private note</label><input name="note" value="{{ u.note or '' }}"><p><button class="btn brand">Save</button></p></form>
{% if u.role in ('member', 'manager', 'bot') %}<div class="card" style="margin:14px 0"><div class="card-h"><h2>BSO setup for {{ u.name }}</h2>
{% if u.setup_applied and u.setup_applied == u.setup_sent %}<span class="badge b-ok">applied on their BSO</span>{% elif u.setup_sent %}<span class="badge b-warn">sent, waiting for their BSO</span>{% else %}<span class="badge b-off">not sent</span>{% endif %}</div><div class="card-b">
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="setup">
<label>Sending inboxes: <b>email, name prospects see, password</b> per line ("keep" keeps the saved password)</label><textarea name="inboxes" style="min-height:120px" autocomplete="off">{{ inbox_lines }}</textarea>
<p class="muted" style="margin:4px 0">Mail server {{ t.smtp_host or '(not set)' }} · {{ t.daily_limit }} emails a day per inbox{{ ', warming up' if t.ramp }} · AI key {{ 'included' if t.anthropic_key else 'not set' }} · runs every {{ t.autorun_minutes }} min. Change these for everyone in <a href="{{ url_for('bso_setup') }}">BSO setup</a>.</p>
{% if not has_key %}<label>Their current BSO password (needed once, to encrypt the setup for them; not stored)</label><input name="password" type="password" autocomplete="off" style="max-width:300px">{% endif %}
<p><button class="btn brand">Save and send to their BSO</button></p></form></div></div>{% endif %}
<form method="post" style="display:inline-flex;gap:6px;align-items:center" onsubmit="return confirm('Set a new password? Their laptops are signed out and must sign in again.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><input name="password" placeholder="new password (blank = generate)" autocomplete="off" style="width:240px"><button class="btn" name="action" value="resetpw">Reset password</button></form>
{% if u.totp_secret %}<form method="post" style="display:inline" onsubmit="return confirm('Switch off this person\\'s 2-step sign-in? They set it up again next time.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" name="action" value="reset2fa">Reset 2-step sign-in</button></form>{% endif %}
<form method="post" style="display:inline" onsubmit="return confirm('At its next check-in this person\\'s BSO hands its leads over (shared equally among the others), then erases its data (leads, prospects, emails, saved passwords) and signs out. The account stays. Continue?')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger" name="action" value="wipe_revoke">Erase laptop and disconnect</button></form>
{% if u.wipe and u.wipe.startswith('pending') %}<span class="badge b-warn">erase requested: waits for their BSO to check in</span>{% elif u.wipe == 'done' %}<span class="badge b-ok">laptop erased</span>{% endif %}
<form method="post" style="display:inline" onsubmit="return confirm('Disconnect all of this user\\'s laptops? They must sign in again.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger" name="action" value="revoke">Disconnect laptops</button></form>
{% if u.role != 'owner' %}<form method="post" style="display:inline" onsubmit="return confirm('Remove {{ u.name }} from the team? Their sign-in stops now; their prospects and leads are shared equally among the people who remain. This can\'t be undone.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger" name="action" value="remove">Remove user…</button></form>
{% if u.status == 'removing' %}<div class="note" style="margin-top:10px">Being removed: waiting for their BSO to check in and hand over its leads. If that laptop won't come back online:
<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger" name="action" value="remove_now" onclick="return confirm('Remove now, without the leads on their laptop?')">Remove now without their leads</button></form></div>{% endif %}
<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}">{% if u.status == 'disabled' %}<button class="btn" name="action" value="enable">Resume account</button>{% else %}<button class="btn danger" name="action" value="disable" onclick="return confirm('Pause this account? Their BSO stops sending at the next check-in.')">Pause account</button>{% endif %}</form>{% endif %}</div></div>{% endif %}
<div class="card"><div class="card-h"><h2>Laptops</h2></div><table><tr><th>Name</th><th>Joined</th><th>Last seen</th><th></th></tr>{% for d in devices %}<tr><td>{{ d.name }}</td><td>{{ d.created_at|dt }}</td><td>{{ d.last_seen|ago }}</td><td>{{ 'disconnected' if d.revoked }}</td></tr>{% else %}<tr><td colspan="4" class="muted">No laptop has joined yet.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Recent activity</h2></div><table><tr><th>When</th><th>Event</th><th>Lead</th><th>Detail</th></tr>{% for e in events %}<tr><td class="muted">{{ e.at|dt }}</td><td>{{ e.type }}</td><td>{{ e.lead_email }}{% if e.company %}<br><span class="muted">{{ e.company }}</span>{% endif %}</td><td>{{ e.detail }}</td></tr>{% else %}<tr><td colspan="4" class="muted">Nothing reported yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["approvals.html"] = """{% extends 'base.html' %}{% block title %}Approvals{% endblock %}{% block body %}
<div class="note">These are emails from users whose approval is set to <b>The master approves every email</b>. An approved email is sent from the user's own inbox the next time their BSO checks in and runs.</div>
{% for d in pend %}<div class="card" id="d{{ d.id }}"><div class="card-h"><h2>{{ d.uname }} → {{ d.lead_email }}</h2><span class="muted">{{ d.company }} · {{ 'First email' if (d.step or 1) == 1 else 'Follow-up ' ~ (d.step - 1) }} · from {{ d.sender }}</span></div><div class="card-b">
<form method="post" action="{{ url_for('approval_action', did=d.id) }}"><input type="hidden" name="_csrf" value="{{ csrf }}"><label>Subject</label><input name="subject" value="{{ d.subject }}" {{ 'readonly' if (d.step or 1) > 1 }}><label>Body</label><textarea name="body">{{ d.body }}</textarea>
<p><button class="btn brand" name="action" value="approve">✓ Approve</button> <button class="btn" name="action" value="save">Save edits</button> <button class="btn danger" name="action" value="reject">Reject</button></p></form></div></div>
{% else %}<div class="card"><div class="card-b muted">Nothing is waiting for approval.</div></div>{% endfor %}
{% if done %}<div class="card"><div class="card-h"><h2>Recently decided</h2></div><table>{% for d in done %}<tr><td>{{ d.uname }}</td><td>{{ d.lead_email }}</td><td><span class="badge {{ 'b-ok' if d.status == 'approved' else 'b-bad' }}">{{ d.status }}</span></td><td class="muted">{{ d.decided_by }} · {{ d.decided_at|dt }}</td><td class="muted">{{ 'delivered' if d.delivered else 'waiting for their laptop' }}</td></tr>{% endfor %}</table></div>{% endif %}{% endblock %}"""
T["handovers.html"] = """{% extends 'base.html' %}{% block title %}Handovers{% endblock %}{% block body %}
<div class="note">Interested, meeting-request and question replies are handed to the <b>Lead Gen Supervisors</b> in turn (add one under Users with the role <i>Lead Gen Supervisor</i>). The original inbox sends a short introduction with the supervisor in CC, the supervisor gets a briefing email, and the prospect appears in their BSO under <b>My prospects</b>.</div>
<div class="card"><div class="card-h"><h2>Supervisors</h2></div><table><tr><th>Supervisor</th><th class="r">Handed over</th><th class="r">Not yet contacted</th><th class="r">Meeting / proposal / won</th><th></th></tr>
{% for s in sups %}<tr><td><b>{{ s.name }}</b>{% if s.away_until and s.away_until >= today %} <span class="badge b-warn">away until {{ s.away_until }}</span>{% endif %}{% if s.status == 'disabled' %} <span class="badge b-bad">paused: gets no new prospects</span>{% endif %}</td><td class="r">{{ s.n }}</td><td class="r">{{ s.open_n }}</td><td class="r">{{ s.good }}</td><td><a href="{{ url_for('handovers_page', sup=s.id) }}">show</a></td></tr>
{% else %}<tr><td colspan="5" class="muted">No supervisors yet. Add a user with the role <b>Lead Gen Supervisor</b>. Until then, interested replies stay with the person who received them.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Handed-over prospects{% if sid %} <a href="{{ url_for('handovers_page') }}" style="font-weight:400;font-size:13px">show all</a>{% endif %}</h2></div>
<table><tr><th>When</th><th>Prospect</th><th>Reply</th><th>From</th><th>Supervisor</th><th>Stage</th><th class="r">Hours to first update</th></tr>
{% for h in items %}<tr><td class="muted">{{ h.at|dt }}</td><td><a href="{{ url_for('handover_detail', hid=h.id) }}"><b>{{ h.lead_name }}</b></a><br><span class="muted">{{ h.title }}{{ ', ' if h.title and h.company }}{{ h.company }}<br>{{ h.lead_email }}</span></td>
<td>{{ (h.label or '').replace('_', ' ') }}</td><td>{{ h.fromname or '–' }}</td><td>{{ h.supname or '–' }}{% if not h.delivered %}<br><span class="muted">waiting for their BSO</span>{% endif %}</td>
<td><span class="badge {{ 'b-ok' if h.status in ('meeting','met','proposal','negotiation','won') else 'b-bad' if h.status == 'lost' else 'b-warn' if h.status == 'new' else '' }}">{{ h.status }}</span></td><td class="r">{{ h.wait if h.wait is not none else '–' }}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No prospects handed over yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["board.html"] = """{% extends 'base.html' %}{% block title %}Prospect board{% endblock %}{% block body %}
<form method="get" class="card"><div class="card-b" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
<div><label>Supervisor</label><select name="sup"><option value="">Everyone</option>{% for s in sups %}<option value="{{ s.id }}" {{ 'selected' if sid == s.id }}>{{ s.name }}</option>{% endfor %}</select></div>
<div><label>Campaign</label><select name="campaign"><option value="">All campaigns</option>{% for c in camps %}<option {{ 'selected' if camp == c }}>{{ c }}</option>{% endfor %}</select></div>
<button class="btn brand">Show</button><span class="muted">Each card is a handed-over prospect. Supervisors move them in their BSO; open a card to comment or move it to another supervisor.</span></div></form>
<div style="display:grid;grid-template-columns:repeat(6,minmax(170px,1fr));gap:10px;overflow-x:auto">
{% for key, label, items in cols %}<div style="background:#eef1f4;border-radius:8px;padding:8px;min-height:200px"><div style="font-weight:600;margin:2px 4px 8px">{{ label }} <span class="muted">{{ items|length }}</span></div>
{% for h in items %}<a href="{{ url_for('handover_detail', hid=h.id) }}" style="display:block;background:#fff;border:1px solid var(--line);{{ 'border-left:4px solid #ba0517;' if h.stale }}border-radius:6px;padding:8px 10px;margin-bottom:8px;color:var(--ink);text-decoration:none">
<b>{{ h.lead_name }}</b><br><small class="muted">{{ h.company }}</small><br><small>{{ h.supname or '–' }} · {{ h.age }}d{% if h.ncomments %} · 💬 {{ h.ncomments }}{% endif %}</small>
{% if h.stale %}<br><small style="color:#ba0517">no update for 2+ days</small>{% endif %}{% if h.campaign %}<br><small class="muted">{{ h.campaign }}</small>{% endif %}</a>{% endfor %}</div>{% endfor %}</div>{% endblock %}"""
T["handover.html"] = """{% extends 'base.html' %}{% block title %}{{ h.lead_name }}{% endblock %}{% block body %}
<p><a href="{{ url_for('board') }}">← Prospect board</a></p>
<div class="card"><div class="card-h"><h2>{{ h.lead_name }}</h2><span class="muted">{{ h.title }}{{ ', ' if h.title and h.company }}{{ h.company }} · {{ h.lead_email }}</span><span style="flex:1"></span><span class="badge">{{ h.status }}</span></div>
<div class="card-b"><div class="grid"><div><small class="muted">Supervisor</small><div><b>{{ h.supname or '–' }}</b>{% if not h.delivered %} <span class="muted">(waiting for their BSO)</span>{% endif %}</div></div>
<div><small class="muted">Handed over by</small><div>{{ h.fromname or '–' }} · {{ h.at|dt }}</div></div><div><small class="muted">Reply type</small><div>{{ (h.label or '').replace('_', ' ') }}</div></div>
<div><small class="muted">Campaign</small><div>{{ h.campaign or '–' }}</div></div></div>
<form method="post" action="{{ url_for('handover_reassign', hid=h.id) }}" style="display:flex;gap:8px;align-items:end;margin-top:12px"><input type="hidden" name="_csrf" value="{{ csrf }}">
<div><label>Move to another supervisor</label><select name="to">{% for s in sups %}<option value="{{ s.id }}" {{ 'selected' if s.name == h.supname }}>{{ s.name }}{{ ' (away until ' ~ s.away_until ~ ')' if s.away_until }}</option>{% endfor %}</select></div>
<button class="btn" onclick="return confirm('Move this prospect? It leaves the current supervisor\'s list and appears in the new one\'s.')">Move</button></form></div></div>
<div class="card" id="comments"><div class="card-h"><h2>Comments</h2><span class="muted">Mention someone with @ and their first name: they see it in their BSO.</span></div><div class="card-b">
{% for c in comments %}<div style="border-left:3px solid #0176d3;padding:6px 10px;margin-bottom:8px;background:#f8fafc"><b>{{ c.author_name }}</b> <span class="muted">{{ c.at|dt }}</span><div style="white-space:pre-wrap">{{ c.text }}</div></div>{% else %}<p class="muted">No comments yet.</p>{% endfor %}
<form method="post" action="{{ url_for('handover_comment', hid=h.id) }}"><input type="hidden" name="_csrf" value="{{ csrf }}"><textarea name="text" style="min-height:80px" placeholder="e.g. @{{ (h.supname or 'Ali').split()[0] }} they asked for a discount, OK up to 10%" required></textarea>
<p class="muted" style="margin:4px 0">People: {% for p in people %}@{{ p.name.split()[0] }}{{ ', ' if not loop.last }}{% endfor %}</p><button class="btn brand">Add comment</button></form></div></div>
<div class="card"><div class="card-h"><h2>Their reply</h2></div><div class="card-b" style="white-space:pre-wrap">{{ p.reply }}</div></div>
{% if p.intro %}<div class="card"><div class="card-h"><h2>Introduction sent</h2></div><div class="card-b" style="white-space:pre-wrap">{{ p.intro }}</div></div>{% endif %}
{% for t in p.thread or [] %}<div class="card"><div class="card-h"><h2>Our email {{ loop.index }}</h2><span class="muted">{{ (t.at or '')[:10] }} · {{ t.subject }}</span></div><div class="card-b" style="white-space:pre-wrap">{{ t.body }}</div></div>{% endfor %}{% endblock %}"""
T["activity.html"] = """{% extends 'base.html' %}{% block title %}Activity{% endblock %}{% block body %}
<form method="get" class="card"><div class="card-b grid"><div><label>User</label><select name="user"><option value="">Everyone</option>{% for u in ulist %}<option value="{{ u.id }}" {{ 'selected' if uid == u.id }}>{{ u.name }}</option>{% endfor %}</select></div>
<div><label>Event</label><select name="type"><option value="">All</option>{% for t in types %}<option {{ 'selected' if typ == t }}>{{ t }}</option>{% endfor %}</select></div><div style="align-self:end"><button class="btn brand">Filter</button></div></div></form>
<div class="card"><table><tr><th>When</th><th>User</th><th>Event</th><th>Lead</th><th>Inbox</th><th>Detail</th></tr>{% for e in rows %}<tr><td class="muted">{{ e.at|dt }}</td><td>{{ e.uname }}</td><td>{{ e.type }}</td><td>{{ e.lead_email }}{% if e.company %}<br><span class="muted">{{ e.company }}</span>{% endif %}</td><td class="muted">{{ e.sender }}</td><td>{{ e.detail }}</td></tr>{% else %}<tr><td colspan="6" class="muted">No activity yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["bso_setup.html"] = """{% extends 'base.html' %}{% block title %}BSO setup{% endblock %}{% block body %}{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
<div class="card"><div class="card-h"><h2>Set up every BSO from here</h2></div><div class="card-b">
<p style="margin-top:0">Fill this in once. When you create a person (or the BSO Bot) and add their inboxes, their BSO sets itself up the first time they sign in: inboxes, mail server, AI key, Instantly key and schedule. Nobody types settings on the laptop. Changes here are sent to every BSO at its next check-in.</p>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><h3 style="font-size:14px;margin:6px 0">Mail server for the sending inboxes</h3>
<div class="grid"><div><label>Outgoing (SMTP) server</label><input name="smtp_host" value="{{ t.smtp_host }}" placeholder="mail.getneosofttech.com"></div><div><label>SMTP port</label><input name="smtp_port" type="number" value="{{ t.smtp_port }}"></div>
<div><label>Incoming (IMAP) server, port 993</label><input name="imap_host" value="{{ t.imap_host }}" placeholder="mail.getneosofttech.com"></div><div><label>Emails a day per inbox</label><input name="daily_limit" type="number" min="1" max="100" value="{{ t.daily_limit }}"></div></div>
<label style="display:flex;gap:8px;align-items:center;color:inherit"><input type="checkbox" name="ramp" style="width:auto" {{ 'checked' if t.ramp }}> Warm up new inboxes (start low and build up over about 3 weeks)</label>
<h3 style="font-size:14px;margin:16px 0 6px">Keys (kept encrypted on this computer; sent encrypted)</h3>
<div class="grid"><div><label>Anthropic API key {% if t.anthropic_key %}<span class="badge b-ok">{{ mask(t.anthropic_key) }}</span>{% endif %}</label><input name="anthropic_key" type="password" autocomplete="off" placeholder="{{ 'leave blank to keep' if t.anthropic_key else 'sk-ant-…' }}">{% if t.anthropic_key %}<label style="display:flex;gap:6px;color:inherit"><input type="checkbox" name="clear_anthropic_key" style="width:auto"> remove</label>{% endif %}</div>
<div><label>Instantly API key (optional) {% if t.instantly_key %}<span class="badge b-ok">{{ mask(t.instantly_key) }}</span>{% endif %}</label><input name="instantly_key" type="password" autocomplete="off" placeholder="{{ 'leave blank to keep' if t.instantly_key else '' }}">{% if t.instantly_key %}<label style="display:flex;gap:6px;color:inherit"><input type="checkbox" name="clear_instantly_key" style="width:auto"> remove</label>{% endif %}</div></div>
<h3 style="font-size:14px;margin:16px 0 6px">Running</h3>
<div class="grid"><div><label>Run the campaigns automatically every N minutes (0 = only when someone clicks Run)</label><input name="autorun_minutes" type="number" min="0" value="{{ t.autorun_minutes }}"></div>
<div><label>Mode</label><select name="mode"><option value="live" {{ 'selected' if t.live }}>Live: send to real leads</option><option value="test" {{ 'selected' if not t.live }}>Test: send everything to the inbox owner first</option></select></div></div>
<p><button class="btn brand">Save and send to every BSO</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Import inboxes from a spreadsheet</h2></div><div class="card-b">
<p style="margin-top:0">Excel (.xlsx) or CSV with one row per inbox: <b>inbox email, sender name, password</b>, and optionally <b>user</b> (whose inbox it is: their sign-in ID or email), <b>user name</b> and <b>role</b>. People who don't exist yet are created, with a password generated for each. <a href="{{ url_for('bso_template') }}">Download the template</a></p>
<form method="post" action="{{ url_for('bso_import') }}" enctype="multipart/form-data"><input type="hidden" name="_csrf" value="{{ csrf }}">
<div class="grid"><div><label>Spreadsheet</label><input type="file" name="file" accept=".xlsx,.xlsm,.csv,.txt" required></div>
<div><label>Whose inboxes are these?</label><select name="assign_to"><option value="">Use the "user" column in the sheet</option>{% for s_ in senders %}<option value="{{ s_.id }}">All of them: {{ s_.name }} ({{ s_.role }})</option>{% endfor %}</select></div>
<div><label>Role for people the sheet creates (if no role column)</label><select name="role"><option value="member">Member</option><option value="bot">BSO Bot</option><option value="manager">Manager</option></select></div></div>
<p><button class="btn brand">Import and send to their BSO</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Who has it</h2></div><table><tr><th>Person</th><th>Role</th><th class="r">Inboxes</th><th>Status</th></tr>
{% for p in people %}<tr><td><a href="{{ url_for('user_detail', uid=p.id) }}">{{ p.name }}</a></td><td>{{ p.role }}</td><td class="r">{{ p.inboxes }}</td>
<td>{% if p.applied and p.applied == p.sent %}<span class="badge b-ok">applied</span>{% elif p.sent %}<span class="badge b-warn">waiting for their BSO</span>{% else %}<span class="muted">add inboxes on their page</span>{% endif %}</td></tr>{% else %}<tr><td colspan="4" class="muted">No senders yet.</td></tr>{% endfor %}</table></div>
<p class="muted">What each person still does on their laptop: install BSO, sign in with the ID and password you gave them, and accept the terms (the law requires each user to accept them). The BSO Bot account does this once on its computer.</p>
{% endblock %}"""
T["bso_import.html"] = """{% extends 'base.html' %}{% block title %}Import inboxes{% endblock %}{% block body %}
<p><a href="{{ url_for('bso_setup') }}">← BSO setup</a></p>
<div class="card"><div class="card-h"><h2>Imported {{ filename }}</h2></div><div class="card-b">
{% for p in problems %}<div class="err">{{ p }}</div>{% endfor %}
{% if creds %}<div class="note"><b>{{ creds|length }} new sign-in(s) created.</b> Give each person their ID and password (shown only now). <a class="btn brand" href="{{ url_for('bso_creds', token=token) }}">Download sign-ins (CSV)</a>
<table style="margin-top:8px"><tr><th>Name</th><th>Sign-in ID</th><th>Password</th><th>Role</th></tr>{% for c in creds %}<tr><td>{{ c.name }}</td><td class="code" style="font-size:13px">{{ c.username }}</td><td class="code" style="font-size:13px">{{ c.password }}</td><td>{{ c.role }}</td></tr>{% endfor %}</table></div>{% endif %}
<table><tr><th>Person</th><th class="r">New inboxes</th><th class="r">Updated</th><th class="r">Total</th><th>Status</th></tr>
{% for r in report %}<tr><td><a href="{{ url_for('user_detail', uid=r.id) }}">{{ r.name }}</a>{% if r.new %} <span class="badge b-ok">new</span>{% endif %}</td><td class="r">{{ r.added }}</td><td class="r">{{ r.updated }}</td><td class="r">{{ r.total }}</td>
<td>{% if r.sent %}sent; their BSO applies it at its next check-in{% else %}<span class="badge b-warn">saved; enter their BSO password once on their page to send it</span>{% endif %}</td></tr>{% else %}<tr><td colspan="5" class="muted">Nothing imported.</td></tr>{% endfor %}</table>
</div></div>{% endblock %}"""
T["conversations.html"] = """{% extends 'base.html' %}{% block title %}Conversations{% endblock %}{% block body %}
<div class="card"><div class="card-h"><h2>Campaigns, by person</h2><span class="muted">click a number to see those leads</span><span style="flex:1"></span><a class="btn" href="{{ url_for('archive_page') }}">Archive (older data)</a></div>
<table><tr><th>Person</th><th>Campaign</th><th class="r">Leads</th><th class="r">Emails sent</th><th class="r">Replied</th><th class="r">🔥 Hot</th><th class="r">☀ Warm</th><th class="r">❄ Cold</th><th class="r">Handed over</th></tr>
{% for c in camps %}{% set base = {'user': c.user_id, 'campaign': c.campaign} %}<tr><td>{{ users_[c.user_id].name if c.user_id in users_ else '?' }}</td><td>{{ c.campaign }}</td>
<td class="r"><a href="{{ url_for('conversations', **base) }}">{{ c.leads }}</a></td><td class="r"><a href="{{ url_for('conversations', what='sent', **base) }}">{{ c.sent }}</a></td>
<td class="r"><a href="{{ url_for('conversations', what='replied', **base) }}">{{ c.replied }}</a></td><td class="r"><a href="{{ url_for('conversations', what='hot', **base) }}">{{ c.hot }}</a></td>
<td class="r"><a href="{{ url_for('conversations', what='warm', **base) }}">{{ c.warm }}</a></td><td class="r"><a href="{{ url_for('conversations', what='cold', **base) }}">{{ c.cold }}</a></td>
<td class="r"><a href="{{ url_for('conversations', what='handed', **base) }}">{{ c.handed }}</a></td></tr>{% else %}<tr><td colspan="9" class="muted">Nothing reported yet. Each BSO sends its data at its next check-in after updating.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-b"><form style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
<div><label>Person</label><select name="user"><option value="">Everyone</option>{% for u in ulist %}<option value="{{ u.id }}" {{ 'selected' if request.args.get('user')|string == u.id|string }}>{{ u.name }}</option>{% endfor %}</select></div>
<div><label>Campaign</label><select name="campaign"><option value="">All</option>{% for c in clist %}<option {{ 'selected' if request.args.get('campaign') == c }}>{{ c }}</option>{% endfor %}</select></div>
<div><label>Show</label><select name="what"><option value="">All leads</option>{% for k, l in [('sent','Emailed'),('replied','Replied'),('hot','🔥 Hot'),('warm','☀ Warm'),('cold','❄ Cold'),('nurture','Nurture'),('handed','Handed over'),('flagged','⚠ Flagged')] %}<option value="{{ k }}" {{ 'selected' if what == k }}>{{ l }}</option>{% endfor %}</select></div>
<div style="flex:1;min-width:200px"><label>Search names, companies and email text</label><input name="q" value="{{ request.args.get('q','') }}"></div><button class="btn brand">Show</button></form></div>
<table><tr><th>Lead</th><th>Person</th><th>Campaign</th><th>Status</th><th>Last activity</th></tr>
{% for l in items %}<tr><td>{% if l.flags %}<span class="badge b-bad">⚠ flagged</span> {% endif %}<a href="{{ url_for('conversation', email=l.email) }}">{{ l.first_name or l.email }}</a> <span class="muted">{{ l.email }}</span><br><small class="muted">{{ l.company or '' }}</small></td>
<td>{{ users_[l.user_id].name if l.user_id in users_ else '?' }}</td><td>{{ l.campaign or 'Main campaign' }}</td>
<td>{{ icons.get(l.temperature, '') }} {{ l.temperature or '' }} {{ l.status }}{% if l.handed_to %}<br><small class="muted">handed to {{ l.handed_to }}</small>{% endif %}{% if l.prospect_stage %}<br><small class="muted">prospect: {{ l.prospect_stage }}</small>{% endif %}</td>
<td class="muted">{{ l.last_at|dt if l.last_at else '' }}</td></tr>{% else %}<tr><td colspan="5" class="muted">No leads match.</td></tr>{% endfor %}</table>
{% if pages > 1 %}<div class="card-b">Page {{ page }} of {{ pages }} · {% if page > 1 %}<a href="{{ request.path }}?{{ request.query_string.decode()|replace('page=' ~ page, '') }}&page={{ page - 1 }}">previous</a>{% endif %} {% if page < pages %}<a href="{{ request.path }}?{{ request.query_string.decode()|replace('page=' ~ page, '') }}&page={{ page + 1 }}">next</a>{% endif %}</div>{% endif %}</div>
{% endblock %}"""
T["conversation.html"] = """{% extends 'base.html' %}{% block title %}{{ e }}{% endblock %}{% block body %}
<div class="card"><div class="card-h"><h2>{{ e }}</h2></div><div class="card-b">
{% for l in leads_ %}<div><b>{{ l.first_name or '' }}</b> · {{ l.company or '' }} · in {{ users_.get(l.user_id, '?') }}'s BSO · campaign {{ l.campaign or 'Main campaign' }} · {{ l.status }}
 {% if l.temperature %} · {{ icons.get(l.temperature, '') }} {{ l.temperature }} <span class="muted">({{ l.temp_reason or '' }})</span>{% endif %}{% if l.check_back %} · check back {{ l.check_back }}{% endif %}
 {% if l.handed_to %} · handed to {{ l.handed_to }}{% endif %}{% if l.prospect_stage %} · prospect stage {{ l.prospect_stage }}{% endif %}{% if l.came_back %} · came back {{ l.came_back }}{% endif %}</div>{% endfor %}
</div></div>
{% if incs %}<div class="card" style="border-left:4px solid #ba0517"><div class="card-h"><h2>⚠ Incidents on this conversation</h2></div><div class="card-b">{% for i in incs %}<div><b>{{ kinds.get(i.kind, i.kind) }}</b> · {{ i.at|dt }} · {{ users_.get(i.user_id, 'console') }}: {{ i.detail }} <a href="{{ url_for('security_page') }}#inc{{ i.id }}">open</a></div>{% endfor %}</div></div>{% endif %}
<div class="card"><div class="card-h"><h2>Every email ({{ items|length }})</h2></div><div class="card-b">
{% for m in items %}<div style="border:1px solid var(--line);border-left:4px solid {{ '#2e844a' if m.dir == 'in' else '#0176d3' }};border-radius:6px;padding:10px 12px;margin-bottom:10px;{{ 'background:#f6fbf7' if m.dir == 'in' }}">
<div class="muted" style="font-size:12px">{{ 'Received' if m.dir == 'in' else 'Sent' }} {{ m.at|dt }} · {{ m.who }}'s BSO · {{ m.tag }}{% if m.extra %} · {{ m.extra }}{% endif %}</div>
<div><b>From:</b> {{ m.frm }}{% if m.to %} &nbsp; <b>To:</b> {{ m.to }}{% endif %}{% if m.cc %} &nbsp; <b>CC:</b> {{ m.cc }}{% endif %}{% if m.bcc %} &nbsp; <b style="color:#ba0517">BCC:</b> {{ m.bcc }}{% endif %}</div>
<div><b>{{ m.subject }}</b></div><div style="white-space:pre-wrap;margin-top:6px">{{ m.body }}</div></div>{% else %}<p class="muted">No emails reported yet.</p>{% endfor %}</div></div>
<div class="grid"><div class="card"><div class="card-h"><h2>Notes</h2></div><div class="card-b">{% for n in notes %}<div style="margin-bottom:8px"><span class="muted">{{ n.at|dt }} · {{ users_.get(n.user_id, '?') }}</span><div style="white-space:pre-wrap">{{ n.body }}</div></div>{% else %}<span class="muted">None.</span>{% endfor %}</div></div>
<div class="card"><div class="card-h"><h2>Tasks and temperature</h2></div><div class="card-b">{% for t in tasks_ %}<div>{{ '✓' if t.status == 'done' else '○' }} {{ t.subject }} <span class="muted">due {{ t.due_at or t.due_date }}</span></div>{% endfor %}
{% for t in temps %}<div class="muted">{{ t.at|dt }}: {{ icons.get(t.temperature, '') }} {{ t.temperature }} by {{ t.by }} ({{ t.reason }})</div>{% endfor %}</div></div></div>
{% if arch %}<div class="card"><div class="card-h"><h2>From the archive (before full visibility)</h2></div><div class="card-b">{% for a in arch %}<div style="margin-bottom:6px"><span class="muted">{{ a.at }} · {{ a.kind }} · {{ users_.get(a.user_id, '?') }}</span><div style="white-space:pre-wrap">{{ a.text[:1500] }}</div></div>{% endfor %}</div></div>{% endif %}
{% endblock %}"""
T["archive.html"] = """{% extends 'base.html' %}{% block title %}Archive{% endblock %}{% block body %}
<div class="card"><div class="card-h"><h2>Archive</h2><span class="muted">everything each BSO held before full visibility: read-only, kept until you delete it</span></div><div class="card-b">
<form style="display:flex;gap:8px"><input name="q" value="{{ q }}" placeholder="search an email address, company, subject or text"><button class="btn brand">Search</button></form>
<p class="muted">{% for c in counts %}{{ c.name }}: {{ c.n }} {{ c.kind }}{{ ', ' if not loop.last }}{% else %}Nothing archived yet.{% endfor %}</p></div>
<table><tr><th>When</th><th>Whose BSO</th><th>What</th><th>Lead</th><th>Text</th></tr>{% for r in rows %}<tr><td class="muted">{{ r.at }}</td><td>{{ r.uname }}</td><td>{{ r.kind }}</td>
<td>{% if r.lead_email %}<a href="{{ url_for('conversation', email=r.lead_email) }}">{{ r.lead_email }}</a>{% endif %}</td><td style="white-space:pre-wrap">{{ r.text[:600] }}</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["workspace.html"] = """{% extends 'base.html' %}{% block title %}Google Workspace setup{% endblock %}{% block body %}
<div class="card"><div class="card-h"><h2>Google Workspace: a record supervisors can't delete, and blocking outside copies everywhere</h2></div><div class="card-b" style="line-height:1.6;max-width:900px">
<p>BSO blocks outside copies of client emails written inside BSO, and reports anything it finds afterwards. These Google Workspace settings cover email written in Gmail on the web or on phones too, and keep a copy nobody but you can delete. You need to be a Workspace super admin. Settings can take up to an hour to apply. Menus are as Google shows them in 2026; if one has moved, search the Admin console for the setting's name.</p>
<h3>1. A company archive mailbox</h3><ol>
<li>Admin console (admin.google.com) → <b>Directory → Users → Add new user</b>, e.g. <b>archive@blacswanken.com</b>. Only you keep its password. Turn on 2-step verification for it.</li>
<li>Make a group of the supervisors: <b>Directory → Groups → Create group</b>, e.g. <i>lead-gen-supervisors@</i>, and add every supervisor.</li></ol>
<h3>2. Copy every supervisor email to the archive</h3><ol>
<li><b>Apps → Google Workspace → Gmail → Routing</b>. Next to <b>Routing</b>, click <b>Configure</b>.</li>
<li>Name it <i>Archive supervisors</i>. Messages to affect: tick <b>Inbound</b>, <b>Outbound</b> and <b>Internal - sending</b> / <b>Internal - receiving</b>.</li>
<li>Under <i>Also deliver to</i> → <b>Add more recipients</b> → <b>Add</b> → archive@blacswanken.com.</li>
<li>Under <i>Options</i>, limit it to the supervisors' group (Account types / Envelope filter: <b>Only affect specific envelope senders</b> and a second rule for <b>recipients</b>, both set to the group). Save.</li>
<li>Every email to or from a supervisor now also lands in the archive mailbox the moment it's sent or received. Deleting it in their own mailbox doesn't touch this copy.</li></ol>
<h3>3. Connect the archive mailbox to this console</h3><ol>
<li>Sign in to archive@ once, then <b>Google Account → Security → App passwords</b>, create one named <i>BSO console</i>.</li>
<li>Here: <b>Security → Company archive mailbox</b>: IMAP server <b>imap.gmail.com</b>, the address and the app password. The console checks it every 10 minutes.</li></ol>
<h3>4. Hold outside copies for your approval (quarantine)</h3><ol>
<li><b>Apps → Google Workspace → Gmail → Compliance → Content compliance</b> → <b>Configure</b>. Name it <i>Client emails outside</i>.</li>
<li>Messages to affect: <b>Outbound</b>. Add expressions: <b>Advanced content match</b> → Location <b>Recipient(s)</b> (this includes BCC) → <b>Matches regex</b>:<br><code>@(gmail|googlemail|yahoo|hotmail|outlook|live|icloud|aol|proton|protonmail|gmx|yandex|zoho)\\.</code></li>
<li>If the above expressions match: <b>Modify message</b> → <b>Send to admin quarantine</b> (create a quarantine named <i>Outside copies</i> under <b>Gmail → Manage quarantines</b> if asked). Limit it to the supervisors' group under <i>Options</i>.</li>
<li>Quarantined emails wait in <b>Admin console → Reporting → Email log search / Admin quarantine</b> until you release or reject them. Supervisors can't get around it from any app.</li>
<li>Your plan decides what's available: if <i>admin quarantine</i> isn't offered, choose <b>Reject message</b> instead, which bounces it back to the supervisor.</li></ol>
<h3>5. Google Vault (if your plan includes it)</h3><p>Business Plus, Enterprise and some Education plans include Vault (vault.google.com). Create a <b>Retention rule</b> for Gmail covering the supervisors' group ("retain indefinitely"): Vault then keeps every message even after a supervisor permanently deletes it, and you can search and export them for legal needs. Check your plan under <b>Billing → Subscriptions</b>.</p>
<h3>6. Tell the team</h3><p>Monitoring company mailboxes is generally allowed when staff are clearly told beforehand. BSO shows each person the updated notice and they confirm it once. Your local employment and privacy rules may add requirements; take local advice.</p>
</div></div>{% endblock %}"""
T["insights.html"] = """{% extends 'base.html' %}{% block title %}Insights{% endblock %}{% block body %}{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
<div class="card"><div class="card-h"><h2>From lead to sale</h2><span class="muted">log scale, so every step stays readable</span><span style="flex:1"></span>
<form style="margin:0"><select name="period" onchange="this.form.submit()">{% for k, l in [('all','All time'),('90d','Last 90 days'),('30d','Last 30 days')] %}<option value="{{ k }}" {{ 'selected' if period == k }}>{{ l }}</option>{% endfor %}</select><input type="hidden" name="by" value="{{ by }}"></form></div>
<div class="card-b">{{ svg|safe }}<p class="muted">Won so far: {{ '{:,.0f}'.format(won_value) }} in deal value.{% for k, t in timings.items() %} · {{ k }}: {{ t.median_days if t.median_days is not none else '–' }} days (median of {{ t.n }}){% endfor %}</p></div></div>
<div class="card"><div class="card-h"><h2>Where it works</h2><span style="flex:1"></span>{% for k, l in [('campaign','Campaign'),('person','Person'),('supervisor','Supervisor'),('strategy','Angle / subject / ask'),('segment','Kind of lead'),('inbox','Inbox')] %}<a class="btn {{ 'brand' if by == k }}" style="padding:4px 10px;margin-left:4px" href="{{ url_for('insights', by=k, period=period) }}">{{ l }}</a>{% endfor %}</div>
<table><tr><th>{{ by|capitalize }}</th><th class="r">Leads</th><th class="r">Emailed</th><th class="r">Replied</th><th class="r">Positive</th><th class="r">Meetings</th><th class="r">Won</th><th class="r">Value</th><th class="r">Reply %</th><th class="r">Positive %</th><th class="r">Meeting %</th></tr>
{% for r in rows %}<tr><td>{{ r.key }}</td><td class="r">{{ r.leads }}</td><td class="r">{{ r.emailed }}</td><td class="r">{{ r.replied }}</td><td class="r">{{ r.positive }}</td><td class="r">{{ r.meetings }}</td><td class="r">{{ r.won }}</td><td class="r">{{ '{:,.0f}'.format(r.value) }}</td><td class="r">{{ r.reply_rate }}</td><td class="r">{{ r.positive_rate }}</td><td class="r">{{ r.meeting_rate }}</td></tr>{% else %}<tr><td colspan="11" class="muted">No results reported yet.</td></tr>{% endfor %}</table></div>
<div class="card" id="strategy"><div class="card-h"><h2>Strategy for the bots</h2>{% if active.version %}<span class="muted">in use since {{ active.approved_at|dt if active.approved_at else '' }} ({{ active.approved_by or '' }})</span>{% else %}<span class="muted">none approved yet: bots try every angle evenly</span>{% endif %}
<span style="flex:1"></span><form method="post" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" name="action" value="recompute">Recompute now</button></form></div><div class="card-b">
{% if pending.version %}<div class="note"><b>Proposed changes</b> (from {{ pending.data.emailed }} emails, {{ pending.data.positive }} positive replies, {{ pending.data.meetings }} meetings, {{ pending.data.won }} won):<ul>{% for c in pending.changes or [] %}<li>{{ c }}</li>{% else %}<li>First strategy from your results.</li>{% endfor %}</ul>
{% if pending.lessons %}<b>Lessons the writer will use:</b><ul>{% for l in pending.lessons %}<li>{{ l }}</li>{% endfor %}</ul>{% endif %}
{% if me.role == 'owner' %}<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn brand" name="action" value="approve">Approve: send to every BSO</button> <button class="btn" name="action" value="reject">Keep the current one</button></form>{% endif %}</div>
{% elif active.lessons %}<b>Lessons in use:</b><ul>{% for l in active.lessons %}<li>{{ l }}</li>{% endfor %}</ul>{% endif %}
<p class="muted">The bots keep about 20% of emails for trying other angles, so they never stop learning. Results are judged on meetings and deals, not opens. Until there's enough data (roughly 1,000–2,000 emails and a few dozen replies), they use the defaults.</p>
{% if weights %}<b>What makes a lead likely to respond</b> (lead scoring, used to email the most promising first):<ul>{% for k, v in weights %}<li>{{ k.replace(':', ': ') }} <span class="{{ 'b-ok' if v > 0 else 'b-bad' }} badge">{{ '+' if v > 0 }}{{ '%.2f'|format(v) }}</span></li>{% endfor %}</ul>{% endif %}
{% if me.role == 'owner' %}<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="auto"><label style="display:flex;gap:6px;color:inherit"><input type="checkbox" name="auto" value="1" style="width:auto" {{ 'checked' if auto }} onchange="this.form.submit()"> Approve small weekly changes automatically (no change bigger than 10 points of volume)</label></form>{% endif %}
</div></div>
<div class="card"><div class="card-h"><h2>Next best step for open deals</h2></div><table><tr><th>Prospect</th><th>Supervisor</th><th>Stage</th><th class="r">Days in stage</th><th>Suggested next step</th></tr>
{% for x in steps %}<tr><td><a href="{{ url_for('conversation', email=x.email) }}">{{ x.name or x.email }}</a> <span class="muted">{{ x.company or '' }}</span></td><td>{{ x.sup or '—' }}</td><td>{{ x.stage }}</td><td class="r">{{ x.days }}</td><td>{{ x.step }}</td></tr>{% else %}<tr><td colspan="5" class="muted">No open deals.</td></tr>{% endfor %}</table></div>
{% endblock %}"""
T["security.html"] = """{% extends 'base.html' %}{% block title %}Security{% endblock %}{% block body %}
{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
<div class="card" id="incidents"><div class="card-h"><h2>Incidents</h2><span class="badge {{ 'b-ok' if chain_ok else 'b-bad' }}">{{ 'log intact' if chain_ok else 'log altered at #' ~ chain_bad }}</span>
<span style="flex:1"></span><form style="margin:0"><select name="kind" onchange="this.form.submit()"><option value="">All types</option>{% for k, l in kinds.items() %}<option value="{{ k }}" {{ 'selected' if kind_f == k }}>{{ l }}</option>{% endfor %}</select></form>
<a class="btn" href="{{ url_for('incidents_csv') }}">Export all (CSV)</a></div>
<p class="muted" style="margin:8px 16px">Every client email copied outside the company, blocked, deleted or sent outside BSO. Entries are permanent and chained together, so any change to an old entry shows above. Reviewing adds a note beside an entry; it never changes it.</p>
<table><tr><th>When</th><th>Who</th><th>What</th><th>Lead</th><th>Review</th></tr>
{% for i in incs %}<tr id="inc{{ i.id }}" style="{{ 'background:#fff4f4' if not i.rstatus }}"><td class="muted">#{{ i.id }}<br>{{ i.at|dt }}</td><td>{{ i.uname or 'console' }}</td>
<td><b>{{ kinds.get(i.kind, i.kind) }}</b><br>{{ i.detail }}<br><small class="muted">{{ i.how }}</small>{% if i.msg %}<details><summary>the email</summary><pre style="white-space:pre-wrap;font:12px ui-monospace,Consolas,monospace">{{ i.msg }}</pre></details>{% endif %}</td>
<td>{% if i.lead_email %}<a href="{{ url_for('conversation', email=i.lead_email) }}">{{ i.lead_email }}</a>{% endif %}</td>
<td>{% if i.rstatus %}<span class="badge {{ 'b-ok' if i.rstatus == 'fine' else 'b-warn' }}">{{ i.rstatus }}</span> by {{ i.rby }}{% if i.rnote %}<br><small>{{ i.rnote }}</small>{% endif %}{% endif %}
<form method="post" style="margin-top:4px"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="review"><input type="hidden" name="incident_id" value="{{ i.id }}">
<select name="status"><option value="reviewed">reviewed: keep flagged</option><option value="fine">fine (no problem)</option><option value="escalated">escalated (HR / legal)</option></select>
<input name="note" placeholder="note" style="margin-top:4px"><label style="display:flex;gap:6px;color:inherit;font-size:12px"><input type="checkbox" name="allow_domain" value="{{ (i.detail or '').split('@')[-1].split(' ')[0] if '@' in (i.detail or '') else '' }}" style="width:auto"> allow this domain from now on</label>
<button class="btn" style="margin-top:4px">Save review</button></form>{% if i.user_id %}<a class="btn" href="{{ url_for('user_detail', uid=i.user_id) }}">Person: reassign / pause…</a>{% endif %}</td></tr>
{% else %}<tr><td colspan="5" class="muted">No incidents.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Supervisors' mailboxes</h2></div><table><tr><th>Supervisor</th><th>Mailbox in BSO</th><th>Last checked</th><th>Problems</th></tr>
{% for w in watch %}<tr><td>{{ w.u.name }}</td><td>{{ 'connected' if w.st.mailbox_connected else 'NOT connected' }}</td><td class="muted">{{ w.st.mailbox_checked|dt if w.st.mailbox_checked else '—' }}</td><td>{{ w.warn|join('; ') or '—' }}</td></tr>{% else %}<tr><td colspan="4" class="muted">No supervisors.</td></tr>{% endfor %}</table></div>
<div class="card" id="archivebox"><div class="card-h"><h2>Company archive mailbox</h2>{% if box_checked %}<span class="muted">last checked {{ box_checked|dt }}</span>{% endif %}</div><div class="card-b">
<p style="margin-top:0">For a record supervisors can't delete: in Google Workspace, copy every supervisor email to an archive mailbox only you control (<a href="{{ url_for('workspace_guide') }}">step-by-step guide</a>). Connect it here and the console checks it every 10 minutes: anything a supervisor sent or received with a lead that their BSO never saw becomes an incident, and outside copies are flagged even if the email was deleted.</p>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="archive_box"><div class="grid">
<div><label>IMAP server</label><input name="box_host" value="{{ box_host }}" placeholder="imap.gmail.com"></div><div><label>Archive mailbox address</label><input name="box_user" value="{{ box_user }}" placeholder="archive@blacswanken.com"></div>
<div><label>App password (kept encrypted on this computer)</label><input name="box_password" type="password" placeholder="{{ 'saved, leave blank to keep' if box_user else '' }}"></div></div><p><button class="btn brand">Save and check now</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Alerts</h2><span class="muted">last 7 days</span></div><div class="card-b">
{% for lvl, head, sub in alerts %}<div class="{{ 'err' if lvl == 'bad' else 'note' }}"><b>{{ head }}</b><br><span class="muted">{{ sub }}</span></div>{% else %}<span class="muted">Nothing needs your attention.</span>{% endfor %}</div></div>
<div class="card"><div class="card-h"><h2>Rules for every BSO</h2></div><div class="card-b"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}">
<div class="grid"><div><label>Keep emails and conversations for (months, 0 = forever; incidents and the archive are always kept)</label><input name="retention_months" type="number" min="0" value="{{ cfg.retention_months }}"></div>
<div style="grid-column:span 2"><label>Domains supervisors may copy on client emails, besides our own and the client's (partners), comma separated</label><input name="allow_domains" value="{{ cfg.allow_domains }}" placeholder="partnerfirm.com, consultants.io"></div></div>
<div class="grid"><div><label>Default automatic sign-out after this many idle minutes (0 = never; each person can change their own)</label><input name="idle_minutes" type="number" min="0" max="240" value="{{ cfg.idle_minutes }}"></div>
<div><label>Ask for the password again every N days, even offline</label><input name="reauth_days" type="number" min="1" max="90" value="{{ cfg.reauth_days }}"></div>
<div><label>Flag anyone who views or exports more than this many records at once</label><input name="big_list" type="number" min="50" value="{{ cfg.big_list }}"></div></div>
<label style="display:flex;gap:8px;align-items:center;color:inherit"><input type="checkbox" name="members_export" style="width:auto" {{ 'checked' if 'member' in cfg.export_roles }}> Let members export to CSV and open the data folder (owners and managers always can; every export is logged here)</label>
<h3 style="margin:18px 0 4px;font-size:14px">Trap contacts</h3><p class="muted" style="margin:0">Each sending account gets 2 fake leads on a domain you own. BSO never emails them. If one ever receives mail, that person's list was copied out, and you'll see whose.</p>
<div class="grid"><div><label>Trap domain (one you control, with a catch-all mailbox)</label><input name="canary_domain" value="{{ cfg.canary_domain }}" placeholder="e.g. mail-desk-co.com"></div>
<div><label>Catch-all IMAP host (optional, checked every 30 min)</label><input name="imap_host" value="{{ imap_host }}" placeholder="mail.example.com"></div>
<div><label>IMAP username</label><input name="imap_user" value="{{ imap_user }}"></div><div><label>IMAP password (kept encrypted on this computer)</label><input name="imap_password" type="password" placeholder="{{ 'saved, leave blank to keep' if imap_user else '' }}"></div></div>
<p><button class="btn brand" name="action" value="save">Save</button> {% if imap_host %}<button class="btn" name="action" value="check_now">Check the trap mailbox now</button>{% endif %}</p></form>
<form method="post" style="margin-top:8px"><input type="hidden" name="_csrf" value="{{ csrf }}"><div class="grid"><div><label>Got an email address from somewhere suspicious? Check if it's a trap</label><input name="address" placeholder="name@domain"></div><div><label>Where you saw it (optional)</label><input name="note"></div></div><p><button class="btn" name="action" value="lookup">Check</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Laptops</h2></div><table><tr><th>Person</th><th>Drive encrypted</th><th>Idle lock</th><th>Last seen</th><th>Erase</th></tr>{% for e in enc %}<tr><td>{{ e.name }}</td>
<td>{% if e.enc is sameas true %}<span class="badge b-ok">yes</span>{% elif e.enc is sameas false %}<span class="badge b-bad">no</span>{% else %}<span class="muted">not reported yet</span>{% endif %}</td>
<td>{{ (e.lock ~ ' min') if e.lock else '—' }}</td><td class="muted">{{ e.seen|dt if e.seen else 'never' }}</td><td>{{ 'requested' if e.wipe and e.wipe.startswith('pending') else ('done' if e.wipe == 'done' else '') }}</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Data access log</h2><span class="muted">exports, big lists, searches, reported by each BSO</span></div><table><tr><th>When</th><th>Who</th><th>What</th><th class="r">Records</th><th>Detail</th></tr>
{% for r in rows %}<tr><td class="muted">{{ r.at|dt }}</td><td>{{ r.uname }}</td><td>{% if r.kind == 'denied' %}<span class="badge b-bad">blocked</span>{% else %}{{ r.kind|replace('_', ' ') }}{% endif %}</td><td class="r">{{ r.n }}</td><td>{{ r.detail }}</td></tr>{% else %}<tr><td colspan="5" class="muted">Nothing yet.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Trap contacts</h2></div><table><tr><th>Address</th><th>Planted for</th><th>Since</th><th>Status</th></tr>{% for t in traps %}<tr><td>{{ t.email }}</td><td>{{ t.uname or 'removed account' }}</td><td class="muted">{{ t.at|dt }}</td>
<td>{% if t.hit_at %}<span class="badge b-bad">received mail {{ t.hit_at|dt }}</span> <span class="muted">{{ t.hit_detail }}</span>{% else %}<span class="badge b-ok">quiet</span>{% endif %}</td></tr>{% else %}<tr><td colspan="4" class="muted">Set a trap domain above to plant them.</td></tr>{% endfor %}</table></div>
{% endblock %}"""
T["audit.html"] = """{% extends 'base.html' %}{% block title %}Audit log{% endblock %}{% block body %}<div class="card"><div class="card-h"><h2>Who changed what in this console</h2></div><table><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th></tr>{% for r in rows %}<tr><td class="muted">{{ r.at|dt }}</td><td>{{ r.actor }}</td><td>{{ r.action }}</td><td>{{ r.detail }}</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["settings.html"] = """{% extends 'base.html' %}{% block title %}Settings{% endblock %}{% block body %}{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
<div class="card"><div class="card-h"><h2>Team connection through your Google Drive</h2>{% if relay_url and relay_ok and not relay_err %}<span class="badge b-ok">Connected</span>{% elif relay_url %}<span class="badge b-bad">Problem</span>{% else %}<span class="badge b-warn">Not set up</span>{% endif %}</div><div class="card-b">
{% if relay_url and relay_version < relay_latest %}<div class="err"><b>Update your Google script (one time, the address stays the same).</b> With the new version, cold emails get a one-click unsubscribe link (Gmail and Yahoo expect one), and handovers, comments, away dates and moving prospects between supervisors are handled by Google itself, so nobody needs this console to be open for them.
<ol style="margin:6px 0 0"><li>Click <b>Copy script</b> below.</li><li>In script.google.com, open your BSO project, select all the code, paste over it and <b>Save</b>.</li><li><b>Deploy → Manage deployments →</b> pencil (Edit) <b>→ Version: New version → Deploy</b>.</li><li>Back here, click <b>Sync now</b>. This message disappears when the update is detected.</li></ol></div>{% endif %}
{% if relay_url %}<p style="margin-top:0">Team laptops sign in and report through <b>{{ relay_url }}</b>. Last sync: {{ relay_ok|dt if relay_ok else 'never' }}.{% if relay_err %}<br><span style="color:#ba0517">{{ relay_err }}</span>{% endif %}</p>
<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" name="action" value="relay_sync">Sync now</button></form>
<details style="margin-top:12px" {{ 'open' if relay_version < relay_latest }}><summary>Set up again or change the address</summary>{% endif %}
<ol style="line-height:1.7">
<li>Copy this script (it is unique to this console; keep it private): <button type="button" class="btn" data-copy="relayscript" style="padding:3px 10px">Copy script</button> or <a href="{{ url_for('relay_script') }}">download Code.gs</a>
<textarea id="relayscript" readonly style="min-height:90px;margin-top:6px">{{ relay_script }}</textarea></li>
<li>Open <a href="https://script.google.com/home" target="_blank" rel="noopener">script.google.com</a>, signed in to the Google account that should hold the team data → <b>New project</b>. Delete what's there, paste, and click <b>Save</b>.</li>
<li><b>Deploy → New deployment</b> → gear icon → <b>Web app</b>. Execute as: <b>Me</b>. Who has access: <b>Anyone</b>. Click <b>Deploy</b>.</li>
<li>Click <b>Authorize access</b> and choose your account. Google warns the app isn't verified (it's your own script): <b>Advanced → Go to … → Allow</b>.</li>
<li>Copy the <b>Web app URL</b> and paste it here:</li></ol>
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="relay_url">
<input name="relay_url" value="{{ relay_url }}" placeholder="https://script.google.com/macros/s/…/exec"><p><button class="btn brand">Save &amp; test</button></p></form>
<p class="muted" style="margin-bottom:0">Team laptops must use the same address: it is built into the BSO you give them. If you ever change it (a new deployment gives a new address), they need a BSO with the new one. To update the script itself, use <b>Deploy → Manage deployments → Edit → New version</b>, which keeps the address.</p>
{% if relay_url %}</details>{% endif %}</div></div>
<div class="card"><div class="card-h"><h2>Hub settings</h2></div><div class="card-b"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="org">
<label>Team or company name</label><input name="org" value="{{ org }}"><label>Public address of this hub (what laptops connect to, e.g. https://hub.example.com). Shown with new users' sign-in details.</label><input name="public_url" value="{{ public_url }}" placeholder="https://hub.your-company.com">
<label>If a laptop cannot reach the hub for this many hours, it pauses sending (0 = never)</label><input name="grace_hours" type="number" min="0" max="720" value="{{ grace }}"><p><button class="btn brand">Save</button></p></form></div></div>
<div class="card"><div class="card-h"><h2>Installing BSO for your team</h2></div><div class="card-b">
<p style="margin-top:0">Make each install ask for an ID and password and point at this hub, in either of two ways:</p>
<ul><li>Install with <code>BSO-Setup.exe /HUB={{ public_url or 'https://your-hub-address' }}</code></li>
<li>or <a href="{{ url_for('team_defaults_file') }}">download team-defaults.json</a> and put it in the BSO program folder (next to <code>BSO.exe</code>).</li></ul>
<p class="muted" style="margin-bottom:0">Without either, BSO behaves as a normal single-user app and asks for the hub address when someone signs in under Settings → Team.</p></div></div>
<div class="card"><div class="card-h"><h2>Outgoing email (for password-reset links)</h2></div><div class="card-b"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}">
<div class="grid"><div><label>SMTP server</label><input name="host" value="{{ smtp.host }}" placeholder="smtp.gmail.com"></div><div><label>Port (587 or 465)</label><input name="port" value="{{ smtp.port or 587 }}"></div>
<div><label>Login</label><input name="user" value="{{ smtp.user }}" autocomplete="off"></div><div><label>Password / app password {{ '(saved, leave blank to keep)' if smtp.password }}</label><input name="password" type="password" autocomplete="new-password"></div>
<div><label>Send from</label><input name="from" value="{{ smtp['from'] }}" placeholder="BSO hub &lt;hub@your-company.com&gt;"></div></div>
<p><button class="btn brand" name="action" value="smtp">Save</button> <button class="btn" name="action" value="smtp_test">Send a test email to me</button></p>
<p class="muted" style="margin:0">Use a mailbox the hub may send from (for Gmail, an app password). Set the public address above too, so links in the email open the right place.</p></form></div></div>
<div class="card"><div class="card-h"><h2>Stop the master console</h2></div><div class="card-b"><p style="margin-top:0" class="muted">It runs in the background. While it's stopped, team laptops can't check in (they keep working for the grace period).</p>
<form method="post" action="{{ url_for('hub_shutdown') }}" onsubmit="return confirm('Stop the master console?')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger">Stop it</button></form></div></div>
<div class="card"><div class="card-h"><h2>Change your password</h2></div><div class="card-b"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="password">
<div class="grid"><div><label>Current password</label><input name="old" type="password" required></div><div><label>New password (10+ characters)</label><input name="new" type="password" required autocomplete="new-password"></div><div><label>New password again</label><input name="new2" type="password" required autocomplete="new-password"></div></div><p><button class="btn brand">Change password</button></p></form></div></div>{% endblock %}"""
app.jinja_loader = DictLoader(T)


# ---------------------------------------------------------------------------------------------- run

import hub_bot as _hub_bot  # noqa: E402  (the BSO Bot, Devices and Clients pages)
_hub_bot.install(sys.modules[__name__])


def prepare(data=None):
    """Create the database, the secret key and (until an owner exists) the one-time setup key."""
    global DB_PATH
    d = Path(data) if data else data_dir()
    d.mkdir(parents=True, exist_ok=True)
    DB_PATH = d / "hub.db"
    init_db()
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    def get(k):
        r = con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return r["value"] if r else None
    def put(k, v):
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, v))
        con.commit()
    if not get("secret_key"):
        put("secret_key", secrets.token_hex(32))
    app.secret_key = get("secret_key")
    setup_key = None
    if not con.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
        setup_key = secrets.token_urlsafe(9)
        put("setup_key_hash", sha(setup_key))
        (d / "setup-key.txt").write_text(setup_key + "\n")
    con.close()
    return d, setup_key


def main(argv=None):
    ap = argparse.ArgumentParser(description="BSO master hub")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to let other computers connect")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--data", help="folder for the hub database (default: the BSO data folder)")
    ap.add_argument("--owner-email", help="pre-fill the master sign-in email on the setup page (default omar@blacswanken.com)")
    ap.add_argument("--reset-owner-2fa", action="store_true", help="lost your phone and recovery codes? Switch off the owner's 2-step sign-in from this computer")
    ap.add_argument("--reset-owner-password", action="store_true", help="locked out and email isn't set up? Set a new owner password from this computer")
    a = ap.parse_args(argv)
    d, key = prepare(a.data)
    if a.owner_email:
        _c = sqlite3.connect(str(DB_PATH)); _c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('owner_email',?)", (a.owner_email.strip().lower(),)); _c.commit(); _c.close()
    if a.reset_owner_2fa:
        con = sqlite3.connect(str(DB_PATH))
        con.execute("UPDATE users SET totp_secret=NULL, recovery_codes=NULL WHERE role='owner'")
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), "local", "owner 2-step reset from the hub computer", ""))
        con.commit()
        print("The owner's 2-step sign-in is off. Sign in with your password and set it up again.")
        return
    if a.reset_owner_password:
        import getpass
        con = sqlite3.connect(str(DB_PATH))
        row = con.execute("SELECT id, email FROM users WHERE role='owner'").fetchone()
        if not row:
            sys.exit("No owner account yet: just start the hub and open the setup page.")
        pw = getpass.getpass(f"New password for {row[1]} (10+ characters): ")
        if not strong(pw) or pw != getpass.getpass("Again: "):
            sys.exit("Not changed: passwords must match and be at least 10 characters.")
        con.row_factory = sqlite3.Row
        set_password(con, row[0], pw)
        con.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?,?,?,?)", (iso(), "local", "owner_password_reset_cli", ""))
        con.commit()
        print("Owner password changed.")
        return
    print(f"\n  BSO master hub, data folder: {d}")
    print(f"  Open http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}")
    if key:
        print(f"\n  FIRST-TIME SETUP KEY (needed to create the owner account): {key}\n")
    threading.Thread(target=_relay_loop, daemon=True).start()
    if a.host not in ("127.0.0.1", "localhost"):
        print("  This hub is reachable from other computers. Put it behind HTTPS before using it over the internet.\n")
    try:
        from waitress import serve
        serve(app, host=a.host, port=a.port, threads=8)
    except ImportError:
        app.run(host=a.host, port=a.port, threaded=True)


if __name__ == "__main__":
    main()
