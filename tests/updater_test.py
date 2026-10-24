"""Automatic updates: only releases signed by BlacSwanken's build key are installed; staged roll-out by role; hold,
release-now and minimum version from the Master Console; the bot waits for a quiet moment; supervisors after 3 days
or tonight; the database is backed up; a failed update is reported as rolled back.
   python tests/updater_test.py"""
import hashlib, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp(prefix="updater-test-")
os.environ["BSO_NO_SIGNIN"] = "1"
import bot, updater, sealbox  # noqa: E402,E401

n = 0
def gh_error(text):
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=updater_test::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:], flush=True)
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        gh_error("FAIL " + what)
        sys.exit(1)
    n += 1

key = json.loads((root / "update_key.json").read_text())
check(int(key["n"], 16).bit_length() >= 3072, "the public release key is bundled (3072-bit RSA)")

pub, priv = sealbox.rsa_keypair(2048)
N, E, D = int(pub["n"], 16), pub["e"], int(priv["d"], 16)
def sign(data):
    k = (N.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - 3 - len(updater.SHA256_PREFIX) - 32) + b"\x00" + updater.SHA256_PREFIX + hashlib.sha256(data).digest()
    return pow(int.from_bytes(em, "big"), D, N).to_bytes(k, "big")
installer = os.urandom(50000)
sig = sign(installer)
updater._key = lambda: (N, E)
check(updater.verify(installer, sig), "a release signed with the build key is accepted")
check(not updater.verify(installer + b"x", sig) and not updater.verify(installer, sig[:-1] + bytes([sig[-1] ^ 1])),
      "a changed installer or signature is refused")

# ---------------------------------------------------------------- checking GitHub
updater.current = lambda: "1.0.47"
now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
class Resp:
    def __init__(self, j): self.j = j
    def raise_for_status(self): pass
    def json(self): return self.j
def rel(tag, base="https://x/"):
    return {"tag_name": tag, "published_at": "2026-11-02T10:00:00Z", "body": "Autopilot for the BSO Bot",
            "assets": [{"name": n, "browser_download_url": base + tag + "/" + n} for n in ("BSO-Setup.exe", "BSO-release.json", "BSO-release.json.sig")]}
release = rel("v1.0.52")
manifests = {}
def publish(tag, data=installer, signed_as=None):
    """What the build does: a manifest naming the version and the installer's hash, signed with the build key."""
    body = json.dumps({"version": (signed_as or tag).lstrip("v"), "sha256": hashlib.sha256(data).hexdigest()}).encode()
    manifests[tag] = (body, sign(body))
publish("v1.0.52")
def small(url):
    tag, name = url.split("/")[-2:]
    if tag not in manifests:
        raise RuntimeError("404")
    return manifests[tag][1] if name.endswith(".sig") else manifests[tag][0]
updater._small = small
updater.requests.get = lambda *a, **k: Resp(release)
b = updater.check(force=True)
check(b["available"] and b["latest"] == "1.0.52" and "Autopilot" in b["notes"], "a newer release is found with its notes")
asked = []
def two_repos(url, *a, **k):
    asked.append(url)
    if "bso-releases" in url:
        raise RuntimeError("404 Not Found")            # the releases repository doesn't exist yet
    return Resp(release)
updater.requests.get = two_repos
b = updater.check(force=True)
check(b["latest"] == "1.0.52" and any("bso-releases" in u for u in asked) and any("outreach-crm" in u for u in asked),
      "updates come from the public releases repository, and from the code repository until that exists")
publish("v1.0.50")
older = rel("v1.0.50")
updater.requests.get = lambda url, *a, **k: Resp(release if "bso-releases" in url else older)
check(updater.check(force=True)["latest"] == "1.0.52", "the newest release in either repository wins")
planted = {"tag_name": "v9.9.9", "assets": [{"name": "BSO-Setup.exe", "browser_download_url": "https://x/v9.9.9/BSO-Setup.exe"}]}
updater.requests.get = lambda url, *a, **k: Resp(planted if "bso-releases" in url else release)
b = updater.check(force=True)
check(b["latest"] == "1.0.52" and b["available"], "a planted release without BlacSwanken's signed manifest is skipped: it can't block updates")
publish("v9.9.8", signed_as="v1.0.40")              # an old, genuinely signed release republished under a higher number
updater.requests.get = lambda url, *a, **k: Resp(rel("v9.9.8") if "bso-releases" in url else release)
check(updater.check(force=True)["latest"] == "1.0.52", "an old signed release republished under a newer number is refused (no downgrade)")
updater.requests.get = lambda url, *a, **k: Resp(release)
role = {"r": "owner"}
updater._role = lambda: role["r"]
ctl = {}
updater._control = lambda: ctl
ok, forced, why = updater.decision(now=now)
check(ok, "the owner's BSO may update straight away")
role["r"] = "supervisor"
ok, forced, why = updater.decision(now=now)
check(not ok and "6 hours" in why, "supervisors get it 6 hours later")
check(updater.decision(now=now + timedelta(hours=7))[0], "…and then it's allowed")
role["r"] = "bot"
check(not updater.decision(now=now + timedelta(hours=7))[0] and updater.decision(now=now + timedelta(hours=25))[0], "the bot waits 24 hours")
ctl["hold"] = True
check(not updater.decision(now=now + timedelta(days=5))[0], "a hold in the Master Console stops every update")
ctl["min_version"] = "1.0.50"
ok, forced, why = updater.decision(now=now)
check(ok and forced, "a minimum version overrides the hold and the waiting (urgent fixes)")
ctl.clear()
ctl["release_now"] = "2026-11-02T10:30:00Z"
check(updater.decision(now=now)[0], "Release to everyone now skips the staged roll-out")
ctl.clear()
s = updater._state()
s["manifest_sig"] = "00" * 256
updater._save(s)
check(not updater.decision(now=now + timedelta(days=3))[0], "a release whose signature doesn't check out is never installed")
updater.check(force=True)

