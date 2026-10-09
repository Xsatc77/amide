"""The base library that ships with Amide: fills a missing or empty entry from base_library.json, never one that has card data."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.loader import load_sheets
from app.models import Peptide, PeptideSource

DATA = Path(__file__).with_name("base_library.json")


def is_empty_entry(peptide: Peptide) -> bool:
    return not (peptide.summary or peptide.tags or peptide.card_class or peptide.card_details or peptide.sheet_sections)


def _fillable(peptide: Peptide) -> bool:
    """An empty seed entry, or an entry the person added that is completely blank (no card data, no aliases, no notes). Anything with
    anything in it is left exactly as it is."""
    if not is_empty_entry(peptide):
        return False
    if peptide.source in (PeptideSource.CARD, PeptideSource.STARTER):
        return True
    return peptide.source is PeptideSource.CUSTOM and not (peptide.aliases or peptide.notes)


def load_base_library(session: Session, records: list[dict] | None = None) -> list[str]:
    """Create or fill what is missing; return the names touched. An entry that already has data, and anything custom, is left alone."""
    existing = {p.name.lower(): p for p in session.scalars(select(Peptide))}
    wanted = []
    for record in records if records is not None else json.loads(DATA.read_text(encoding="utf-8")):
        peptide = existing.get(record["name"].lower())
        if peptide is None or _fillable(peptide):
            wanted.append(record)
    if wanted:
        load_sheets(session, wanted, force_names={r["name"] for r in wanted})
    return [r["name"] for r in wanted]
