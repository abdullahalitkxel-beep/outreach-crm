"""Sending safety: no double sends, replies read before sending, one-click unsubscribe, backups that are proven to restore.

Send journal (sendlog.db, next to the main database)
    Every email that goes out with a key (a campaign step, a check-back email, an information answer) is written
    down with its Message-ID *before* BSO talks to the mail server, and marked sent afterwards. If the laptop dies
    in between, the row stays "sending". Before the next round, reconcile() looks in that inbox's Sent folder:
    found -> sent; not found on a host that keeps a copy of everything sent (Google, Microsoft) -> it never left,
    so it may go again; not found elsewhere -> "unconfirmed", treated as sent (a missed email costs less than a
    double one). A key that is already sent returns the first Message-ID instead of sending again.

Replies first
    An inbox only sends cold email once its replies were read successfully in the last READ_FRESH_MIN minutes.
    After an outage that means every inbox is read (stop requests included) before anything goes out.

One-click unsubscribe
    Cold emails carry List-Unsubscribe with a private link on the team's Google relay (version 5 or later) plus a
    mailto: fallback, and List-Unsubscribe-Post for Gmail and Yahoo's one-click button. The link holds a random
    token, never the address; only this laptop knows which address a token belongs to.

Backups
    Once a day the database is copied (SQLite online backup), checked (integrity, the copy opens, row counts match)
    and, when a backup folder is set (for example a Google Drive for desktop folder), a compressed copy sealed with
    the team key is written there so it survives the laptop. restore() puts a backup back.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

READ_FRESH_MIN = 45
SENDING_STALE_MIN = 2
KEEP_BACKUPS = 28                 # every 6 hours: a week of copies
SAVES_SENT = ("gmail.com", "googlemail.com", "google.com", "office365.com", "outlook.com", "office.com", "hotmail.com", "live.com")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data():
    import bot
    return Path(bot.DATA)


# ---------------------------------------------------------------- send journal

def _log_db():
    c = sqlite3.connect(str(_data() / "sendlog.db"), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS sends (key TEXT PRIMARY KEY, msg_id TEXT, sender TEXT, to_ TEXT, subject TEXT, "
              "status TEXT, at TEXT, done_at TEXT, note TEXT)")
    return c


def before_send(key, msg_id, sender_email, to, subject):
    """Write the intent down. Returns the earlier Message-ID if this key was already sent (then don't send again)."""
    c = _log_db()
    try:
        row = c.execute("SELECT * FROM sends WHERE key=?", (key,)).fetchone()
        if row and row["status"] in ("sent", "unconfirmed"):
            return row["msg_id"]
        if row and row["status"] == "sending":
            raise RuntimeError("an earlier attempt of this email was cut off; BSO is checking the Sent folder before trying again")
        c.execute("INSERT INTO sends(key, msg_id, sender, to_, subject, status, at) VALUES (?,?,?,?,?,'sending',?) "
                  "ON CONFLICT(key) DO UPDATE SET msg_id=excluded.msg_id, sender=excluded.sender, to_=excluded.to_, "
                  "subject=excluded.subject, status='sending', at=excluded.at, done_at=NULL, note=NULL",
                  (key, msg_id, sender_email, to, (subject or "")[:200], _iso(_now())))
        c.commit()
        return None
    finally:
        c.close()


def after_send(key, ok, note=None):
    c = _log_db()
    try:
        c.execute("UPDATE sends SET status=?, done_at=?, note=? WHERE key=?", ("sent" if ok else "failed", _iso(_now()), note, key))
        c.commit()
    finally:
        c.close()


def pending():
    c = _log_db()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM sends WHERE status='sending' AND at<?",
                                           (_iso(_now() - timedelta(minutes=SENDING_STALE_MIN)),))]
    finally:
        c.close()


def saves_sent(sender):
    h = f"{sender.get('smtp_host', '')} {sender.get('imap_host', '')}".lower()
    return any(x in h for x in SAVES_SENT)