# ---------------------------------------------------------------- downloading
def fake_download(url, dest):
    Path(dest).write_bytes(installer)
    return dest
updater._download = fake_download
exe = updater.fetch()
check(exe.exists(), "the installer is downloaded and verified")
exe.unlink()
updater._download = lambda url, dest: Path(dest).write_bytes(installer + b"tampered") or dest
try:
    updater.fetch()
    ok = False
except RuntimeError as e:
    ok = "signature" in str(e) and not any(updater.DIR.glob("BSO-Setup-1.0.52.exe"))
check(ok, "a tampered download is deleted and not installed")
con = bot.db_connect("outreach.db")
bk = updater.backup_db()
check(bk and bk.exists() and bk.stat().st_size > 0, "the database is backed up before an update")
ok, msg = updater.install_now()
check(not ok and "Windows app" in msg, "running from source never tries to install")

# ---------------------------------------------------------------- when it installs
calls = []
updater.install_now = lambda reason="": calls.append(reason) or (True, "started")
role["r"] = "bot"
con.execute("INSERT INTO leads(email, status, step, next_send_at, created_at) VALUES ('soon@x.com','active',1,?, '2026-01-01')",
            (bot.iso(bot.now_utc() + timedelta(minutes=5)),))
con.commit()
updater.tick(con=con, now=now + timedelta(hours=25))
check(not calls, "the bot doesn't update while an email is due in the next 15 minutes")
con.execute("DELETE FROM leads")
con.commit()
updater.tick(con=con, now=now + timedelta(hours=25))
check(calls and "bot" in calls[-1], "…and updates at the next quiet moment")
role["r"] = "supervisor"
calls.clear()
updater.tick(idle_minutes=5, now=now + timedelta(hours=8))
check(not calls, "a supervisor isn't updated without asking in the first 3 days")
s = updater._state()
s["first_seen"] = {"1.0.52": bot.iso(now - timedelta(days=4))}
updater._save(s)
updater.tick(idle_minutes=5, now=now + timedelta(days=1))
check(not calls, "…nor while they're working")
updater.tick(idle_minutes=45, now=now + timedelta(days=1))
check(calls and "3 days" in calls[-1], "after 3 days it updates at a quiet moment")
calls.clear()
s = updater._state()
s["first_seen"] = {}
s["tonight"] = True
updater._save(s)
updater.tick(idle_minutes=0, now=datetime(2026, 11, 3, 2, 0).astimezone())
check(calls and "tonight" in calls[-1], "Update tonight installs between 01:00 and 05:00")

# ---------------------------------------------------------------- after the update
s = updater._state()
s["installing"] = "1.0.47"
updater._save(s)
check(updater.mark_healthy() == "healthy" and (updater.DIR / "healthy-1.0.47.flag").exists(), "the new version tells the helper it started (no rollback)")
(updater.DIR / "rolled-back.flag").write_text("1.0.52")
check(updater.mark_healthy() == "rolled back" and updater.banner()["rolled_back"] == "1.0.52", "a failed update that was rolled back is reported")
script = updater.helper_script(Path("C:/u/new.exe"), Path("C:/u/old.exe"), "C:/BSO/BSO.exe", "1.0.52", True)
check("/VERYSILENT" in script and "/CURRENTUSER" in script and "old.exe" in script and "--background" in script and "healthy-1.0.52.flag" in script,
      "the helper installs silently for this user, restarts BSO and goes back if it doesn't start")
check((root / "assets" / "bso-publisher.cer").exists(), "the BlacSwanken publisher certificate is bundled for trusting on company laptops")
print(f"\nAll {n} updater checks passed.")
