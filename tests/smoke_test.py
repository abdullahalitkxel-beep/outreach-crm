"""
Smoke test for the packaged app: launches OutreachCRM.exe (or `python app.py`), clicks through it
over HTTP, runs a bot job through the --bot path, and shuts it down. Used by the GitHub build.

  python tests/smoke_test.py dist/OutreachCRM.exe
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

target = sys.argv[1]
home = Path(tempfile.mkdtemp(prefix="outreach-home-"))
env = {**os.environ, "OUTREACH_HOME": str(home), "OUTREACH_NO_WINDOW": "1", "OUTREACH_PORT": "8799"}
cmd = [target] if target.endswith(".exe") or os.access(target, os.X_OK) and not target.endswith(".py") else [sys.executable, target]
base = "http://127.0.0.1:8799"
proc = subprocess.Popen(cmd, env=env)
ok = False


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SystemExit(1)


try:
    for _ in range(90):
        try:
            if requests.get(base + "/setup", timeout=2).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        check(False, "app started and served the Getting Started page")
    check(True, "app started and served the Getting Started page")
    check((home / "config.yaml").exists(), "default config copied into the data folder")

    for page in ["/", "/leads", "/review", "/accounts", "/opportunities", "/tasks", "/reports", "/jobs",
                 "/settings?tab=inbox", "/settings?tab=safety", "/settings?tab=keys", "/settings?tab=emails",
                 "/settings?tab=targeting", "/settings?tab=schedule", "/import", "/new"]:
        check(requests.get(base + page, timeout=10).status_code == 200, f"page {page}")

    s = requests.Session()
    r = s.post(base + "/settings/inbox", data={"provider1": "gmail", "email1": "tester@example-outreach.com",
                                                "name1": "Tester", "password1": "pw-1234", "daily_limit1": "10"})
    check(r.status_code == 200 and "Inboxes saved" in r.text, "settings: inbox saved")
    check("SENDER1_PASSWORD=pw-1234" in (home / ".env").read_text(), "settings: password stored in .env")
    r = s.post(base + "/settings/safety", data={"test_mode": "on", "redirect_to": "me@example.com", "review": "on"})
    check("Saved" in r.text, "settings: safety saved")

    sample = Path(__file__).with_name("sample.csv")
    r = s.post(base + "/import", files={"file": ("sample.csv", sample.read_bytes())}, data={"sequence": "1"})
    check("<b>2</b> lead(s) added" in r.text, "import: 2 leads added, shared inbox skipped")

    # runs the bot as a separate process (OutreachCRM.exe --bot ...) — drafts only, nothing is sent
    s.post(base + "/jobs/start", data={"cmd": "run"})
    for _ in range(120):
        jobs = s.get(base + "/jobs").text
        if "Running…" not in jobs:
            break
        time.sleep(1)
    page = s.get(base + "/jobs/1").text
    check("Drafted 2 new for review" in page, "bot run via --bot: 2 drafts created")
    r = s.get(base + "/review")
    check(r.text.count('id="d') == 2 and "Example Corp" in r.text, "review queue shows the 2 drafts")
    ok = True
finally:
    try:
        requests.post(base + "/quit", timeout=3)
    except requests.RequestException:
        pass
    time.sleep(2)
    if proc.poll() is None:
        proc.kill()
    log = home / "app.log"
    if log.exists() and not ok:
        print("---- app.log ----\n" + log.read_text(errors="replace")[-4000:])
    for f in sorted((home / "logs").glob("*.log")) if (home / "logs").exists() else []:
        if not ok:
            print(f"---- {f.name} ----\n" + f.read_text(errors="replace")[-4000:])

print("SMOKE TEST PASSED" if ok else "SMOKE TEST FAILED")
sys.exit(0 if ok else 1)
