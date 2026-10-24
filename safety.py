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
    Every 6 hours the database is copied (SQLite online backup) and checked (integrity, the copy opens, row counts
    match). Every backup file is encrypted wherever it is: the copies kept on this laptop (sealed with the team key, or
    with this laptop's own key when it isn't in a team; older plain copies are encrypted on the next backup), the
    copy kept aside before a restore or an update, and the copies that leave the laptop. A copy sealed with the team key leaves the laptop: to the owner's Google Drive through the
    team relay (version 6 or later, nothing to set up), read back and checked there, and also to a backup folder when
    one is set. restore() puts a backup back; drive_fetch() downloads one from Google Drive first.
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


def expire_pending(con):
    """An email that waited 24 hours without being seen again (deleted, moved): a person gets a task, it stops blocking."""
    import bot
    old = con.execute("SELECT key, inbox, error FROM msg_pending WHERE at<?", (_iso(_now() - timedelta(hours=24)),)).fetchall()
    for r in old:
        con.execute("DELETE FROM msg_pending WHERE key=?", (r[0],))
        bot.add_task(con, f"An email in {r[1]} couldn't be handled and has since disappeared from the inbox ({(r[2] or 'no error recorded')[:80]}). "
                          "Look for it in the mailbox (it may be a stop request).")
    return len(old)


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
    pending = {(r[0] or "").lower() for r in con.execute("SELECT DISTINCT inbox FROM msg_pending WHERE at>?",
                                                           (_iso(_now() - timedelta(hours=24)),))}
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
    """The team relay's address when it takes unsubscribes and the master switched the links on there (UNSUB_LINKS in
    the script: off by default, because the link prints the relay's address in every cold email), else None."""
    try:
        import relay
        import team
        s = team.state()
        if team.joined() and relay.is_relay(s.get("hub")) and int(s.get("relay_version") or 0) >= 5 and s.get("relay_unsub"):
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


MAGIC = b"BSOBAK1\n"          # sealed backups start with this, a line of JSON saying whose backup it is, then the database


def _origin():
    o = {"at": _iso(_now())}
    try:
        import team
        o["user"] = team.signed_in() or ""
        o["laptop"] = (team.device_info() or {}).get("name", "")
    except Exception:
        pass
    return o


def _pack(db_bytes, origin):
    return zlib.compress(MAGIC + json.dumps(origin).encode() + b"\n" + db_bytes, 6)


def _unpack(raw):
    """(origin, database bytes). Backups made before the origin was recorded have no header: origin is {}."""
    if raw.startswith(MAGIC):
        head, _, db = raw[len(MAGIC):].partition(b"\n")
        try:
            return json.loads(head.decode() or "{}"), db
        except ValueError:
            return {}, db
    return {}, raw


def _save_status(res, con=None):
    c = con or sqlite3.connect(str(_data() / "outreach.db"), timeout=30)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS app_mode (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO app_mode(k, v) VALUES ('backup_status', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(res),))
        c.commit()
    finally:
        if con is None:
            c.close()


# ---------------------------------------------------------------- every backup file is encrypted, wherever it is

LOCAL_KEY = "BSO_BACKUP_KEY"


def _local_key(create=True):
    """This laptop's own backup key (kept in the encrypted secret store), for a BSO not signed in to a team."""
    try:
        import bot
        k = bot.SECRETS.load().get(LOCAL_KEY)
        if not k and create:
            k = secrets.token_hex(32)
            bot.SECRETS.set(LOCAL_KEY, k)
        return bytes.fromhex(k) if k else None
    except Exception:
        return None


def _seal_key():
    """Backups on this laptop are sealed with the team key when there is one (a replacement laptop can open them),
    otherwise with this laptop's own key."""
    return _team_key() or _local_key()


def _open_keys():
    return [k for k in (_team_key(), _local_key(create=False)) if k]


def _write_sealed(dest, text):
    tmp = Path(str(dest) + ".part")
    tmp.write_text(text, encoding="ascii")
    _retry(lambda: os.replace(tmp, dest))


