"""Full visibility for the Master Console.

Everything about leads and conversations on this BSO goes to the console: every email sent and received, lead lists
by campaign, notes, tasks, temperatures, run summaries, inbox activity, the supervisor's conversations with prospects
and any incident (a blocked or leaked email, a deleted one). It is sealed with a key only this BSO and the console know:
the BSO picks the key, sends it once sealed with the console's public key, and Google Drive only ever carries ciphertext.

The first time, everything this BSO already holds (from before this version) goes up once as the archive.
"""
import json
import secrets

import sealbox

KEY = "BSO_VIS_KEY"
BATCH_BYTES = 240_000
MAX_BATCHES = 4
ARCHIVE_TABLES = (("events", "SELECT e.*, l.email lead_email FROM events e LEFT JOIN leads l ON l.id=e.lead_id"),
                  ("outbox", "SELECT o.*, l.email lead_email FROM outbox o LEFT JOIN leads l ON l.id=o.lead_id"),
                  ("notes", "SELECT n.*, l.email lead_email FROM notes n LEFT JOIN leads l ON l.id=n.lead_id"),
                  ("messages", "SELECT m.*, l.email lead_email FROM messages m LEFT JOIN leads l ON l.id=m.lead_id"),
                  ("tasks", "SELECT t.*, l.email lead_email FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id"),
                  ("comments", "SELECT * FROM comments"))


def ensure(con, add_column):
    for t in ("messages", "notes", "inbox_log", "temp_log", "jobs"):
        add_column(con, t, "vis", "INTEGER DEFAULT 0")
    for t in ("leads", "tasks"):
        add_column(con, t, "vis_dirty", "INTEGER DEFAULT 1")
    add_column(con, "messages", "bcc", "TEXT")
    add_column(con, "messages", "folder", "TEXT")
    add_column(con, "messages", "deleted_at", "TEXT")
    add_column(con, "messages", "deleted_how", "TEXT")
    con.executescript("""
CREATE TABLE IF NOT EXISTS incidents (            -- blocked or leaked emails, deletions: reported to the console, kept forever
    id INTEGER PRIMARY KEY, at TEXT, kind TEXT, lead_email TEXT, detail TEXT, how TEXT, msg TEXT, vis INTEGER DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS leads_vis_dirty AFTER UPDATE ON leads WHEN NEW.vis_dirty = 0 AND OLD.vis_dirty = 0
BEGIN UPDATE leads SET vis_dirty = 1 WHERE id = NEW.id; END;
CREATE TRIGGER IF NOT EXISTS tasks_vis_dirty AFTER UPDATE ON tasks WHEN NEW.vis_dirty = 0 AND OLD.vis_dirty = 0
BEGIN UPDATE tasks SET vis_dirty = 1 WHERE id = NEW.id; END;
""")


def incident(con, kind, lead_email, detail, how, msg=None):
    con.execute("INSERT INTO incidents(at, kind, lead_email, detail, how, msg) VALUES (?,?,?,?,?,?)",
                (_now(), kind, (lead_email or "").lower(), detail[:500], how, json.dumps(msg)[:60000] if msg else None))


def _now():
    from bot import iso, now_utc
    return iso(now_utc())


def my_key(secrets_store):
    k = secrets_store.load().get(KEY)
    if not k:
        k = secrets.token_hex(32)
        secrets_store.set(KEY, k)
    return bytes.fromhex(k)


def key_event(secrets_store, pub):
    """The key, sealed for the console's eyes only."""
    return {"id": "vkey", "type": "__vkey", "data": {"box": sealbox.rsa_encrypt(pub, my_key(secrets_store))}}


def _rows(con, sql, *a):
    return [dict(r) for r in con.execute(sql, a)]


