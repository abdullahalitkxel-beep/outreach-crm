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

ROLES = ("owner", "manager", "member")
EVENT_TYPES = ("sent", "reply", "drafted", "approved", "rejected", "bounce", "error", "settings", "app_start", "paused")
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
    for col, decl in (("relay_pw", "TEXT"), ("tv", "INTEGER DEFAULT 1")):
        if col not in {r[1] for r in con.execute("PRAGMA table_info(users)")}:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
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
        u = db().execute("SELECT * FROM users WHERE id=? AND status!='disabled' AND role IN ('owner','manager')", (uid,)).fetchone()
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
            u = db().execute("SELECT * FROM users WHERE (username=? OR email=?) AND status!='disabled' AND role IN ('owner','manager')", (email, email)).fetchone()
            if u and u["pw_hash"] and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                throttle_clear(k1)
                session.clear()
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
            u = db().execute("SELECT * FROM users WHERE (email=? OR username=?) AND status!='disabled' AND role IN ('owner','manager')", (email, email)).fetchone()
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
    for u in db().execute("SELECT * FROM users WHERE role IN ('member','manager','owner') ORDER BY name").fetchall():
        c = lambda t, s=since: db().execute("SELECT COUNT(*) FROM events WHERE user_id=? AND type=? AND at>=?", (u["id"], t, s)).fetchone()[0]
        sent, replies, bounces = c("sent"), c("reply"), c("bounce")
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
                  online=sum(1 for s in stats if s["online"]))
    series = daily_series()
    mx = max([n for _, n in series] + [1])
    return render("dashboard.html", stats=stats, totals=totals, series=series, mx=mx, tab="dashboard")


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


