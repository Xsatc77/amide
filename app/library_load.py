"""Reload imported peptide cards into the database: `python -m app.library_load [cards.json]`.

Use this on a fresh database or another server; the PDF is not needed, only the data/library folder.
"""

import json
import sys
from pathlib import Path

from app import config
from app.db import SessionLocal
from app.library.loader import load_cards
from app.migrate import upgrade_db


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else config.CARDS_JSON
    if not path.exists():
        sys.exit(f"No cards file at {path}. Run tools/import_cards.py first.")
    upgrade_db()
    cards = json.loads(path.read_text(encoding="utf-8"))
    with SessionLocal() as session:
        print(load_cards(session, cards).summary())


if __name__ == "__main__":
    main()
