"""Import parsed peptide reference sheet files into the database: `python -m app.library_load_sheets <sheet1.txt> [<sheet2.txt> ...]`.

Each argument is a path to an already-on-disk text file the user has explicitly provided.
This script does not scan directories or glob patterns.
"""

import sys
from pathlib import Path

from app.db import SessionLocal
from app.library.loader import load_sheets
from app.library.sheet_parser import parse_sheet
from app.migrate import upgrade_db


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m app.library_load_sheets <sheet1.txt> [<sheet2.txt> ...]")

    upgrade_db()

    # Parse each named file
    sheets = []
    for filepath in sys.argv[1:]:
        path = Path(filepath)
        if not path.exists():
            sys.exit(f"No file at {path}.")
        text = path.read_text(encoding="utf-8")
        sheet = parse_sheet(text)
        # Add empty usage_tips by default (curated tips are added manually later)
        sheet["usage_tips"] = []
        sheets.append(sheet)

    # Load all sheets at once
    with SessionLocal() as session:
        print(load_sheets(session, sheets).summary())


if __name__ == "__main__":
    main()
