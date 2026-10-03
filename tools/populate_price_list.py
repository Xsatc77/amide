#!/usr/bin/env python3
"""
Populate Peptide.library_specifications from a price list PDF.

Usage: python tools/populate_price_list.py /path/to/price_list.pdf
"""

import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SessionLocal
from app.library.populate_library_specs import populate_from_price_list


def main():
    if len(sys.argv) != 2:
        print("Usage: python tools/populate_price_list.py <pdf_path>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: {pdf_path} not found")
        sys.exit(1)

    session = SessionLocal()
    try:
        stats = populate_from_price_list(session, pdf_path)
        print(f"Updated: {stats['updated']}, Unmatched (no library card with that exact name): {stats['unmatched']}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