@app.post("/users/new")
@need("owner")
def user_new():
    f = request.form
    name, email, role = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("role", "member")
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
    set_password(db(), db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"], pw)
    db().commit()
    audit("user_created", f"{username} ({role})")
    session["new_creds"] = creds_note(name, username, pw)
    return redirect(url_for("users"))


@app.route("/users/<int:uid>")
@need()
def user_detail(uid):
    u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() or abort(404)
    st = next(s for s in user_stats(30) if s["u"]["id"] == uid)
    events = db().execute("SELECT * FROM events WHERE user_id=? ORDER BY at DESC, id DESC LIMIT 60", (uid,)).fetchall()
    devices = db().execute("SELECT * FROM devices WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    return render("user.html", u=dict(u), st=st, events=events, devices=devices, tab="users", new_creds=session.pop("new_creds", None))


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
        db().execute("UPDATE users SET name=?, role=?, daily_cap=?, weekly_target=?, approval=?, note=? WHERE id=?",
                     (f.get("name", u["name"]).strip() or u["name"], role, _valid_int(f.get("daily_cap")), _valid_int(f.get("weekly_target")),
                      "master" if f.get("approval") == "master" else "member", f.get("note", "")[:300], uid))
        audit("user_changed", f"{u['email']}: role={role} cap={f.get('daily_cap')} target={f.get('weekly_target')} approval={f.get('approval')}")
    elif action in ("disable", "enable"):
        if u["role"] == "owner":
            abort(400, "The owner can't be disabled.")
        new = "disabled" if action == "disable" else ("active" if db().execute("SELECT 1 FROM devices WHERE user_id=?", (uid,)).fetchone() or u["role"] != "member" else "invited")
        db().execute("UPDATE users SET status=? WHERE id=?", (new, uid))
        audit("user_" + action + "d", u["email"])
    elif action == "resetpw":
        pw = f.get("password", "") or gen_password()
        if not strong(pw):
            return redirect(url_for("user_detail", uid=uid, err="Password must be at least 10 characters (or leave blank to generate one)."))
        set_password(db(), uid, pw, sign_out=True)   # a new password signs the old laptops out
        session["new_creds"] = creds_note(u["name"], u["username"] or u["email"], pw)
        audit("password_reset", u["email"])
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
    return render("settings.html", msg=msg, smtp=smtp_conf(), relay_url=meta_get("relay_url", ""), relay_ok=meta_get("relay_last_ok", ""),
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
    if u["status"] == "disabled":
        return jsonify(error="This account is paused. Contact your manager."), 403
    throttle_clear(k1)
    token = secrets.token_urlsafe(32)
    device = str(j.get("device", "laptop"))[:80]
    db().execute("UPDATE devices SET revoked=1 WHERE user_id=? AND name=? AND revoked=0", (u["id"], device))
    db().execute("INSERT INTO devices(user_id, token_hash, name, created_at, last_seen) VALUES(?,?,?,?,?)",
                 (u["id"], sha(token), device, iso(), iso()))
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
    for e in (j.get("events") or [])[:500]:
        try:
            con.execute("INSERT OR IGNORE INTO events(user_id, local_id, at, type, detail, sender, lead_email, company, step, variant) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uid, int(e["id"]), str(e.get("at", iso()))[:25], str(e.get("type", ""))[:30], str(e.get("detail", ""))[:300],
                         str(e.get("sender") or "")[:120], str(e.get("lead_email") or "")[:200], str(e.get("company") or "")[:200],
                         e.get("step"), str(e.get("variant") or "")[:80]))
            n_ev += 1
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
    for lid in (j.get("acked") or [])[:500]:
        try:
            con.execute("UPDATE drafts SET delivered=1 WHERE user_id=? AND local_id=? AND status!='pending'", (uid, int(lid)))
        except (ValueError, TypeError):
            pass
    return n_ev


def decisions_for(con, uid):
    return [{"local_id": d["local_id"], "action": "approve" if d["status"] == "approved" else "reject",
             "subject": d["subject"], "body": d["body"], "by": d["decided_by"]}
            for d in con.execute("SELECT * FROM drafts WHERE user_id=? AND status!='pending' AND delivered=0", (uid,)).fetchall()]


@app.post("/api/v1/sync")
def api_sync():
    u, dev = api_user()
    if not u:
        return jsonify(error="unauthorized"), 401
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


def relay_doc(con):
    users = {}
    for u in con.execute("SELECT * FROM users").fetchall():
        if not u["relay_pw"]:
            continue
        rec = json.loads(u["relay_pw"])
        users[u["username"] or u["email"]] = dict(
            rec, name=u["name"], email=u["email"], role=u["role"], status="disabled" if u["status"] == "disabled" else "active",
            tv=u["tv"] or 1, daily_cap=u["daily_cap"] or 0, weekly_target=u["weekly_target"] or 0, approval=u["approval"] or "member",
            decisions=decisions_for(con, u["id"]))
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
                    for it in res.get("items") or []:
                        body = it.get("body") or {}
                        u = con.execute("SELECT id FROM users WHERE username=? OR email=?", (body.get("user"), body.get("user"))).fetchone()
                        if u:
                            ingest(con, u["id"], body.get("data") or {})
                            got += 1
                        done.append(it["id"])
                    con.commit()
                    for name, seen in (res.get("seen") or {}).items():
                        at = str(seen.get("at", ""))[:19] + "Z"
                        con.execute("UPDATE users SET last_seen=?, app_version=COALESCE(NULLIF(?, ''), app_version), "
                                    "status=CASE WHEN status='invited' THEN 'active' ELSE status END "
                                    "WHERE (username=? OR email=?) AND (last_seen IS NULL OR last_seen<?)",
                                    (at, str(seen.get("version", ""))[:20], name, name, at))
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
    while True:
        time.sleep(RELAY_EVERY)
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
<small>{{ me.name }} · {{ me.role }}</small><form method="post" action="{{ url_for('logout') }}" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" style="padding:4px 10px">Sign out</button></form></div>
<div class="nav">{% for k, l, ep in [('dashboard','Overview','dashboard'),('users','Users','users'),('approvals','Approvals','approvals'),('activity','Activity','activity')] + ([('audit','Audit log','audit_page'),('settings','Settings','hub_settings')] if me.role == 'owner' else []) %}<a class="{{ 'on' if tab == k }}" href="{{ url_for(ep) }}">{{ l }}</a>{% endfor %}</div>{% endif %}
<div class="wrap">{% if request.args.get('err') %}<div class="err">{{ request.args.get('err') }}</div>{% endif %}{% block body %}{% endblock %}</div><script src="/hub.js"></script></body></html>"""

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
T["dashboard.html"] = """{% extends 'base.html' %}{% block body %}
<div class="stats"><div class="stat"><small>Sent (7 days)</small><div>{{ totals.sent }}</div></div><div class="stat"><small>Replies (7 days)</small><div>{{ totals.replies }}</div></div>
<div class="stat"><small>Interested replies</small><div>{{ totals.interested }}</div></div><div class="stat"><small>Drafts awaiting approval</small><div><a href="{{ url_for('approvals') }}">{{ totals.pending }}</a></div></div>
<div class="stat"><small>Laptops online now</small><div>{{ totals.online }}</div></div></div>
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
<div><label>Role</label><select name="role"><option value="member">Member (uses BSO on their laptop)</option><option value="manager">Manager (member + can use this console)</option></select></div>
<div><label>Password (10+ characters; blank = generate one)</label><input name="password" autocomplete="off"></div>
<div><label>Daily send cap (0 = no cap)</label><input name="daily_cap" type="number" min="0" value="0"></div><div><label>Weekly target (emails, 0 = none)</label><input name="weekly_target" type="number" min="0" value="0"></div>
<div><label>Who approves their emails</label><select name="approval"><option value="member">They approve their own</option><option value="master">The master approves every email</option></select></div></div>
<p><button class="btn brand">Create user</button></p></form></div></div>{% endif %}{% endblock %}"""
T["user.html"] = """{% extends 'base.html' %}{% block title %}{{ u.name }}{% endblock %}{% block body %}{% include '_invite.html' %}
<p><a href="{{ url_for('users') }}">← All users</a></p>
<div class="stats"><div class="stat"><small>Sent (30 days)</small><div>{{ st.sent }}</div></div><div class="stat"><small>Replies</small><div>{{ st.replies }}</div></div><div class="stat"><small>Reply rate</small><div>{{ st.rate }}</div></div>
<div class="stat"><small>Interested</small><div>{{ st.interested }}</div></div><div class="stat"><small>App version</small><div style="font-size:18px">{{ u.app_version or '–' }}</div></div></div>
{% if me.role == 'owner' %}<div class="card"><div class="card-h"><h2>{{ u.name }} <span class="badge {{ 'b-ok' if u.status == 'active' else 'b-bad' if u.status == 'disabled' else 'b-warn' }}">{{ u.status }}</span></h2></div><div class="card-b">
<form method="post"><input type="hidden" name="_csrf" value="{{ csrf }}"><input type="hidden" name="action" value="save"><div class="grid">
<div><label>Name</label><input name="name" value="{{ u.name }}"></div><div><label>Email</label><input value="{{ u.email }}" disabled></div><div><label>Sign-in ID</label><input value="{{ u.username or u.email }}" disabled></div>
<div><label>Role</label><select name="role" {{ 'disabled' if u.role == 'owner' }}>{% for r in ['member','manager'] %}<option {{ 'selected' if u.role == r }}>{{ r }}</option>{% endfor %}{% if u.role == 'owner' %}<option selected>owner</option>{% endif %}</select></div>
<div><label>Daily send cap (0 = no cap)</label><input name="daily_cap" type="number" min="0" value="{{ u.daily_cap or 0 }}"></div><div><label>Weekly target</label><input name="weekly_target" type="number" min="0" value="{{ u.weekly_target or 0 }}"></div>
<div><label>Who approves their emails</label><select name="approval"><option value="member" {{ 'selected' if u.approval != 'master' }}>They approve their own</option><option value="master" {{ 'selected' if u.approval == 'master' }}>The master approves every email</option></select></div>
</div>
<label>Private note</label><input name="note" value="{{ u.note or '' }}"><p><button class="btn brand">Save</button></p></form>
<form method="post" style="display:inline-flex;gap:6px;align-items:center" onsubmit="return confirm('Set a new password? Their laptops are signed out and must sign in again.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><input name="password" placeholder="new password (blank = generate)" autocomplete="off" style="width:240px"><button class="btn" name="action" value="resetpw">Reset password</button></form>
<form method="post" style="display:inline" onsubmit="return confirm('Disconnect all of this user\\'s laptops? They must sign in again.')"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn danger" name="action" value="revoke">Disconnect laptops</button></form>
{% if u.role != 'owner' %}<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}">{% if u.status == 'disabled' %}<button class="btn" name="action" value="enable">Resume account</button>{% else %}<button class="btn danger" name="action" value="disable" onclick="return confirm('Pause this account? Their BSO stops sending at the next check-in.')">Pause account</button>{% endif %}</form>{% endif %}</div></div>{% endif %}
<div class="card"><div class="card-h"><h2>Laptops</h2></div><table><tr><th>Name</th><th>Joined</th><th>Last seen</th><th></th></tr>{% for d in devices %}<tr><td>{{ d.name }}</td><td>{{ d.created_at|dt }}</td><td>{{ d.last_seen|ago }}</td><td>{{ 'disconnected' if d.revoked }}</td></tr>{% else %}<tr><td colspan="4" class="muted">No laptop has joined yet.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Recent activity</h2></div><table><tr><th>When</th><th>Event</th><th>Lead</th><th>Detail</th></tr>{% for e in events %}<tr><td class="muted">{{ e.at|dt }}</td><td>{{ e.type }}</td><td>{{ e.lead_email }}{% if e.company %}<br><span class="muted">{{ e.company }}</span>{% endif %}</td><td>{{ e.detail }}</td></tr>{% else %}<tr><td colspan="4" class="muted">Nothing reported yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["approvals.html"] = """{% extends 'base.html' %}{% block title %}Approvals{% endblock %}{% block body %}
<div class="note">These are emails from users whose approval is set to <b>The master approves every email</b>. An approved email is sent from the user's own inbox the next time their BSO checks in and runs.</div>
{% for d in pend %}<div class="card" id="d{{ d.id }}"><div class="card-h"><h2>{{ d.uname }} → {{ d.lead_email }}</h2><span class="muted">{{ d.company }} · {{ 'First email' if (d.step or 1) == 1 else 'Follow-up ' ~ (d.step - 1) }} · from {{ d.sender }}</span></div><div class="card-b">
<form method="post" action="{{ url_for('approval_action', did=d.id) }}"><input type="hidden" name="_csrf" value="{{ csrf }}"><label>Subject</label><input name="subject" value="{{ d.subject }}" {{ 'readonly' if (d.step or 1) > 1 }}><label>Body</label><textarea name="body">{{ d.body }}</textarea>
<p><button class="btn brand" name="action" value="approve">✓ Approve</button> <button class="btn" name="action" value="save">Save edits</button> <button class="btn danger" name="action" value="reject">Reject</button></p></form></div></div>
{% else %}<div class="card"><div class="card-b muted">Nothing is waiting for approval.</div></div>{% endfor %}
{% if done %}<div class="card"><div class="card-h"><h2>Recently decided</h2></div><table>{% for d in done %}<tr><td>{{ d.uname }}</td><td>{{ d.lead_email }}</td><td><span class="badge {{ 'b-ok' if d.status == 'approved' else 'b-bad' }}">{{ d.status }}</span></td><td class="muted">{{ d.decided_by }} · {{ d.decided_at|dt }}</td><td class="muted">{{ 'delivered' if d.delivered else 'waiting for their laptop' }}</td></tr>{% endfor %}</table></div>{% endif %}{% endblock %}"""
T["activity.html"] = """{% extends 'base.html' %}{% block title %}Activity{% endblock %}{% block body %}
<form method="get" class="card"><div class="card-b grid"><div><label>User</label><select name="user"><option value="">Everyone</option>{% for u in ulist %}<option value="{{ u.id }}" {{ 'selected' if uid == u.id }}>{{ u.name }}</option>{% endfor %}</select></div>
<div><label>Event</label><select name="type"><option value="">All</option>{% for t in types %}<option {{ 'selected' if typ == t }}>{{ t }}</option>{% endfor %}</select></div><div style="align-self:end"><button class="btn brand">Filter</button></div></div></form>
<div class="card"><table><tr><th>When</th><th>User</th><th>Event</th><th>Lead</th><th>Inbox</th><th>Detail</th></tr>{% for e in rows %}<tr><td class="muted">{{ e.at|dt }}</td><td>{{ e.uname }}</td><td>{{ e.type }}</td><td>{{ e.lead_email }}{% if e.company %}<br><span class="muted">{{ e.company }}</span>{% endif %}</td><td class="muted">{{ e.sender }}</td><td>{{ e.detail }}</td></tr>{% else %}<tr><td colspan="6" class="muted">No activity yet.</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["audit.html"] = """{% extends 'base.html' %}{% block title %}Audit log{% endblock %}{% block body %}<div class="card"><div class="card-h"><h2>Who changed what in this console</h2></div><table><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th></tr>{% for r in rows %}<tr><td class="muted">{{ r.at|dt }}</td><td>{{ r.actor }}</td><td>{{ r.action }}</td><td>{{ r.detail }}</td></tr>{% endfor %}</table></div>{% endblock %}"""
T["settings.html"] = """{% extends 'base.html' %}{% block title %}Settings{% endblock %}{% block body %}{% if msg %}<div class="note">{{ msg }}</div>{% endif %}
<div class="card"><div class="card-h"><h2>Team connection through your Google Drive</h2>{% if relay_url and relay_ok and not relay_err %}<span class="badge b-ok">Connected</span>{% elif relay_url %}<span class="badge b-bad">Problem</span>{% else %}<span class="badge b-warn">Not set up</span>{% endif %}</div><div class="card-b">
{% if relay_url %}<p style="margin-top:0">Team laptops sign in and report through <b>{{ relay_url }}</b>. Last sync: {{ relay_ok|dt if relay_ok else 'never' }}.{% if relay_err %}<br><span style="color:#ba0517">{{ relay_err }}</span>{% endif %}</p>
<form method="post" style="display:inline"><input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn" name="action" value="relay_sync">Sync now</button></form>
<details style="margin-top:12px"><summary>Set up again or change the address</summary>{% endif %}
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
    ap.add_argument("--reset-owner-password", action="store_true", help="locked out and email isn't set up? Set a new owner password from this computer")
    a = ap.parse_args(argv)
    d, key = prepare(a.data)
    if a.owner_email:
        _c = sqlite3.connect(str(DB_PATH)); _c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('owner_email',?)", (a.owner_email.strip().lower(),)); _c.commit(); _c.close()
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
