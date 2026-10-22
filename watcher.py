"""Live reply watcher: keeps a connection open to each sending inbox and reacts the moment a message arrives
(IMAP IDLE, which SiteGround, Google, Microsoft and most mail servers support). A reply is read, labelled and,
if it's interested, a meeting request or a question, handed to a supervisor within seconds instead of at the next run.

Runs inside the BSO app. Falls back to checking every POLL_SECONDS when a server doesn't support IDLE.
Only reads: messages are never marked as read (the inbox is opened read-only).
"""
import imaplib
import socket
import threading
import time

import bot

IDLE_SECONDS = 20 * 60          # servers end an idle connection after ~30 min; reconnect before that
POLL_SECONDS = 120
_threads = {}                   # inbox email -> Watcher
_lock = threading.Lock()
_inbox_locks = {}
STATUS = {}                     # inbox email -> {"state": ..., "at": ..., "error": ...} (shown in Settings)


def _inbox_lock(email):
    with _lock:
        return _inbox_locks.setdefault(email, threading.Lock())


def check_now(config_path, email):
    """Read new messages in one inbox now. Returns how many were new."""
    cfg = bot.load_config(config_path)
    s = next((x for x in bot.load_senders(cfg) if x["email"].lower() == email.lower()), None)
    if not s or not s.get("password") or not s.get("imap_host"):
        return 0
    with _inbox_lock(email.lower()):
        con = bot.db_connect(cfg.get("database", "outreach.db"))
        try:
            bot.sync_mode(con, cfg)
            n = bot.recheck_once(cfg, con, s) + bot.process_inbox(cfg, con, s)
            if n:
                after_new_mail(con)
            return n
        finally:
            con.close()


def after_new_mail(con):
    """Pass handovers on to the team straight away rather than at the next check-in."""
    try:
        import team
        if team.joined() and con.execute("SELECT 1 FROM handover_out WHERE synced=0").fetchone():
            team.sync(con)
    except Exception as e:
        print("watcher sync:", e)


class Watcher(threading.Thread):
    def __init__(self, config_path, sender, allowed):
        super().__init__(daemon=True, name=f"watch {sender['email']}")
        self.config_path, self.s, self.allowed = config_path, sender, allowed
        self.stop = threading.Event()
        self.key = (sender["email"].lower(), sender.get("imap_host"), sender.get("password"))

    def status(self, state, error=""):
        STATUS[self.s["email"].lower()] = {"state": state, "at": time.time(), "error": str(error)[:200]}

    def run(self):
        wait = 15
        while not self.stop.is_set():
            if not self.allowed():
                self.stop.wait(30)
                continue
            M = None
            try:
                check_now(self.config_path, self.s["email"])          # catch up on anything that came in meanwhile
                M = imaplib.IMAP4_SSL(self.s["imap_host"], int(self.s.get("imap_port") or 993), timeout=60)
                M.login(self.s.get("username", self.s["email"]), self.s["password"])
                M.select("INBOX", readonly=True)
                if b"IDLE" not in b" ".join(x if isinstance(x, bytes) else str(x).encode() for x in M.capabilities):
                    self.status("checking every 2 minutes")
                    M.logout()
                    M = None
                    self.stop.wait(POLL_SECONDS)
                    continue
                self.status("live")
                wait = 15
                started = time.time()
                while not self.stop.is_set() and time.time() - started < IDLE_SECONDS and self.allowed():
                    if self._idle(M, max(30, IDLE_SECONDS - (time.time() - started))):
                        check_now(self.config_path, self.s["email"])
            except (socket.timeout, TimeoutError):
                pass                                                  # quiet inbox: reconnect, which also catches up
            except Exception as e:
                self.status("reconnecting", e)
                self.stop.wait(wait)
                wait = min(wait * 2, 600)
            finally:
                if M is not None:
                    try:
                        M.shutdown()
                    except Exception:
                        pass

    def _idle(self, M, seconds):
        """Wait in IDLE until the server reports new mail (True) or `seconds` pass (False). Raises on a broken connection."""
        tag = M._new_tag()
        M.send(tag + b" IDLE\r\n")
        line = M.readline()
        if not line.startswith(b"+"):
            raise imaplib.IMAP4.error(f"IDLE refused: {line[:80]!r}")
        M.sock.settimeout(seconds)
        got = False
        try:
            while not got:
                line = M.readline()
                if not line:
                    raise imaplib.IMAP4.abort("connection closed")
                got = b"EXISTS" in line or b"RECENT" in line
        except (socket.timeout, TimeoutError):
            raise                                                     # the reader can't be reused after a timeout: reconnect
        finally:
            try:
                M.sock.settimeout(60)
            except Exception:
                pass
        M.send(b"DONE\r\n")
        while True:
            line = M.readline()
            if not line or line.startswith(tag):
                break
        return got


def reconcile(config_path, allowed):
    """Start a watcher for every inbox that has a password and an IMAP server; stop ones that were removed or changed."""
    try:
        cfg = bot.load_config(config_path)
        if cfg.get("live_replies") is False:
            senders = []
        else:
            senders = [s for s in bot.load_senders(cfg) if s.get("password") and s.get("imap_host") and "example" not in s["email"]]
    except Exception as e:
        print("watcher:", e)
        return
    want = {(s["email"].lower(), s.get("imap_host"), s.get("password")): s for s in senders}
    with _lock:
        for email, w in list(_threads.items()):
            if w.key not in want or not w.is_alive():
                w.stop.set()
                _threads.pop(email, None)
                STATUS.pop(email, None)
        for key, s in want.items():
            if key[0] not in _threads:
                w = Watcher(config_path, s, allowed)
                _threads[key[0]] = w
                w.start()


def start(config_path, allowed=lambda: True, every=60):
    """Keep the watchers in step with Settings for as long as the app runs."""
    def loop():
        while True:
            reconcile(config_path, allowed)
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="reply watchers").start()


def stop_all():
    with _lock:
        for w in _threads.values():
            w.stop.set()
        _threads.clear()
