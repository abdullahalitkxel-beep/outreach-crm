"""
Build preparation (run by the GitHub build before PyInstaller):

  python packaging/prepare.py <build_number>

Writes:
  version.txt                     app version shown in the app (1.0.<build>)
  build/version_info.txt          Windows file properties: publisher BlacSwanken, product, copyright
  build/EULA.txt                  licence text shown by the installer
  build/AppxManifest.xml          Microsoft Store package manifest
  legal/THIRD_PARTY_NOTICES.txt   licences of every bundled open-source component
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=True)

build_no = int(sys.argv[1]) if len(sys.argv) > 1 else 0
version = f"1.0.{build_no}"
cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
publisher = cfg.get("publisher") or "BlacSwanken"
company = (cfg.get("legal") or {}).get("company_name") or publisher
copyright_ = f"© 2026 {company}. All rights reserved."

(ROOT / "version.txt").write_text(version, encoding="utf-8")

# ---- Windows version resource (File → Properties → Details)
v4 = (1, 0, build_no, 0)
(BUILD / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v4}, prodvers={v4}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', {json.dumps(company)}),
      StringStruct('FileDescription', 'Outreach CRM'),
      StringStruct('FileVersion', '{version}.0'),
      StringStruct('InternalName', 'OutreachCRM'),
      StringStruct('LegalCopyright', {json.dumps(copyright_)}),
      StringStruct('OriginalFilename', 'OutreachCRM.exe'),
      StringStruct('ProductName', 'Outreach CRM'),
      StringStruct('ProductVersion', '{version}.0')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

# ---- installer licence page
sys.path.insert(0, str(ROOT))
import legal  # noqa: E402

eula = "\n\n".join(legal.to_text(legal.source(d, cfg)) for d in ("terms", "acceptable-use", "privacy"))
(BUILD / "EULA.txt").write_text(eula, encoding="utf-8-sig")

# ---- Store manifest (identity comes from Partner Center via repository variables)
identity = os.getenv("STORE_IDENTITY_NAME") or "BlacSwanken.OutreachCRM"
store_publisher = os.getenv("STORE_PUBLISHER") or "CN=BlacSwanken"
display_publisher = os.getenv("STORE_PUBLISHER_DISPLAY") or company
manifest = (ROOT / "packaging" / "AppxManifest.template.xml").read_text(encoding="utf-8")
for k, v in {"IDENTITY": identity, "PUBLISHER": store_publisher, "PUBLISHER_DISPLAY": display_publisher,
             "VERSION": ".".join(map(str, v4))}.items():
    manifest = manifest.replace("{{" + k + "}}", v.replace("&", "&amp;"))
(BUILD / "AppxManifest.xml").write_text(manifest, encoding="utf-8")

# ---- third-party notices for everything bundled into the app
BUILD_ONLY = {"pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "pip", "setuptools", "wheel",
              "pip-licenses", "prettytable", "wcwidth", "packaging", "macholib", "tomli"}
out = subprocess.run([sys.executable, "-m", "piplicenses", "--from=mixed", "--with-urls", "--with-license-file",
                      "--no-license-path", "--format=json"], capture_output=True, text=True, check=True).stdout
pkgs = [p for p in json.loads(out) if p["Name"].lower() not in BUILD_ONLY]
parts = ["Outreach CRM includes the following open-source software. Each component is provided under its own",
         "licence, reproduced below. Nothing in the Outreach CRM Terms of Use limits your rights under these licences.", ""]
py_license = Path(sys.base_prefix) / "LICENSE.txt"
parts += ["=" * 78, f"Python {sys.version.split()[0]} — Python Software Foundation License", "https://www.python.org/", "=" * 78,
          py_license.read_text(encoding="utf-8", errors="replace") if py_license.exists() else "PSF License Agreement", ""]
for p in sorted(pkgs, key=lambda p: p["Name"].lower()):
    note = ""
    if p["Name"].lower() == "pyinstaller":
        note = ("(Only the PyInstaller bootloader is included in this app. It is distributed under the GPL with "
                "PyInstaller's bootloader exception, which permits distribution with programs under any licence.)\n")
    parts += ["=" * 78, f"{p['Name']} {p['Version']} — {p['License']}", p.get("URL") or "", "=" * 78, note + (p.get("LicenseText") or "").strip(), ""]
(ROOT / "legal" / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(parts), encoding="utf-8")

print(f"prepared version {version} for {company}; {len(pkgs)} third-party packages listed; identity {identity} / {store_publisher}")
