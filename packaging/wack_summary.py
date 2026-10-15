"""Summarise a Windows App Certification Kit report as GitHub annotations: python packaging/wack_summary.py report.xml"""
import sys
import xml.etree.ElementTree as ET

try:
    root = ET.parse(sys.argv[1]).getroot()
except Exception as e:
    print(f"::warning title=App Certification Kit::No report produced ({e})")
    sys.exit(0)

overall = root.get("OVERALL_RESULT") or "UNKNOWN"
failed, warned, passed = [], [], 0
for test in root.iter("TEST"):
    name = test.get("NAME") or test.get("ID")
    res = (test.findtext("RESULT") or "").strip().upper()
    if res == "PASS":
        passed += 1
    elif res == "FAIL":
        msgs = [m.get("TEXT") for m in test.iter("MESSAGE") if m.get("TEXT")]
        failed.append(f"{name}: {' | '.join(msgs)[:400]}")
    elif res in ("WARNING", "WARN"):
        warned.append(name)

print(f"::notice title=App Certification Kit::Overall {overall}: {passed} passed, {len(failed)} failed, {len(warned)} warnings")
for f in failed:
    print(f"::warning title=WACK failure::{f}")
for w in warned:
    print(f"::notice title=WACK warning::{w}")