def collect(con, state, after):
    """One batch of everything not yet sent. `after` holds, per table, the last id already taken in this check-in.
    Returns (payload, marks); payload is None when there's nothing left."""
    out, marks, size = {}, [], 0

    def take(name, items, mark):
        nonlocal size
        got = []
        for it in items:
            b = len(json.dumps(it, default=str))
            if size + b > BATCH_BYTES and (got or out):
                break
            got.append(it)
            size += b
        if got:
            out.setdefault(name, []).extend(got)
            marks.append((mark, [g["id"] for g in got]))
            key = "a:" + mark[1] if mark[0] == "archive" else mark[0]
            after[key] = max(after.get(key, 0), max(g["id"] for g in got))
        return len(got) == len(items)

    if not state.get("vis_archive_done"):
        cur = dict(state.get("vis_archive_cursor") or {})
        for tbl, sql in ARCHIVE_TABLES + (("leads", "SELECT l.*, c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=COALESCE(l.campaign_id,1)"),):
            start = max(cur.get(tbl, 0), after.get("a:" + tbl, 0))
            try:
                items = _rows(con, f"SELECT * FROM ({sql}) WHERE id>? ORDER BY id LIMIT 300", start)
            except Exception:
                continue
            if items:
                take("archive_" + tbl, items, ("archive", tbl))
            if size >= BATCH_BYTES:
                break
        if marks:
            return out, marks
        state["vis_archive_done"] = True

    plans = [
        ("sent", "SELECT * FROM sent_log WHERE synced=0 AND test=0 AND id>? ORDER BY id LIMIT 200", ("sent_log", "synced")),
        ("received", "SELECT r.*, l.email lead_email FROM received r LEFT JOIN leads l ON l.id=r.lead_id WHERE r.synced=0 AND r.id>? ORDER BY r.id LIMIT 200",
         ("received", "synced")),
        ("messages", "SELECT m.*, l.email lead_email FROM messages m LEFT JOIN leads l ON l.id=m.lead_id WHERE m.vis=0 AND m.id>? ORDER BY m.id LIMIT 200",
         ("messages", "vis")),
        ("incidents", "SELECT * FROM incidents WHERE vis=0 AND id>? ORDER BY id LIMIT 100", ("incidents", "vis")),
        ("leads", "SELECT l.*, c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=COALESCE(l.campaign_id,1) WHERE l.vis_dirty=1 AND l.id>? ORDER BY l.id LIMIT 400",
         ("leads", "vis_dirty")),
        ("notes", "SELECT n.*, l.email lead_email FROM notes n LEFT JOIN leads l ON l.id=n.lead_id WHERE n.vis=0 AND n.id>? ORDER BY n.id LIMIT 300", ("notes", "vis")),
        ("tasks", "SELECT t.*, l.email lead_email FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id WHERE t.vis_dirty=1 AND t.id>? ORDER BY t.id LIMIT 300",
         ("tasks", "vis_dirty")),
        ("temps", "SELECT t.*, l.email lead_email FROM temp_log t LEFT JOIN leads l ON l.id=t.lead_id WHERE t.vis=0 AND t.id>? ORDER BY t.id LIMIT 300", ("temp_log", "vis")),
        ("inbox", "SELECT * FROM inbox_log WHERE vis=0 AND id>? ORDER BY id LIMIT 300", ("inbox_log", "vis")),
        ("runs", "SELECT id, cmd, status, started_at, finished_at, substr(output, -6000) output FROM jobs WHERE vis=0 AND status!='running' AND id>? ORDER BY id LIMIT 20",
         ("jobs", "vis")),
    ]
    try:
        test_rows = {(r[0], r[1]) for r in con.execute("SELECT tbl, rid FROM test_rows")}
    except Exception:
        test_rows = set()
    for name, sql, mark in plans:
        try:
            items = _rows(con, sql, after.get(mark[0], 0))
        except Exception:
            continue
        tbl = mark[0]
        if tbl == "leads":                              # a lead changed by a test waits until going live puts it back
            try:
                held = {r[0] for r in con.execute("SELECT lead_id FROM test_snapshot")}
            except Exception:
                held = set()
            live = [i for i in items if i["id"] not in held]
            if len(live) < len(items):
                after[tbl] = max(after.get(tbl, 0), max(i["id"] for i in items))
        else:
            live = [i for i in items if (tbl, str(i["id"])) not in test_rows]        # test results never leave this computer
            skipped = [i["id"] for i in items if (tbl, str(i["id"])) in test_rows]
            if skipped:
                marks.append((mark, skipped))
                after[tbl] = max(after.get(tbl, 0), max(skipped))
        if live:
            take(name, live, mark)
        if size >= BATCH_BYTES:
            break
    return (out or None), marks


def mark_done(con, state, marks):
    for mark, ids in marks or []:
        if not ids:
            continue
        if mark[0] == "archive":
            cur = dict(state.get("vis_archive_cursor") or {})
            cur[mark[1]] = max([cur.get(mark[1], 0)] + list(ids))
            state["vis_archive_cursor"] = cur
            continue
        tbl, col = mark
        val = 0 if col == "vis_dirty" else 1
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            con.execute(f"UPDATE {tbl} SET {col}=? WHERE id IN ({','.join('?' * len(part))})", (val, *part))
    con.commit()


def events(con, state, secrets_store):
    """The sealed batches for this check-in: ([events], marks to apply once the hub has accepted them, archive finished?)."""
    pub = (state.get("security") or {}).get("console_key")
    if not pub:
        return [], [], False
    evs, all_marks = [], []
    if not state.get("vis_key_ok"):
        evs.append(key_event(secrets_store, pub))
    k = my_key(secrets_store)
    look = dict(state)                                  # a copy: nothing is recorded as sent until the hub has it
    after = {}
    for n in range(MAX_BATCHES):
        payload, marks = collect(con, look, after)
        all_marks += marks or []
        if payload is None:
            break
        if not look.get("vis_archive_done") and any(key.startswith("archive_") for key in payload):
            payload["archive"] = True
        evs.append({"id": f"vis{n}", "type": "__vis", "data": {"box": sealbox.seal(k, json.dumps(payload, default=str))}})
    return evs, all_marks, bool(look.get("vis_archive_done"))