def seal_db_file(db_path, dest, origin=None):
    """Encrypt a database copy into dest (.bsobak) and delete the plain copy."""
    raw = Path(db_path).read_bytes()
    key = _seal_key()
    if not key:
        raise RuntimeError("no key to encrypt the backup with")
    _write_sealed(dest, _fast_seal(key, _pack(raw, origin or _origin())))
    _shred(db_path)
    return Path(dest)


def _shred(path):
    """Delete a plain database copy, overwriting it first so the old bytes don't linger on disk."""
    p = Path(path)
    try:
        n = p.stat().st_size
        with open(p, "r+b") as f:
            while n > 0:
                k = min(n, 1 << 20)
                f.write(b"\0" * k)
                n -= k
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    _retry(lambda: p.unlink(missing_ok=True))


def open_sealed_any(path):
    """(origin, database bytes) of a sealed backup, trying the team key and this laptop's own key."""
    text = Path(path).read_text(encoding="ascii")
    import sealbox
    for k in _open_keys():
        try:
            return _unpack(zlib.decompress(sealbox.open_(k, text)))
        except Exception:
            continue
    raise RuntimeError("That file isn't a BSO backup of this team or this laptop, or it is damaged.")


def encrypt_old_backups():
    """Backups made before 1.1 were plain database copies: encrypt them in place (once)."""
    d = _data() / "backups"
    done = 0
    for p in list(d.glob("*.db")) if d.exists() else []:
        try:
            seal_db_file(p, p.with_suffix(".bsobak"), {"at": _iso(datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)), "user": "", "converted": True})
            done += 1
        except Exception as e:
            print("encrypting an old backup:", p.name, e)
    return done


DRIVE_LIMIT_S = 300                 # a Google Drive copy gives up after 5 minutes; the next backup tries again


