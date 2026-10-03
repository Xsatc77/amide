"""Populate Peptide.library_specifications from a price list PDF."""

from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Peptide
from app.library.price_list_parser import parse_price_list_pdf


def populate_from_price_list(session: Session, pdf_path: str | Path) -> dict[str, int]:
    """Annotate existing library cards with the specs a price list offers.

    Never creates a Peptide: a price-list name with no exact match on an existing card is counted
    as 'unmatched' and left alone, so a vendor's spelling variants can't spawn duplicate library
    cards. Returns {'updated', 'unmatched'} counts.
    """
    specs_by_name = parse_price_list_pdf(Path(pdf_path))
    stats = {'updated': 0, 'unmatched': 0}

    for name, specs in sorted(specs_by_name.items()):
        peptide = session.query(Peptide).filter_by(name=name).first()
        if peptide is None:
            stats['unmatched'] += 1
            continue
        peptide.library_specifications = specs
        stats['updated'] += 1

    session.commit()
    return stats
