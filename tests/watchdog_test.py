"""The app must not quit while its window is merely hidden, minimised or asleep, only after it is closed.
   python tests/watchdog_test.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import should_quit, CLOSE_GRACE, STALE, FIRST_WINDOW  # noqa: E402

T0 = 1_000_000.0
cases = [
    ("window open and pinging", dict(now=T0 + 600, last_seen=T0 + 595, last_bye=None), False),
    ("minimised 20 min, pings once a minute", dict(now=T0 + 1200, last_seen=T0 + 1150, last_bye=None), False),
    ("laptop asleep 2 hours, no pings", dict(now=T0 + 7200, last_seen=T0, last_bye=None), False),
    ("moved to another page (bye, then ping)", dict(now=T0 + 200, last_seen=T0 + 101, last_bye=T0 + 100), False),
    ("window just closed", dict(now=T0 + 100 + CLOSE_GRACE - 5, last_seen=T0 + 99, last_bye=T0 + 100), False),
    ("window closed a while ago", dict(now=T0 + 100 + CLOSE_GRACE + 5, last_seen=T0 + 99, last_bye=T0 + 100), True),
    ("browser crashed, silent for over 6 h", dict(now=T0 + STALE + 10, last_seen=T0, last_bye=None), True),
    ("first window never appeared", dict(now=T0 + FIRST_WINDOW + 5, last_seen=None, last_bye=None), True),
    ("closed but auto-run on", dict(now=T0 + 1000, last_seen=T0, last_bye=T0 + 1, keep=True), False),
    ("closed but a bot run is busy", dict(now=T0 + 1000, last_seen=T0, last_bye=T0 + 1, busy=True), False),
]
bad = 0
for name, kw, want in cases:
    got = should_quit(started=T0, **kw)
    print(("PASS " if got == want else "FAIL ") + name)
    bad += got != want
sys.exit(1 if bad else 0)
