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

    # Collect specs by peptide name
    specs_by_peptide = defaultdict(set)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Try table extraction first (most reliable)
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    current_product = None
                    for row in table:
                        if not row:
                            continue

                        # Row format: [function, product, cat_no, specification, price]
                        # Rows may have None values
                        row_text = [str(cell).strip() if cell else "" for cell in row]

                        # Find specification in this row
                        spec_text = None
                        for cell in row_text:
                            if '*vial' in cell.lower():
                                spec_text = cell
                                break

                        # If we find a spec, extract it and try to get product name
                        if spec_text:
                            spec_match = re.search(
                                r'(\d+(?:\.\d+)?)\s*(mg|mcg|iu|IU)\s*\*\s*\d+vials',
                                spec_text,
                                re.IGNORECASE
                            )
                            if spec_match:
                                amount = spec_match.group(1)
                                unit = spec_match.group(2)
                                if unit.lower() == 'iu':
                                    unit = 'IU'
                                else:
                                    unit = unit.lower()
                                spec = f"{amount}{unit}"

                                # Get product name from the row
                                for cell in row_text:
                                    # Product names are longer and don't look like Cat.No or specs
                                    if cell and len(cell) > 2 and '*vial' not in cell.lower():
                                        if not re.match(r'^[A-Z]+\d+', cell):  # Not Cat.No
                                            if not cell.startswith('$'):  # Not price
                                                current_product = cell
                                                break

                                if current_product:
                                    specs_by_peptide[current_product].add(spec)

            # Fallback to text extraction if no tables
            else:
                text = page.extract_text()
                if text:
                    lines = text.split('\n')
                    current_product = None
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue

                        spec_match = re.search(
                            r'(\d+(?:\.\d+)?)\s*(mg|mcg|iu|IU)\s*\*\s*\d+vials',
                            line,
                            re.IGNORECASE
                        )

                        if spec_match:
                            amount = spec_match.group(1)
                            unit = spec_match.group(2).lower()
                            if unit == 'iu':
                                unit = 'IU'
                            spec = f"{amount}{unit}"

                            # Try to extract product name from line
                            product_match = re.search(r'^([A-Za-z0-9\s\-\+\(\)]+?)\s+[A-Z]\w+\s+\d+', line)
                            if product_match:
                                current_product = product_match.group(1).strip()

                            if current_product:
                                specs_by_peptide[current_product].add(spec)

                        elif re.match(r'^[A-Z][A-Za-z0-9\s\-\+\(\)]+$', line) and len(line) > 3:
                            if not re.search(r'\d+\s*\$', line):
                                current_product = line.strip()

    # Convert sets to sorted comma-separated strings
    result = {
        name: ", ".join(sorted(specs, key=_spec_sort_key))
        for name, specs in specs_by_peptide.items()
    }

    return result


def _spec_sort_key(spec: str) -> tuple:
    """Sort specs by amount, then by unit (mg < mcg < IU)."""
    match = re.match(r'([\d.]+)(mg|mcg|IU)', spec)
    if not match:
        return (float('inf'), 999)

    amount = float(match.group(1))
    unit = match.group(2)
    unit_order = {'mg': 0, 'mcg': 1, 'IU': 2}
    return (unit_order.get(unit, 999), amount)
