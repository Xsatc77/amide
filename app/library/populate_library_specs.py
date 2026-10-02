"""Populate Peptide.library_specifications from a price list PDF."""

from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Peptide, PeptideSource, DoseUnit
from app.library.price_list_parser import parse_price_list_pdf


def populate_from_price_list(session: Session, pdf_path: str | Path) -> dict[str, str]:
    """
    Parse a price list PDF and populate Peptide.library_specifications.

    Returns a dict with 'created', 'updated', and 'skipped' counts.
    """
    pdf_path = Path(pdf_path)
    specs_by_name = parse_price_list_pdf(pdf_path)

    stats = {'created': 0, 'updated': 0, 'skipped': 0}

    # Exclude non-peptide items (solvents, supplies, etc.)
    exclude_keywords = [
        'water', 'sterile', 'bacteriostatic', 'acetic acid', 'solvent',
        'supply', 'lemon bottle', 'syringe'
    ]

    for name, specs in sorted(specs_by_name.items()):
        # Skip items matching exclude keywords
        name_lower = name.lower()
        if any(keyword in name_lower for keyword in exclude_keywords):
            stats['skipped'] += 1
            continue

        # Find or create peptide
        peptide = session.query(Peptide).filter_by(name=name).first()

        if peptide:
            # Update existing peptide
            peptide.library_specifications = specs
            stats['updated'] += 1
        else:
            # Create new peptide
            peptide = Peptide(
                name=name,
                library_specifications=specs,
                source=PeptideSource.CUSTOM
            )
            session.add(peptide)
            stats['created'] += 1

    session.commit()
    return stats
