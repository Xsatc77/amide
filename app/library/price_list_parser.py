"""Parse price list PDFs to extract peptide specifications."""

import re
from pathlib import Path
from collections import defaultdict

import pdfplumber


def parse_price_list_pdf(pdf_path: str | Path) -> dict[str, str]:
    """
    Parse a price list PDF and extract peptide names with their available specifications.

    Returns a dict mapping peptide name to comma-separated specs (e.g., "5mg, 10mg, 15mg").
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Price list PDF not found: {pdf_path}")

    specs_by_peptide = defaultdict(set)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue

            for table in tables:
                current_product = None
                # Table: [category/function, product, cat_no, specification, price]
                for row in table:
                    if not row or len(row) < 4:
                        continue

                    # Get text from columns (row[1] = product, row[3] = spec)
                    product = str(row[1]).strip() if row[1] else ""
                    spec = str(row[3]).strip() if row[3] else ""

                    # Update product if this row has one (skip header)
                    if product and product not in ("PRODUCT", ""):
                        current_product = product

                    # Extract dose/unit from spec if it contains vial info
                    if spec and "vial" in spec.lower() and current_product:
                        # Match: 5mg*10vials, 12iu*10vials, etc.
                        m = re.search(r'(\d+(?:\.\d+)?)\s*(mg|mcg|iu|IU)', spec, re.IGNORECASE)
                        if m:
                            amount, unit = m.group(1), m.group(2)
                            # Normalize unit
                            if unit.lower() == 'iu':
                                unit = 'IU'
                            else:
                                unit = unit.lower()

                            specs_by_peptide[current_product].add(f"{amount}{unit}")

    return {name: merge_specs(*specs) for name, specs in specs_by_peptide.items()}


def merge_specs(*spec_lists: str) -> str:
    """Union of comma-separated spec strings (e.g. "5mg, 10mg"), sorted by unit then amount."""
    specs = {s.strip() for spec_list in spec_lists for s in spec_list.split(",") if s.strip()}
    return ", ".join(sorted(specs, key=lambda s: (_unit_order(s), _amount_from_spec(s))))


def _unit_order(spec: str) -> int:
    """Return sort order for unit in spec (e.g. '5mg' -> 0)."""
    if 'IU' in spec:
        return 2
    elif 'mcg' in spec:
        return 1
    else:  # mg
        return 0


def _amount_from_spec(spec: str) -> float:
    """Extract numeric amount from spec (e.g. '5mg' -> 5.0)."""
    m = re.match(r'([\d.]+)', spec)
    return float(m.group(1)) if m else 0.0