def _in_sent_folder(sender, msg_ids):
    """Which of these Message-IDs are in the inbox's Sent folder."""
    import imaplib
    import mailbox as mb
    found = set()
    M = imaplib.IMAP4_SSL(sender["imap_host"], sender.get("imap_port", 993), timeout=30)
    try:
        M.login(sender.get("username", sender["email"]), sender["password"])
        folder = mb._sent_folder(M)
        if not folder:
            return found, False
        typ, _ = M.select(folder, readonly=True)
        if typ != "OK":
            return found, False
        for mid in msg_ids:
            typ, data = M.search(None, "HEADER", "Message-ID", f'"{mid}"')
            if typ == "OK" and data and data[0].split():
                found.add(mid)
        return found, True
    finally:
        try:
            M.logout()
        except Exception:
            pass


def reconcile(cfg, senders, look=None):
    """Settle every email whose sending was cut off. Returns lines for the run log."""
    rows = pending()
    if not rows:
        return []
    look = look or _in_sent_folder
    by = {s["email"].lower(): s for s in senders}
    lines = []
    groups = {}
    for r in rows:
        groups.setdefault((r["sender"] or "").lower(), []).append(r)
    c = _log_db()
    try:
        for email, items in groups.items():
            s = by.get(email)
            if not s or not s.get("password"):
                continue                                             # can't look yet: those keys stay blocked
            try:
                found, looked = look(s, [r["msg_id"] for r in items])
            except Exception as e:
                lines.append(f"Couldn't check the Sent folder of {email} ({e}); {len(items)} interrupted email(s) wait.")
                continue
            for r in items:
                if r["msg_id"] in found:
                    st, note = "sent", "found in the Sent folder after an interruption"
                elif looked and saves_sent(s):
                    st, note = "failed", "not in the Sent folder after an interruption: it never left"
                else:
                    st, note = "unconfirmed", "interrupted; this host doesn't keep sent copies, so it counts as sent (no double email)"
                c.execute("UPDATE sends SET status=?, done_at=?, note=? WHERE key=?", (st, _iso(_now()), note, r["key"]))
                lines.append(f"Interrupted email to {r['to_']} from {email}: {note}.")
        c.commit()
    finally:
        c.close()
    return lines


# ---------------------------------------------------------------- replies are read before sending

def ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS inbox_read (email TEXT PRIMARY KEY, ok_at TEXT, fail_at TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS unsub_tokens (token TEXT PRIMARY KEY, email TEXT UNIQUE, at TEXT);
    CREATE TABLE IF NOT EXISTS msg_pending (key TEXT PRIMARY KEY, at TEXT, tries INTEGER DEFAULT 0, inbox TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS app_mode (k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS dnc_reported (email TEXT PRIMARY KEY);
    """)
    ensure_breaker(con)
    if not con.execute("SELECT 1 FROM app_mode WHERE k='recovered_errors_v1'").fetchone():
        # Earlier versions marked an email done even when handling it failed (a stop request could be lost that way).
        # Those emails are read again: they are taken out of the "already seen" list once.
        n = con.execute("DELETE FROM seen_messages WHERE msg_id IN (SELECT msg_id FROM inbox_log WHERE decision LIKE 'error:%' "
                        "AND msg_id IS NOT NULL) AND msg_id NOT IN (SELECT msg_id FROM received WHERE msg_id IS NOT NULL)").rowcount
        con.execute("INSERT OR REPLACE INTO app_mode(k, v) VALUES ('recovered_errors_v1', ?)", (f"{_iso(_now())} {n} email(s) to read again",))


def read_ok(con, email):
    con.execute("INSERT INTO inbox_read(email, ok_at) VALUES (?,?) ON CONFLICT(email) DO UPDATE SET ok_at=excluded.ok_at, error=NULL",
                (email.lower(), _iso(_now())))


def read_fail(con, email, err):
    con.execute("INSERT INTO inbox_read(email, fail_at, error) VALUES (?,?,?) ON CONFLICT(email) DO UPDATE SET fail_at=excluded.fail_at, "
                "error=excluded.error", (email.lower(), _iso(_now()), str(err)[:300]))


def unread(con, senders, minutes=READ_FRESH_MIN):
    """{email: why} for inboxes whose replies haven't been read recently. They don't send cold email until they are."""
    cut = _iso(_now() - timedelta(minutes=minutes))
    rows = {r["email"]: r for r in con.execute("SELECT * FROM inbox_read")}
    pending = {(r[0] or "").lower() for r in con.execute("SELECT DISTINCT inbox FROM msg_pending")}
    out = {}
    for s in senders:
        e = s["email"].lower()
        r = rows.get(e)
        if e in pending:
            out[e] = "an email in it (perhaps a stop request) is still being handled; it sends once that is done"
            continue
        if r and r["ok_at"] and r["ok_at"] >= cut and (not r["fail_at"] or r["fail_at"] < r["ok_at"]):
            continue
        why = f"its replies couldn't be read ({r['error']})" if r and r["error"] else "its replies haven't been read yet"
        out[e] = why + "; it sends once they are, so no stop request is missed"
    return out


# ---------------------------------------------------------------- one-click unsubscribe

def _token(key, email):
    import base64
    import hashlib
    import hmac
    return base64.urlsafe_b64encode(hmac.new(key, b"unsub:" + email.encode(), hashlib.sha256).digest()).decode()[:22]


def token_for(con, email):
    """The unsubscribe code for an address. With the team key it is derived from the address, so any of the team's
    laptops (a restored or replacement bot included) can tell who unsubscribed; it still reveals nothing by itself."""
    email = email.lower().strip()
    r = con.execute("SELECT token FROM unsub_tokens WHERE email=?", (email,)).fetchone()
    if r:
        return r[0]
    key = _team_key()
    t = _token(key, email) if key else secrets.token_urlsafe(16)
    con.execute("INSERT OR IGNORE INTO unsub_tokens(token, email, at) VALUES (?,?,?)", (t, email, _iso(_now())))
    return con.execute("SELECT token FROM unsub_tokens WHERE email=?", (email,)).fetchone()[0]


def relay_url():
    """The team relay's address when it can take unsubscribes (relay version 5+), else None."""
    try:
        import relay
        import team
        s = team.state()
        if team.joined() and relay.is_relay(s.get("hub")) and int(s.get("relay_version") or 0) >= 5:
            return s["hub"].split("?")[0]
    except Exception:
        pass
    return None


def unsub_headers(con, sender_email, lead_email):
    mailto = f"<mailto:{sender_email}?subject=unsubscribe>"
    url = relay_url()
    if not url:
        return {"List-Unsubscribe": mailto}
    return {"List-Unsubscribe": f"<{url}?u={token_for(con, lead_email)}>, {mailto}",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def apply_unsubs(con, items):
    """Unsubscribes collected by the relay. Tokens this laptop didn't issue are ignored. Returns addresses stopped."""
    import bot
    done = []
    derived = None
    for it in items or []:
        t = str((it or {}).get("t") or "")
        if not TOKEN_RE.match(t):
            continue
        r = con.execute("SELECT email FROM unsub_tokens WHERE token=?", (t,)).fetchone()
        email = r[0] if r else None
        if not email:                      # issued by another of the team's laptops, or before a restore: derive it
            key = _team_key()
            if key and derived is None:
                derived = {_token(key, (e or "").lower()): (e or "").lower()
                           for (e,) in con.execute("SELECT email FROM leads UNION SELECT email FROM suppression").fetchall() if e}
            email = (derived or {}).get(t)
        if not email:
            continue
        if con.execute("SELECT 1 FROM suppression WHERE email=?", (email,)).fetchone():
            continue
        bot.suppress(con, email, "unsubscribe link")
        for l in con.execute("SELECT id FROM leads WHERE lower(email)=?", (email,)).fetchall():
            con.execute("UPDATE leads SET status='unsubscribed' WHERE id=?", (l[0],))
            bot.cancel_drafts(con, l[0])
            bot.log_event(con, l[0], "unsubscribed", "one-click unsubscribe link")
        done.append(email)
    con.commit()
    return done


# ---------------------------------------------------------------- backups that are proven to restore

COUNT_TABLES = ("leads", "sent_log", "received", "suppression", "events", "notes", "campaigns")


def _counts(c):
    out = {}
    for t in COUNT_TABLES:
        try:
            out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            pass
    return out


def _fast_seal(key, data):
    import sealbox
    return sealbox.seal(key, data)


def _team_key():
    try:
        import team
        return team._team_key()
    except Exception:
        return None


def backup(con, folder=None, key=None):
    """Copy the database, prove the copy works, and (with a folder and the team key) store a sealed copy off the laptop.
    Returns a dict for the run log and the console."""
    import bot
    out_dir = _data() / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = out_dir / f"daily-{stamp}.db"
    con.commit()
    b = sqlite3.connect(str(dest))
    try:
        con.backup(b)
    finally:
        b.close()
    live = _counts(con)
    res = {"at": _iso(_now()), "file": dest.name, "size_mb": round(dest.stat().st_size / 1e6, 1)}
    ok, detail = check_copy(dest, live)
    res.update(ok=ok, detail=detail)
    for old in sorted(out_dir.glob("daily-*.db"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    key = key if key is not None else _team_key()
    if folder:
        try:
            if not key:
                raise RuntimeError("sign this BSO in to the team first: the off-laptop copy is sealed with the team key")
            f = Path(folder)
            f.mkdir(parents=True, exist_ok=True)
            sealed = _fast_seal(key, zlib.compress(dest.read_bytes(), 6))
            tmp = f / f"BSO-backup-{stamp}.bsobak.part"
            tmp.write_text(sealed, encoding="ascii")
            final = tmp.with_suffix("")
            tmp.replace(final)
            for old in sorted(f.glob("BSO-backup-*.bsobak"))[:-KEEP_BACKUPS]:
                old.unlink(missing_ok=True)
            back_ok, back_detail = check_sealed(final, key, live)
            res.update(offsite=final.name, offsite_ok=back_ok, offsite_detail=back_detail)
        except Exception as e:
            res.update(offsite_ok=False, offsite_detail=f"off-laptop copy failed: {e}")
    con.execute("INSERT INTO app_mode(k, v) VALUES ('backup_status', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(res),))
    con.commit()
    return res


def check_copy(path, live=None):
    """The restore test: the copy passes SQLite's integrity check, opens like BSO's own database, and holds the same rows."""
    tmpdir = Path(tempfile.mkdtemp(prefix="bso-restore-test-"))
    try:
        t = tmpdir / "outreach.db"
        shutil.copyfile(path, t)
        c = sqlite3.connect(str(t))
        try:
            ic = c.execute("PRAGMA integrity_check").fetchone()[0]
            if ic != "ok":
                return False, f"integrity check failed: {ic}"
            got = _counts(c)
        finally:
            c.close()
        if live is not None:
            short = [f"{k} {got.get(k, 0)}/{v}" for k, v in live.items() if got.get(k, 0) < v]
            if short:
                return False, "rows missing in the copy: " + ", ".join(short)
        return True, f"restore test passed ({got.get('leads', 0)} leads, {got.get('sent_log', 0)} sent emails, {got.get('received', 0)} replies)"
    except Exception as e:
        return False, f"restore test failed: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def open_sealed(path, key):
    import sealbox
    return zlib.decompress(sealbox.open_(key, Path(path).read_text(encoding="ascii")))


def check_sealed(path, key, live=None):
    try:
        raw = open_sealed(path, key)
    except Exception as e:
        return False, f"the sealed copy can't be opened ({e})"
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        Path(tmp).write_bytes(raw)
        return check_copy(tmp, live)
    finally:
        os.unlink(tmp)


def restore(src, key=None, now=True):
    """Put a backup back as this laptop's database (the current one is kept aside). src is a .db copy or a sealed .bsobak.
    now=False (the app, which has the database open): the checked copy waits as outreach.restore.db and is swapped in
    by apply_pending_restore() the next time BSO starts, before anything opens the database."""
    src = Path(src)
    data = _data()
    if src.suffix == ".bsobak":
        key = key if key is not None else _team_key()
        if not key:
            raise RuntimeError("Sign in to the team first: this backup is sealed with the team key.")
        raw = open_sealed(src, key)
    else:
        raw = src.read_bytes()
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=str(data))
    os.close(fd)
    Path(tmp).write_bytes(raw)
    ok, detail = check_copy(tmp)
    if not ok:
        os.unlink(tmp)
        raise RuntimeError("That backup didn't pass the restore test: " + detail)
    if not now:
        os.replace(tmp, data / "outreach.restore.db")
        return detail
    _swap_in(tmp)
    return detail + "; " + settle_gap()


def apply_pending_restore():
    """Called when BSO starts: a restore chosen in the app is put in place before the database is opened."""
    p = _data() / "outreach.restore.db"
    if p.exists():
        _swap_in(str(p))
        print("restore:", settle_gap())
        return True
    return False


def settle_gap():
    """A backup is up to a few hours old: emails sent after it aren't in it. So after a restore, sending stays stopped
    until a person starts it, and every lead that was due between the backup and now is settled the cautious way:
      - follow-ups that were due are counted as sent (a missed follow-up costs less than a repeated one);
      - first emails that were due are held for a person to release (a follow-up must never arrive without a first email)."""
    c = sqlite3.connect(str(_data() / "outreach.db"))
    c.row_factory = sqlite3.Row
    try:
        last = c.execute("SELECT MAX(at) FROM events").fetchone()[0] or _iso(_now())
        now = _iso(_now())
        nxt = _now()
        for _ in range(3):
            nxt += timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
        ups = c.execute("SELECT id FROM leads WHERE status='active' AND step>0 AND next_send_at>? AND next_send_at<=?", (last[:19], now)).fetchall()
        firsts = c.execute("SELECT id FROM leads WHERE status='active' AND step=0 AND next_send_at>? AND next_send_at<=?", (last[:19], now)).fetchall()
        for r in ups:
            c.execute("UPDATE leads SET step=step+1, next_send_at=? WHERE id=?", (_iso(nxt), r["id"]))
            c.execute("INSERT INTO events(lead_id, type, detail, at) VALUES (?, 'restored', 'follow-up due while the bot was down: counted as sent', ?)",
                      (r["id"], now))
        c.execute("CREATE TABLE IF NOT EXISTS breakers (id INTEGER PRIMARY KEY, kind TEXT, key TEXT, at TEXT, reason TEXT, resumed_at TEXT, resumed_by TEXT)")
        if firsts:
            key = "restore-" + now[:16]
            c.executemany("UPDATE leads SET status='held', batch=COALESCE(batch, ?) WHERE id=?", [(key, r["id"]) for r in firsts])
            c.execute("CREATE TABLE IF NOT EXISTS restore_held (lead_id INTEGER PRIMARY KEY, key TEXT)")
            c.executemany("INSERT OR REPLACE INTO restore_held(lead_id, key) VALUES (?,?)", [(r["id"], key) for r in firsts])
            c.execute("INSERT INTO breakers(kind, key, at, reason) VALUES ('restore', ?, ?, ?)",
                      (key, now, f"{len(firsts)} first email(s) were due while the bot was down; some may have gone out. Check the Sent "
                                  "folders, then resume to send the rest"))
        c.execute("INSERT INTO app_mode(k, v) VALUES ('kill', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  ("restored from a backup: check Status, then press Start sending " + now[:16],))
        try:                        # the backup was taken during a run: that run's "running" mark must not block every later run
            c.execute("UPDATE jobs SET status='failed', finished_at=? WHERE status='running'", (now,))
        except sqlite3.Error:
            pass
        c.commit()
        return (f"backup last activity {last[:16]}; {len(ups)} follow-up(s) due since then counted as sent, {len(firsts)} first email(s) held; "
                "sending stays stopped until you start it")
    finally:
        c.close()


def _swap_in(tmp):
    try:                               # a restored database may lack stops made since the backup: fetch the team's whole list again
        import team
        s = team.state()
        if s:
            s.pop("dnc_ready", None)
            s.pop("dnc_req_at", None)
            s["dnc_must_wait"] = True
            team.save_state(s)
    except Exception:
        pass
    data = _data()
    live = data / "outreach.db"
    if live.exists():
        keep = data / "backups" / f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        keep.parent.mkdir(exist_ok=True)
        a, b = sqlite3.connect(str(live)), sqlite3.connect(str(keep))
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
        for ext in ("-wal", "-shm"):
            _retry(lambda: Path(str(live) + ext).unlink(missing_ok=True))
    _retry(lambda: os.replace(tmp, live))


def _retry(fn, tries=10):
    """Windows: a file another program (an antivirus scan, a closing connection) holds for a moment can't be replaced yet."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * (i + 1))


def due_backup(con, hours=6):
    r = con.execute("SELECT v FROM app_mode WHERE k='backup_status'").fetchone()
    try:
        last = json.loads(r[0])["at"] if r else None
    except Exception:
        last = None
    return not last or last < _iso(_now() - timedelta(hours=hours))


def status(con):
    r = con.execute("SELECT v FROM app_mode WHERE k='backup_status'").fetchone()
    try:
        return json.loads(r[0]) if r else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- bounces: permanent or temporary

SOFT_LIMIT = 3
_SOFT = re.compile(r"(\b4\.\d{1,3}\.\d{1,3}\b|\b4[25]\d\b|mailbox (is )?full|over (the )?quota|quota exceeded|insufficient (storage|system storage)|"
                   r"temporar(y|ily)|try again later|deferred|delayed|greylist|rate limit|too many (connections|messages)|still trying)", re.I)
_HARD = re.compile(r"(\b5\.\d{1,3}\.\d{1,3}\b|\b55\d\b|user unknown|no such user|does not exist|doesn'?t exist|unknown (user|recipient)|"
                   r"recipient (address )?rejected|address (not found|rejected)|mailbox (unavailable|not found|disabled)|account (has been )?disabled|"
                   r"invalid (recipient|address|mailbox)|undeliverable|permanent(ly)? (fail|error))", re.I)


def bounce_kind(subject, body):
    """'hard' (the address is gone: stop now) or 'soft' (mailbox full, server busy: count it, stop after SOFT_LIMIT)."""
    text = f"{subject}\n{body}"[:6000]
    if _HARD.search(text):
        return "hard"
    if _SOFT.search(text):
        return "soft"
    return "hard"


# ---------------------------------------------------------------- the circuit breaker for campaigns and imported lists

BREAKER = {"min_sent": 50, "bounce_rate": 0.05, "complaints": 3, "complaint_rate": 0.003, "unsub_rate": 0.03, "days": 7}


def ensure_breaker(con):
    con.execute("CREATE TABLE IF NOT EXISTS breakers (id INTEGER PRIMARY KEY, kind TEXT, key TEXT, at TEXT, reason TEXT, resumed_at TEXT, resumed_by TEXT)")


def _since(con, kind, key, days):
    base = _iso(_now() - timedelta(days=days))
    r = con.execute("SELECT MAX(resumed_at) FROM breakers WHERE kind=? AND key=?", (kind, str(key))).fetchone()
    return max(base, r[0]) if r and r[0] else base


def _rates(con, lead_filter, args, since):
    q = lambda t: con.execute(f"SELECT COUNT(*) FROM events WHERE type=? AND at>? AND lead_id IN ({lead_filter})" +
                              (" AND detail NOT LIKE '%(test)%'" if t == "sent" else ""), (t, since, *args)).fetchone()[0]
    return q("sent"), q("bounce"), q("complaint"), q("unsubscribed")


def breaker_check(cfg, con, notify=None):
    """Pause a campaign or an imported list whose bounces, complaints or unsubscribes are too high. Judged only after
    BREAKER['min_sent'] emails. Pausing is automatic; resuming is a person's decision. Returns lines for the run log."""
    ensure_breaker(con)
    b = dict(BREAKER, **((cfg.get("guard") or {}).get("breaker") or {}))
    out = []

    def judge(sent, bounced, compl, unsub):
        if sent < int(b["min_sent"]):
            return None
        if bounced / sent >= float(b["bounce_rate"]):
            return f"{bounced} of {sent} emails bounced ({bounced * 100 / sent:.1f}%, limit {float(b['bounce_rate']) * 100:g}%)"
        if compl >= int(b["complaints"]) and compl / sent >= float(b["complaint_rate"]):
            return f"{compl} complaint replies (spam, legal) in {sent} emails"
        if unsub / sent >= float(b["unsub_rate"]):
            return f"{unsub} of {sent} people asked to stop ({unsub * 100 / sent:.1f}%, limit {float(b['unsub_rate']) * 100:g}%)"
        return None

    for c in con.execute("SELECT id, name FROM campaigns WHERE COALESCE(status,'active')='active'").fetchall():
        why = judge(*_rates(con, "SELECT id FROM leads WHERE COALESCE(campaign_id,1)=?", (c["id"],), _since(con, "campaign", c["id"], b["days"])))
        if why:
            con.execute("UPDATE campaigns SET status='paused' WHERE id=?", (c["id"],))
            con.execute("INSERT INTO breakers(kind, key, at, reason) VALUES ('campaign',?,?,?)", (str(c["id"]), _iso(_now()), why))
            out.append(f"Campaign \"{c['name']}\" paused by the safety breaker: {why}. Resume it by hand once the cause is fixed.")
    for r in con.execute("SELECT DISTINCT batch FROM leads WHERE batch IS NOT NULL AND status='active'").fetchall():
        bt = r[0]
        why = judge(*_rates(con, "SELECT id FROM leads WHERE batch=?", (bt,), _since(con, "list", bt, b["days"])))
        if why:
            n = con.execute("UPDATE leads SET status='held' WHERE batch=? AND status='active'", (bt,)).rowcount
            con.execute("INSERT INTO breakers(kind, key, at, reason) VALUES ('list',?,?,?)", (bt, _iso(_now()), why))
            out.append(f"Imported list {bt} paused by the safety breaker ({n} leads held): {why}. Check the list, then resume it by hand.")
    con.commit()
    for line in out:
        if notify:
            notify("⚠ " + line)
    return out


def open_breakers(con):
    ensure_breaker(con)
    return [dict(r) for r in con.execute("SELECT * FROM breakers WHERE resumed_at IS NULL ORDER BY id DESC")]


def resume_breaker(con, bid, who="owner"):
    ensure_breaker(con)
    r = con.execute("SELECT * FROM breakers WHERE id=? AND resumed_at IS NULL", (bid,)).fetchone()
    if not r:
        return False
    if r["kind"] == "campaign":
        con.execute("UPDATE campaigns SET status='active' WHERE id=?", (int(r["key"]),))
    elif r["kind"] == "restore":
        con.execute("UPDATE leads SET status='active' WHERE status='held' AND id IN (SELECT lead_id FROM restore_held WHERE key=?)", (r["key"],))
    else:
        con.execute("UPDATE leads SET status='active' WHERE batch=? AND status='held'", (r["key"],))
    con.execute("UPDATE breakers SET resumed_at=?, resumed_by=? WHERE id=?", (_iso(_now()), who, bid))
    con.commit()
    return True


# ---------------------------------------------------------------- an email that couldn't be handled

MAX_TRIES = 3


def message_failed(cfg, con, sender, key, msg, err):
    """Record a failed attempt. Returns True while it will be tried again; after MAX_TRIES it is settled the cautious
    way: if it reads like a stop request the sender is put on do-not-contact anyway, and a person gets a task."""
    import bot
    import guard
    from email.utils import parseaddr
    con.execute("INSERT INTO msg_pending(key, at, tries, inbox, error) VALUES (?,?,1,?,?) ON CONFLICT(key) DO UPDATE SET "
                "tries=tries+1, error=excluded.error", (key, _iso(_now()), sender["email"], str(err)[:300]))
    tries = con.execute("SELECT tries FROM msg_pending WHERE key=?", (key,)).fetchone()[0]
    if tries < MAX_TRIES:
        return True
    settle(cfg, con, sender, key, msg, err)
    return False


def settle(cfg, con, sender, key, msg, err):
    """An email that could never be handled: if it reads like a stop request the sender is stopped anyway; a person
    always gets a task to read it."""
    import bot
    import guard
    from email.utils import parseaddr
    con.execute("DELETE FROM msg_pending WHERE key=?", (key,))
    frm = parseaddr(guard.text(msg.get("From", "")))[1].lower()
    subj = guard.text(msg.get("Subject", ""))
    try:
        body = bot.get_full_text(msg)
    except Exception:
        body = ""
    stop = guard.stop_reason(subj, body) if frm else None
    if stop and frm:
        bot.suppress(con, frm, "stop request in an email BSO couldn't fully handle")
    bot.add_task(con, f"An email from {frm or 'an unknown sender'} in {sender['email']} couldn't be handled after {MAX_TRIES} tries"
                      f"{' (it reads like a stop request: all email to them is stopped)' if stop else ''}. Please read it: \"{subj[:80]}\"")
    try:
        import convo
        convo.log_inbox(con, sender["email"], msg, f"error after {MAX_TRIES} tries: {err}"[:200], None, "INBOX")
    except Exception:
        pass


# ---------------------------------------------------------------- the last check before an email goes out

def why_not_send(lead_id, statuses=("active",)):
    """Why this lead must not get an email right now (None = it may). Read fresh from the database, right before the
    mail server is contacted: a reply, an unsubscribe or a do-not-contact entry that arrived a moment ago counts."""
    c = sqlite3.connect(str(_db_path()), timeout=30)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT email, status FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not r:
            return "the lead no longer exists"
        if r["status"] not in statuses:
            return f"it is now {r['status'].replace('_', ' ')}"
        if c.execute("SELECT 1 FROM suppression WHERE email=?", ((r["email"] or "").lower(),)).fetchone():
            return "the address is on do-not-contact"
        try:
            import team
            joined = team.joined()
        except Exception:
            joined = False
        if joined and c.execute("SELECT 1 FROM sqlite_master WHERE name='team_dnc'").fetchone() and \
                c.execute("SELECT 1 FROM team_dnc WHERE h=?", (team.dnc_hash(r["email"]),)).fetchone():
            return "the address is on the team's do-not-contact list"
        return None
    finally:
        c.close()


def blocked_recipients(addrs):
    """Addresses among To/Cc/Bcc that are on do-not-contact (this laptop's list or the team's)."""
    import re as _re
    found = []
    for a in addrs:
        found += [e.lower() for e in _re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", str(a or ""))]
    if not found:
        return []
    c = sqlite3.connect(str(_db_path()), timeout=30)
    try:
        out = [e for e in found if c.execute("SELECT 1 FROM suppression WHERE email=?", (e,)).fetchone()]
        try:
            import team
            if team.joined() and c.execute("SELECT 1 FROM sqlite_master WHERE name='team_dnc'").fetchone():
                out += [e for e in found if e not in out and c.execute("SELECT 1 FROM team_dnc WHERE h=?", (team.dnc_hash(e),)).fetchone()]
        except sqlite3.Error:
            raise
        except Exception:
            pass
        return out
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise
    finally:
        c.close()


def _db_path():
    """The database this BSO uses (the 'database' setting, outreach.db by default)."""
    import bot
    return getattr(bot, "DB_FILE", None) or (_data() / "outreach.db")


# ---------------------------------------------------------------- taking a mistaken stop back

def unstop(con, email, who="owner"):
    """Remove an address from do-not-contact here and queue its removal from the team's list. The lead is set to
    paused: a person resumes it on purpose."""
    import bot
    import team
    email = (email or "").lower().strip()
    con.execute("DELETE FROM suppression WHERE email=?", (email,))
    con.execute("DELETE FROM dnc_reported WHERE email=?", (email,))
    h = team.dnc_hash(email)
    try:
        con.execute("DELETE FROM team_dnc WHERE h=?", (h,))
    except sqlite3.OperationalError:
        pass
    for (lid,) in con.execute("SELECT id FROM leads WHERE email=? AND status IN ('unsubscribed','bounced')", (email,)).fetchall():
        con.execute("UPDATE leads SET status='paused' WHERE id=?", (lid,))
        bot.log_event(con, lid, "unstopped", f"taken off do-not-contact by {who}")
    con.commit()
    try:
        s = team.state()
        if s:
            s["dnc_del_pending"] = sorted(set(s.get("dnc_del_pending") or []) | {h})
            team.save_state(s)
    except Exception:
        pass


def test_mode_now():
    """Whether this database is in test mode right now (read fresh)."""
    c = sqlite3.connect(str(_db_path()), timeout=30)
    try:
        r = c.execute("SELECT v FROM app_mode WHERE k='test'").fetchone()
        return bool(r and r[0] == "1")
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()
