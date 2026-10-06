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
        result = populate_from_price_list(session, pdf_path)
        print(f"Updated {result.updated} library cards from {len(result.matches)} price-list names.")
        if result.unmatched:
            print(f"\n{len(result.unmatched)} names matched no card (nothing was created for them). To have a future "
                  "import match one, add that spelling to the right card's Aliases:")
            for vendor_name, suggestions in result.unmatched:
                print(f"  {vendor_name!r}" + (f"  -> closest: {', '.join(suggestions)}" if suggestions else ""))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
