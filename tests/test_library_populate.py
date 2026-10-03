from sqlalchemy import func, select

from app.db import SessionLocal
from app.library import populate_library_specs
from app.models import Peptide


def test_populate_annotates_existing_cards_and_never_creates_new_ones(db, monkeypatch):
    monkeypatch.setattr(populate_library_specs, "parse_price_list_pdf", lambda path: {
        "Retatrutide": "5mg, 10mg",
        "Retatrutyde": "5mg",  # vendor spelling variant: must not become its own library card
    })
    with SessionLocal() as s:
        before = s.scalar(select(func.count()).select_from(Peptide))
        stats = populate_library_specs.populate_from_price_list(s, "ignored.pdf")
        after = s.scalar(select(func.count()).select_from(Peptide))
        spec = s.scalar(select(Peptide.library_specifications).where(Peptide.name == "Retatrutide"))
        assert s.scalar(select(Peptide).where(Peptide.name == "Retatrutyde")) is None
    assert stats == {"updated": 1, "unmatched": 1}
    assert after == before
    assert spec == "5mg, 10mg"
