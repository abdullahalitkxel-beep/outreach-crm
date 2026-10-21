"""Live replies: a reply is picked up within seconds through IMAP IDLE (a small stand-in mail server here),
test-mode replies from your own address count for the lead, and nothing is handled twice.     python tests/watcher_test.py
"""
import imaplib, os, re, socketserver, sys, tempfile, threading, time
from email.message import EmailMessage
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="watcher-test-"))
os.environ["OUTREACH_HOME"] = str(tmp)
os.environ["BSO_NO_SIGNIN"] = "1"
sys.path.insert(0, str(root))
import bot, watcher  # noqa: E402

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=watcher_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

# ---------------------------------------------------------------- a tiny IMAP server with IDLE
MAILBOX, IDLERS, LOGINS = [], [], []
LOCK = threading.Lock()
CAPS = {"IDLE": True}


class H(socketserver.StreamRequestHandler):
    def w(self, s):
        self.wfile.write(s if isinstance(s, bytes) else s.encode())
        self.wfile.flush()

    def handle(self):
        caps = "IMAP4rev1" + (" IDLE" if CAPS["IDLE"] else "")
        self.w(f"* OK [CAPABILITY {caps}] ready\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            parts = line.decode().strip().split(" ", 2)
            if len(parts) < 2:
                continue
            tag, cmd, rest = parts[0], parts[1].upper(), (parts[2] if len(parts) > 2 else "")
            if cmd == "CAPABILITY":
                self.w(f"* CAPABILITY {caps}\r\n{tag} OK done\r\n")
            elif cmd == "LOGIN":
                LOGINS.append(rest)
                self.w(f"{tag} OK logged in\r\n")
            elif cmd in ("SELECT", "EXAMINE"):
                self.w(f"* {len(MAILBOX)} EXISTS\r\n{tag} OK [READ-ONLY] done\r\n")
            elif cmd == "UID" and rest.upper().startswith("SEARCH"):
                self.w("* SEARCH " + " ".join(str(i + 1) for i in range(len(MAILBOX))) + f"\r\n{tag} OK done\r\n")
            elif cmd == "UID" and rest.upper().startswith("FETCH"):
                uid = int(rest.split()[1])
                raw = MAILBOX[uid - 1]
                if "HEADER.FIELDS" in rest.upper():
                    hdr = raw.split(b"\r\n\r\n", 1)[0]
                    keep = b"\r\n".join(l for l in hdr.split(b"\r\n") if l.lower().startswith((b"message-id", b"subject"))) + b"\r\n\r\n"
                    data, item = keep, "BODY[HEADER.FIELDS (MESSAGE-ID SUBJECT)]"
                else:
                    data, item = raw, "BODY[]"
                self.w(f"* {uid} FETCH (UID {uid} {item} {{{len(data)}}}\r\n".encode() + data + b")\r\n" + f"{tag} OK done\r\n".encode())
            elif cmd == "IDLE":
                self.w("+ idling\r\n")
                with LOCK:
                    IDLERS.append(self)
                done = self.rfile.readline()
                with LOCK:
                    if self in IDLERS:
                        IDLERS.remove(self)
                if not done:
                    return
                self.w(f"{tag} OK IDLE done\r\n")
            elif cmd == "LOGOUT":
                self.w(f"* BYE\r\n{tag} OK bye\r\n")
                return
            else:
                self.w(f"{tag} OK\r\n")


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = S(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
imaplib.IMAP4_SSL = lambda host, port=993, timeout=None, **k: imaplib.IMAP4(host, port, timeout=timeout)   # plain connection for the test


def deliver(frm, subject, body, mid):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "inbox1@getneosofttech.com", subject, mid
    m.set_content(body)
    MAILBOX.append(m.as_bytes().replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n"))
    with LOCK:
        for h in list(IDLERS):
            try:
                h.w(f"* {len(MAILBOX)} EXISTS\r\n")
            except Exception:
                pass


# ---------------------------------------------------------------- BSO with one inbox, in test mode
cfgp = str(bot.DATA / "config.yaml")
import yaml  # noqa: E402
sp = bot.settings_path(cfgp)
sp.write_text(yaml.safe_dump({
    "senders": [{"email": "inbox1@getneosofttech.com", "name": "Cole Reed", "smtp_host": "127.0.0.1", "smtp_port": 465,
                 "imap_host": "127.0.0.1", "imap_port": PORT, "password_env": "SENDER1_PASSWORD", "daily_limit": 10}],
    "test_mode": {"enabled": True, "redirect_to": "omar@blacswanken.com"}}), encoding="utf-8")
os.environ["SENDER1_PASSWORD"] = "pw"
cfg = bot.load_config(cfgp)
con = bot.db_connect(cfg.get("database", "outreach.db"))
for e_, f_ in (("sarah@acme.com", "Sarah"), ("tom@globex.com", "Tom"), ("kim@umbrella.com", "Kim")):
    con.execute("INSERT INTO leads(email, first_name, company, extra, status, step, created_at, first_subject) VALUES (?,?,'Co','{}','active',1,'2026-10-01','Quick question')", (e_, f_))
con.commit()
bot.send_email = lambda *a, **k: "<x>"

# a test reply that an older version already saw (and couldn't match): it's looked at again once
deliver("Omar Ali <omar@blacswanken.com>", "Re: [TEST for kim@umbrella.com] Quick question", "Not interested, thanks.", "<old-1@x>")
con.execute("INSERT INTO seen_messages VALUES ('<old-1@x>')")
con.commit()

watcher.start(cfgp, every=1)
for _ in range(100):
    if watcher.STATUS.get("inbox1@getneosofttech.com", {}).get("state") == "live" and IDLERS:
        break
    time.sleep(0.1)
check(watcher.STATUS.get("inbox1@getneosofttech.com", {}).get("state") == "live" and IDLERS, "BSO keeps a live connection open to the inbox")
st = lambda e: con.execute("SELECT status FROM leads WHERE email=?", (e,)).fetchone()[0]
check(st("kim@umbrella.com") == "not_interested", "on start it catches up, including a test reply an older version skipped")

t0 = time.time()
deliver("Omar Ali <omar@blacswanken.com>", "RE: [TEST for sarah@acme.com] Quick question", "Sounds interesting, tell me more please.", "<r-1@x>")
while time.time() - t0 < 10 and st("sarah@acme.com") == "active":
    time.sleep(0.1)
took = time.time() - t0
check(st("sarah@acme.com") == "replied", f"a reply is picked up {took:.1f} s after it arrives, without a run")
ev = con.execute("SELECT type, detail FROM events WHERE lead_id=(SELECT id FROM leads WHERE email='sarah@acme.com') AND type='reply'").fetchall()
check(len(ev) == 1 and ev[0]["detail"] == "interested", "your test reply counts as the lead's reply, labelled 'interested'")

deliver("Omar Ali <omar@blacswanken.com>", "[TEST for tom@globex.com] Quick question", "This is BSO's own test email, not a reply.", "<own-1@x>")
time.sleep(2)
check(st("tom@globex.com") == "active", "BSO's own test emails aren't mistaken for replies")

# a run and the watcher reading the same inbox at the same moment never handle a message twice
deliver("tom@globex.com", "Re: Quick question", "Can we talk next week?", "<r-2@x>")
s1 = next(x for x in bot.load_senders(cfg))
res = []
ths = [threading.Thread(target=lambda: res.append(bot.process_inbox(cfg, bot.db_connect(cfg.get("database", "outreach.db")), s1))) for _ in range(3)]
[t.start() for t in ths]
[t.join() for t in ths]
time.sleep(2)
cnt = con.execute("SELECT COUNT(*) FROM events WHERE type='reply' AND lead_id=(SELECT id FROM leads WHERE email='tom@globex.com')").fetchone()[0]
check(cnt == 1, "each message is handled exactly once")

# a server without IDLE: checked every couple of minutes instead
watcher.stop_all()
watcher.STATUS.clear()
CAPS["IDLE"] = False
watcher.reconcile(cfgp, lambda: True)
for _ in range(60):
    if watcher.STATUS.get("inbox1@getneosofttech.com"):
        break
    time.sleep(0.1)
check("every 2 minutes" in watcher.STATUS.get("inbox1@getneosofttech.com", {}).get("state", ""), "a server without live push falls back to checking every 2 minutes")
watcher.stop_all()
print(f"\nAll {n} live-reply checks passed.")
