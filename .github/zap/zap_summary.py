"""Prints the alert types of a ZAP JSON report, highest risk first. Standard library only; never fails (the scan is advisory)."""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "zap-report.json")
if not path.exists():
    print("no ZAP report was written")
    raise SystemExit(0)
alerts = [a for site in json.loads(path.read_text(encoding="utf-8")).get("site", []) for a in site.get("alerts", [])]
print(f"{len(alerts)} alert type(s)")
for alert in sorted(alerts, key=lambda a: -int(a.get("riskcode", 0))):
    print(f"  [{alert.get('riskdesc')}] {alert.get('name')} x{alert.get('count')}")
