"""Populate Peptide.library_specifications from a price list PDF."""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.matching import match_name, suggest
from app.library.price_list_parser import merge_specs, parse_price_list_pdf
from app.models import Peptide


@dataclass
class ImportResult:
    updated: int = 0  # library cards whose specs were set or extended
    matches: list[tuple[str, str, str]] = field(default_factory=list)  # (vendor name, card name, how)
    unmatched: list[tuple[str, list[str]]] = field(default_factory=list)  # (vendor name, suggested cards)


def populate_from_price_list(session: Session, pdf_path: str | Path) -> ImportResult:
    """Annotate existing library cards with the specs a price list offers.

    Vendor names are matched to cards by app.library.matching (spacing, punctuation, case, typos).
    It never creates a Peptide: a name nothing matches is reported with close suggestions and left
    alone, so a vendor's spelling variants can't spawn duplicate library cards. Specs are added to
    whatever the card already lists, so importing a second vendor's list never drops the first's.
    """
    specs_by_name = parse_price_list_pdf(Path(pdf_path))
    cards = session.scalars(select(Peptide)).all()
    result = ImportResult()
    pending: dict[int, tuple[Peptide, list[str]]] = {}

    for vendor_name, specs in sorted(specs_by_name.items()):
        match = match_name(vendor_name, cards)
        if match is None:
            result.unmatched.append((vendor_name, suggest(vendor_name, cards)))
            continue
        for card in match.cards:
            pending.setdefault(card.id, (card, []))[1].append(specs)
            result.matches.append((vendor_name, card.name, match.how))

    for card, spec_lists in pending.values():
        card.library_specifications = merge_specs(card.library_specifications or "", *spec_lists)
    session.commit()
    result.updated = len(pending)
    return result