def backup(con, folder=None, key=None, manual=False, drive="background", on_drive_fail=None):
    """Copy the database, prove the copy works, then store sealed copies off the laptop: on Google Drive through the
    team relay (in the background by default, so a slow upload never holds up sending) and in a folder if one is set.
    Returns a dict for the run log and the console (the Drive result is added to the saved status when it finishes)."""
    import threading
    out_dir = _data() / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M%S")                # UTC with seconds: two backups never share a name
    encrypt_old_backups()
    plain = out_dir / f".daily-{stamp}.tmp"                  # plain only while it is tested, then encrypted
    con.commit()
    b = sqlite3.connect(str(plain))
    try:
        con.backup(b)
    finally:
        b.close()
    live = _counts(con)
    origin = _origin()
    res = {"at": _iso(_now()), "file": f"daily-{stamp}.bsobak", "size_mb": round(plain.stat().st_size / 1e6, 1), "manual": bool(manual)}
    try:
        ok, detail = check_copy(plain, live)
        raw = plain.read_bytes()
    finally:
        _shred(plain)
    res.update(ok=ok, detail=detail)
    try:                                                     # the copy kept on this laptop: encrypted too
        lkey = _seal_key()
        if not lkey:
            raise RuntimeError("no key to encrypt it with")
        local_sealed = _fast_seal(lkey, _pack(raw, origin))
        _write_sealed(out_dir / res["file"], local_sealed)
        if _check_sealed_text(local_sealed, lkey)[0] is False and ok:
            raise RuntimeError("the encrypted copy didn't open")
    except Exception as e:
        res.update(ok=False, detail=f"{detail}; the encrypted copy on this laptop failed: {e}")
        ok = False
    for old in sorted(out_dir.glob("daily-*.bsobak"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    key = key if key is not None else _team_key()
    sealed = None
    if (folder or drive_ready()) and key and ok:
        sealed = local_sealed if key == lkey else _fast_seal(key, _pack(raw, origin))
    del raw
    if folder:
        try:
            if not key:
                raise RuntimeError("sign this BSO in to the team first: the copy is sealed with the team key")
            if sealed is None:
                raise RuntimeError("not copied: this backup failed its restore test")
            f = Path(folder)
            f.mkdir(parents=True, exist_ok=True)
            tmp = f / f"BSO-backup-{stamp}.bsobak.part"
            tmp.write_text(sealed, encoding="ascii")
            final = tmp.with_suffix("")
            tmp.replace(final)
            for old in sorted(f.glob("BSO-backup-*.bsobak"))[:-KEEP_BACKUPS]:
                old.unlink(missing_ok=True)
            back_ok, back_detail = check_sealed(final, key, live)
            res.update(offsite=final.name, offsite_ok=back_ok, offsite_detail=back_detail)
        except Exception as e:
            res.update(offsite_ok=False, offsite_detail=f"backup folder copy failed: {e}")
    job = None
    if drive_ready():
        if not ok:
            res.update(drive_ok=False, drive_detail="not uploaded: this copy failed its restore test")
        elif not key:
            res.update(drive_ok=False, drive_detail="sign this BSO in to the team first: the Drive copy is sealed with the team key")
        else:
            name = f"BSO-backup-{stamp}.bsobak"
            res.update(drive=name, drive_ok=None, drive_detail="uploading to Google Drive…")

            def job():
                out = {}
                deadline = time.time() + DRIVE_LIMIT_S
                try:
                    drive_put(name, sealed, manual=manual, deadline=deadline)
                    back = drive_download(name, deadline=deadline)
                    if back != sealed:
                        raise RuntimeError("the copy read back from Google Drive doesn't match")
                    d_ok, d_detail = _check_sealed_text(back, key, live)
                    out.update(drive_ok=d_ok, drive_detail=("read back from Google Drive: " + d_detail) if d_ok else d_detail)
                except Exception as e:
                    out.update(drive_ok=False, drive_detail=f"Google Drive copy failed: {e}")
                return out
    _save_status(res, con)
    if job:
        if drive == "wait":
            res.update(job())
            _save_status(res, con)
        else:
            def run():
                out = job()
                cur = status_file()
                if cur.get("drive") == res["drive"]:             # a newer backup hasn't replaced this one meanwhile
                    cur.update(out)
                    _save_status(cur)
                print(f"Google Drive copy {res['drive']}: {out.get('drive_detail')}", flush=True)
                if out.get("drive_ok") is False and on_drive_fail:
                    try:
                        on_drive_fail(f"⚠ BSO backup problem: {out['drive_detail']}")
                    except Exception:
                        pass
            # Not a daemon thread: a run (a process that ends when its work is done) waits for the upload to finish
            # before it exits, at most DRIVE_LIMIT_S; sending isn't held up, since the run carries on meanwhile.
            print("Google Drive copy: uploading in the background (at most 5 minutes)", flush=True)
            threading.Thread(target=run, daemon=False, name="drive-backup").start()
    return res


def status_file():
    c = sqlite3.connect(str(_data() / "outreach.db"), timeout=30)
    try:
        return status(c)
    except sqlite3.Error:
        return {}
    finally:
        c.close()


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
        for f in tmpdir.glob("*"):
            _shred(f)
        shutil.rmtree(tmpdir, ignore_errors=True)


def open_sealed(path, key, with_origin=False):
    import sealbox
    origin, db = _unpack(zlib.decompress(sealbox.open_(key, Path(path).read_text(encoding="ascii"))))
    return (origin, db) if with_origin else db


def _check_sealed_text(text, key, live=None):
    import sealbox
    try:
        raw = _unpack(zlib.decompress(sealbox.open_(key, text)))[1]
    except Exception as e:
        return False, f"the sealed copy can't be opened ({e})"
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        Path(tmp).write_bytes(raw)
        return check_copy(tmp, live)
    finally:
        _shred(tmp)


# ---------------------------------------------------------------- Google Drive copies through the team relay (version 6)

DRIVE_PART = 4_000_000


def _drive_call(action, body, timeout=180, deadline=None):
    if deadline is not None:
        left = deadline - time.time()
        if left <= 5:
            raise RuntimeError("gave up after 5 minutes (slow connection); the next backup tries again")
        timeout = min(timeout, left)
    import team
    s = team.state()
    code, j = team._post(s["hub"], "/" + action, body, token=team.env(team.TOKEN_KEY), timeout=timeout)
    if code != 200:
        raise RuntimeError(j.get("error") or f"the relay answered {code}")
    return j


def drive_ready():
    """True when this laptop is signed in to a team relay that keeps backups (version 6 or later)."""
    try:
        import relay
        import team
        s = team.state()
        return bool(team.joined() and relay.is_relay(s.get("hub")) and int(s.get("relay_version") or 0) >= 6 and team.env(team.TOKEN_KEY))
    except Exception:
        return False


def drive_put(name, sealed, manual=False, deadline=None):
    parts = [sealed[i:i + DRIVE_PART] for i in range(0, len(sealed), DRIVE_PART)] or [""]
    if len(parts) > 12:
        raise RuntimeError(f"the backup is too big for the relay ({len(sealed) // 1_000_000} MB sealed)")
    for i, p in enumerate(parts):
        last = None
        for attempt in range(3):
            try:
                _drive_call("backup_put", {"name": name, "part": i, "parts": len(parts), "data": p,
                                           "kind": "manual" if manual else "scheduled"}, deadline=deadline)
                last = None
                break
            except Exception as e:
                last = e
                if i == 0 or "Start the backup again" in str(e) or (deadline and time.time() > deadline - 10):
                    break                       # part 0 picks the slot: retrying it would take a second slot
                time.sleep(2 * (attempt + 1))
        if last:
            raise last


def drive_list():
    """The backups stored on Google Drive for this sign-in, newest first."""
    return _drive_call("backup_list", {}, timeout=60).get("backups") or []


def drive_download(name, deadline=None):
    """All parts of one backup. If the copy changes on Drive while it downloads (a new backup reusing its slot), the
    download is tried once more, then refused with a clear message."""
    for attempt in range(2):
        first = _drive_call("backup_get", {"name": name, "part": 0}, deadline=deadline)
        n = int(first.get("parts") or 1)
        data, same = [first.get("data") or ""], True
        for i in range(1, n):
            got = _drive_call("backup_get", {"name": name, "part": i}, deadline=deadline)
            if int(got.get("parts") or 0) != n or got.get("name") != name:
                same = False
                break
            data.append(got.get("data") or "")
        if same:
            return "".join(data)
    raise RuntimeError("That copy changed on Google Drive while it was downloading (a newer backup replaced it). Choose it again or pick another.")


def drive_fetch(name):
    """Download a backup from Google Drive into this laptop's backups folder; returns its path for restore()."""
    if not re.match(r"^BSO-backup-\d{8}-\d{4,6}\.bsobak$", name or ""):
        raise RuntimeError("That isn't a BSO backup name.")
    out = _data() / "backups"
    out.mkdir(exist_ok=True)
    p = out / name
    p.write_text(drive_download(name), encoding="ascii")
    return p


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
        _shred(tmp)


PENDING_HOURS = 6


def restore(src, key=None, now=True):
    """Put a backup back as this laptop's database (the current one is kept aside). src is a .db copy or a sealed .bsobak.
    A sealed backup made under another sign-in is refused: it is someone else's (or another laptop's) work.
    now=False (the app, which has the database open): the checked copy waits, encrypted, as outreach.restore.bsobak and is swapped in
    by apply_pending_restore() the next time BSO starts, before anything opens the database; it expires after
    PENDING_HOURS so a forgotten restore can't replace days of work later."""
    src = Path(src)
    if not src.exists():
        raise RuntimeError("That file doesn't exist. Check the path.")
    data = _data()
    origin = {}
    if src.suffix == ".bsobak":
        if key is not None:
            try:
                origin, raw = open_sealed(src, key, with_origin=True)
            except Exception:
                raise RuntimeError("That file isn't a BSO backup of this team, or it is damaged.")
        else:
            if not _open_keys():
                raise RuntimeError("Sign in to the team first: this backup is sealed with the team key.")
            origin, raw = open_sealed_any(src)
        try:
            import team
            me = team.signed_in()
        except Exception:
            me = None
        if origin.get("user") and me and origin["user"].lower() != me.lower():
            raise RuntimeError(f"That backup belongs to {origin['user']}, not to {me} who is signed in here. "
                               f"Sign in as {origin['user']} to restore it.")
    else:
        raw = src.read_bytes()
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=str(data))
    os.close(fd)
    Path(tmp).write_bytes(raw)
    ok, detail = check_copy(tmp)
    if not ok:
        _shred(tmp)
        if "not a database" in detail:
            raise RuntimeError("That file isn't a BSO backup.")
        raise RuntimeError("That backup didn't pass the restore test: " + detail)
    if not now:                                   # waits for the restart encrypted, like every other copy
        seal_db_file(tmp, data / "outreach.restore.bsobak", origin)
        (data / "outreach.restore.json").write_text(json.dumps({"at": _iso(_now()), "source": src.name, "detail": detail,
                                                                "origin": origin}), encoding="utf-8")
        return detail
    _swap_in(tmp)
    return detail + "; " + settle_gap()


def pending_restore():
    """The restore waiting for BSO to restart, or None (expired ones are removed)."""
    p, m = _data() / "outreach.restore.bsobak", _data() / "outreach.restore.json"
    old = _data() / "outreach.restore.db"                 # left by 1.0.x: encrypt it while it waits
    if old.exists() and not p.exists():
        try:
            seal_db_file(old, p)
        except Exception:
            pass
    if not p.exists():
        return None
    try:
        meta = json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        meta = {"at": _iso(datetime.fromtimestamp(p.stat().st_mtime, timezone.utc))}
    if meta.get("at", "") < _iso(_now() - timedelta(hours=PENDING_HOURS)):
        cancel_restore()
        return None
    return meta


def cancel_restore():
    for n in ("outreach.restore.bsobak", "outreach.restore.db", "outreach.restore.json"):
        (_data() / n).unlink(missing_ok=True)


def apply_pending_restore():
    """Called when BSO starts: a restore chosen in the app is put in place before the database is opened."""
    if not pending_restore():
        return False
    _, raw = open_sealed_any(_data() / "outreach.restore.bsobak")
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=str(_data()))
    os.close(fd)
    Path(tmp).write_bytes(raw)
    _swap_in(tmp)
    (_data() / "outreach.restore.bsobak").unlink(missing_ok=True)
    (_data() / "outreach.restore.json").unlink(missing_ok=True)
    print("restore:", settle_gap())
    return True


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
        stem = f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        keep = data / "backups" / f".{stem}.tmp"
        keep.parent.mkdir(exist_ok=True)
        a, b = sqlite3.connect(str(live)), sqlite3.connect(str(keep))
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
        seal_db_file(keep, keep.with_name(stem + ".bsobak"))   # the database it replaces is kept aside, encrypted
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
    st = status(con)
    last = st.get("at")
    if st.get("drive_ok") is False and not st.get("manual"):
        hours = 1                                        # the Google Drive copy failed: try again within the hour
    return not last or last < _iso(_now() - timedelta(hours=hours))


def status(con):
    r = con.execute("SELECT v FROM app_mode WHERE k='backup_status'").fetchone()
    try:
        st = json.loads(r[0]) if r else {}
    except Exception:
        return {}
    if st.get("drive") and st.get("drive_ok") is None and st.get("at", "") < _iso(_now() - timedelta(seconds=DRIVE_LIMIT_S + 300)):
        st.update(drive_ok=False, drive_detail="the Google Drive upload didn't finish (BSO closed or the connection dropped); "
                                               "the next backup tries again within the hour")
    return st


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
