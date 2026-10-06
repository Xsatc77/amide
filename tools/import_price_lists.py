#!/usr/bin/env python3
"""Import vendor price-list PDFs: vendors, pack prices (kits and boxes), warehouse region, and library vial sizes.

Usage: python tools/import_price_lists.py PATH [PATH ...] [--dry-run] [--warehouse us|china]

PATH is a PDF or a folder of them. The vendor, optional warehouse and date come from the filename:
"<Vendor> - [<Warehouse> ]Price List - <YYYY-MM-DD>.pdf". Re-importing a file replaces its rows.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.library.price_lists.importer import format_reports, run_import  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    parser.add_argument("--warehouse", choices=["us", "china"], help="override the detected warehouse (one file only)")
    args = parser.parse_args(argv)
    if args.warehouse and (len(args.paths) != 1 or args.paths[0].is_dir()):
        parser.error("--warehouse needs exactly one file")
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        parser.error(f"not found: {', '.join(map(str, missing))}")
    reports = run_import(args.paths, SessionLocal, warehouse=args.warehouse, dry_run=args.dry_run)
    print(format_reports(reports, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
