"""Import parsed peptide reference sheet files into the database: `python -m app.library_load_sheets <sheet1.txt> [<sheet2.txt> ...]`.

Each argument is a path to an already-on-disk text file the user has explicitly provided.
This script does not scan directories or glob patterns.
"""

import sys
from pathlib import Path

from app.db import SessionLocal
from app.library.loader import load_sheets
from app.library.sheet_parser import UnrecognizedSheetError, parse_sheet, simple_sections
from app.migrate import upgrade_db


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m app.library_load_sheets <sheet1.txt> [<sheet2.txt> ...]")

    upgrade_db()

    # Parse each named file
    sheets = []
    skipped: list[str] = []
    for filepath in sys.argv[1:]:
        path = Path(filepath)
        if not path.exists():
            sys.exit(f"No file at {path}.")
        text = path.read_text(encoding="utf-8")
        try:
            sheet = parse_sheet(text)
        except UnrecognizedSheetError:
            print(f"Skipped {path}: does not look like a peptide reference sheet")
            skipped.append(str(path))
            continue
        # Add empty usage_tips by default (curated tips are added manually later)
        sheet["usage_tips"] = []
        sheet["sheet_sections_simple"] = simple_sections(sheet.get("sheet_sections") or {})      # the readable sections shown on the library page
        sheets.append(sheet)

    # Load all sheets at once
    with SessionLocal() as session:
        print(load_sheets(session, sheets).summary())
    print(f"{len(skipped)} skipped (not a peptide reference sheet)")


if __name__ == "__main__":
    main()
