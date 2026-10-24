"""The 1-minute check of the release secrets (packaging/check_secrets.py) names the wrong secret.
    python tests/secrets_check_test.py"""
import base64, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="secrets-test-"))
n = 0
def check(cond, what):
    global n
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)
    n += 1

def sh(*a):
    subprocess.run(a, check=True, capture_output=True, cwd=tmp)

# a throwaway key pair and certificate, like the real ones
sh("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", "upd.pem")
sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "cs.key", "-out", "cs.crt", "-days", "30", "-subj", "/CN=BlacSwanken",
   "-addext", "extendedKeyUsage=codeSigning")
sh("openssl", "pkcs12", "-export", "-inkey", "cs.key", "-in", "cs.crt", "-out", "cs.pfx", "-passout", "pass:pw123",
   "-certpbe", "AES-256-CBC", "-keypbe", "AES-256-CBC", "-macalg", "SHA256")
sh("openssl", "x509", "-in", "cs.crt", "-outform", "der", "-out", "cs.cer")
sh("openssl", "pkcs8", "-topk8", "-in", "upd.pem", "-out", "upd_enc.pem", "-passout", "pass:keypw")
mod = subprocess.run(["openssl", "rsa", "-in", "upd.pem", "-noout", "-modulus"], capture_output=True, text=True, cwd=tmp).stdout.split("=", 1)[1].strip()
fake = tmp / "repo"
(fake / "assets").mkdir(parents=True)
shutil.copy(tmp / "cs.cer", fake / "assets" / "bso-publisher.cer")
(fake / "update_key.json").write_text(json.dumps({"n": mod, "e": 65537}))

good = {"CODESIGN_PFX_BASE64": base64.b64encode((tmp / "cs.pfx").read_bytes()).decode(), "CODESIGN_PFX_PASSWORD": "pw123",
        "UPDATE_SIGNING_KEY": (tmp / "upd.pem").read_text()}

def run(**over):
    env = {k: v for k, v in os.environ.items() if k not in ("CODESIGN_PFX_BASE64", "CODESIGN_PFX_PASSWORD", "UPDATE_SIGNING_KEY",
                                                              "UPDATE_SIGNING_KEY_PASSWORD", "RELEASES_TOKEN", "GITHUB_ACTIONS")}
    env.update(good, BSO_ROOT=str(fake))
    env.update({k: v for k, v in over.items() if v is not None})
    for k, v in over.items():
        if v is None:
            env.pop(k, None)
    r = subprocess.run([sys.executable, str(root / "packaging" / "check_secrets.py")], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout

rc, out = run()
check(rc == 0 and "All release secrets are correct" in out, "correct secrets pass")
rc, out = run(CODESIGN_PFX_PASSWORD="wrong")
check(rc == 1 and "PROBLEM CODESIGN_PFX_PASSWORD" in out, "a wrong certificate password is named")
rc, out = run(CODESIGN_PFX_BASE64=good["CODESIGN_PFX_BASE64"][:-7] + "%%%")
check(rc == 1 and "PROBLEM CODESIGN_PFX_BASE64" in out and "base64" in out, "a damaged certificate paste is named")
rc, out = run(UPDATE_SIGNING_KEY=(tmp / "upd_enc.pem").read_text())
check(rc == 1 and "PROBLEM UPDATE_SIGNING_KEY" in out and "password" in out, "a password-protected update key is explained")
rc, out = run(UPDATE_SIGNING_KEY=(tmp / "upd_enc.pem").read_text(), UPDATE_SIGNING_KEY_PASSWORD="keypw")
check(rc == 0, "…and accepted with its password")
rc, out = run(UPDATE_SIGNING_KEY=(tmp / "cs.key").read_text())
check(rc == 1 and "not the one BSO trusts" in out, "a different key (e.g. the certificate's) is refused")
rc, out = run(UPDATE_SIGNING_KEY=good["UPDATE_SIGNING_KEY"][40:])
check(rc == 1 and "PROBLEM UPDATE_SIGNING_KEY" in out, "a key pasted without its first line is named")
(fake / "assets" / "bso-publisher.cer").write_bytes(b"other")
rc, out = run()
check(rc == 1 and "different certificate" in out, "a certificate that doesn't match the bundled one is refused")
shutil.copy(tmp / "cs.cer", fake / "assets" / "bso-publisher.cer")
rc, out = run(GITHUB_ACTIONS="true", CODESIGN_PFX_PASSWORD="wrong")
check("::error title=Secret check::CODESIGN_PFX_PASSWORD" in out, "on GitHub the problem shows as an error on the run")
rc, out = run(CODESIGN_PFX_BASE64=None, UPDATE_SIGNING_KEY=None)
check(rc == 0 and "unsigned" in out and "automatically" in out, "missing secrets are reported as notes, not failures")
shutil.rmtree(tmp, ignore_errors=True)
print(f"\nAll {n} secret checks passed.")
