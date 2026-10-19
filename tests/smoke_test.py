"""
Smoke test for the packaged app: launches BSO.exe (or `python app.py`), clicks through it
over HTTP, runs a bot job through the --bot path, and shuts it down. Used by the GitHub build.

  python tests/smoke_test.py dist/BSO.exe
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

target = sys.argv[1]
home = Path(tempfile.mkdtemp(prefix="outreach-home-", dir=os.getenv("SMOKE_TMP") or None))
env = {**os.environ, "OUTREACH_HOME": str(home), "OUTREACH_NO_WINDOW": "1", "OUTREACH_PORT": "8799", "BSO_NO_SIGNIN": "1"}  # no hub in the build machine; sign-in is tested in team_test.py
cmd = [target] if target.endswith(".exe") or os.access(target, os.X_OK) and not target.endswith(".py") else [sys.executable, target]
base = "http://127.0.0.1:8799"
proc = subprocess.Popen(cmd, env=env)
ok = False


def gh_error(title, text):
    """Show a message in GitHub's run summary (annotations are readable even when raw logs aren't)."""
    if os.getenv("GITHUB_ACTIONS"):
        body = text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-6000:]
        print(f"::error title={title}::{body}", flush=True)


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("Smoke test failed", what)
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
    check(True, "app started")
    r = requests.get(base + "/", timeout=10, allow_redirects=False)
    check(r.status_code == 302 and "/welcome" in r.headers.get("Location", ""), "first launch asks to accept the terms")
    w = requests.get(base + "/welcome", timeout=10).text
    check("by BlacSwanken" in w, "welcome screen shows publisher BlacSwanken")
    for d in ("terms", "privacy", "acceptable-use", "third-party"):
        r = requests.get(base + f"/legal/{d}", timeout=10)
        check(r.status_code == 200 and "{{" not in r.text, f"legal document /legal/{d}")
    ic = requests.get(base + "/favicon.ico", timeout=10)
    check(ic.status_code == 200 and ic.content[:4] == b"\x00\x00\x01\x00" and 'rel="icon"' in requests.get(base + "/welcome", timeout=10).text,
          "window/taskbar icon is served (favicon.ico + link tag)")
    check(requests.post(base + "/bye", timeout=10).status_code == 204, "window close signal (/bye) accepted")
    check(requests.get(base + "/manifest.webmanifest", timeout=10).json().get("short_name") == "BSO", "web manifest names the app BSO")
    tp = requests.get(base + "/legal/third-party", timeout=10).text
    check("flask" in tp.lower() and "Python Software Foundation" in tp, "third-party notices list bundled components")
    r = requests.post(base + "/legal/accept", data={"agree": "1"}, timeout=10)
    check(r.status_code == 200, "terms accepted")
    check((home / "config.yaml").exists(), "default config copied into the data folder")
    sc = subprocess.run(cmd + ["--bot", "selfcheck"], env=env, capture_output=True, text=True, timeout=120)
    sc_out = (sc.stdout or "") + (sc.stderr or "")
    if (home / "bot.log").exists():
        sc_out += (home / "bot.log").read_text(errors="replace")
    print(sc_out.strip()[-2000:])
    check(sc.returncode == 0 and "SELFCHECK OK" in sc_out, "all on-demand libraries and time zones are bundled (selfcheck)")

    for page in ["/", "/leads", "/review", "/accounts", "/opportunities", "/tasks", "/reports", "/jobs",
                 "/settings?tab=inbox", "/settings?tab=safety", "/settings?tab=keys", "/settings?tab=emails",
                 "/settings?tab=targeting", "/settings?tab=schedule", "/settings?tab=team", "/import", "/new"]:
        check(requests.get(base + page, timeout=10).status_code == 200, f"page {page}")

    s = requests.Session()
    r = s.post(base + "/settings/inbox", data={"provider1": "gmail", "email1": "tester@example-outreach.com",
                                                "name1": "Tester", "password1": "pw-1234", "daily_limit1": "10"})
    check(r.status_code == 200 and "Inboxes saved" in r.text, "settings: inbox saved")
    leaked = [f.name for f in home.rglob("*") if f.is_file() and b"pw-1234" in f.read_bytes()]
    check(not leaked or os.name != "nt", f"password not stored in plain text anywhere (found in: {leaked})")
    check("1234" in s.get(base + "/settings?tab=inbox").text, "saved password is recognised (masked) after encryption")
    a = s.get(base + "/about").text
    check("BlacSwanken" in a and ("encrypted with Windows Data Protection" in a or os.name != "nt"), "About page: publisher and encryption status")
    r = s.post(base + "/settings/safety", data={"test_mode": "on", "redirect_to": "me@example.com", "review": "on"})
    check("Saved" in r.text, "settings: safety saved")

    sample = Path(__file__).with_name("sample.csv")
    r = s.post(base + "/import", files={"file": ("sample.csv", sample.read_bytes())}, data={"sequence": "1"})
    check("<b>2</b> lead(s) added" in r.text, "import: 2 leads added, shared inbox skipped")

    # runs the bot as a separate process (BSO.exe --bot ...) — drafts only, nothing is sent
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
    r = s.post(base + "/sample/load")
    check("Added 6 sample leads" in r.text, "sample data can be added (for reviewers)")
    r = s.post(base + "/sample/remove")
    check("Removed 6 sample leads" in r.text, "sample data can be removed")
    ok = True
finally:
    try:
        requests.post(base + "/quit", timeout=3)
    except requests.RequestException:
        pass
    time.sleep(2)
    if proc.poll() is None:
        proc.kill()
    if not ok:
        for f in [home / "app.log", home / "bot.log"] + (sorted((home / "logs").glob("*.log")) if (home / "logs").exists() else []):
            if f.exists() and f.read_text(errors="replace").strip():
                text = f.read_text(errors="replace")[-4000:]
                print(f"---- {f.name} ----\n" + text)
                gh_error(f"Log: {f.name}", text)

print("SMOKE TEST PASSED" if ok else "SMOKE TEST FAILED")
sys.exit(0 if ok else 1)
