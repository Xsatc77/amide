"""A reference sheet joins the library card it is about even when the names differ a little, instead of creating a second entry."""

import pytest
from sqlalchemy import select

from app.library.loader import load_sheets
from app.models import Peptide, PeptideSource


def sheet(name, aliases=()):
    return {"name": name, "aliases": list(aliases), "tags": [], "half_life_text": None, "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None, "stack_relations": [], "monitoring_tests": [], "bioavailability_text": None, "tmax_text": None, "storage_before_text": None,
            "storage_after_text": None, "storage_temperature_text": None, "legal_status_text": None, "cost_estimate_text": None, "sheet_sections": {}, "usage_tips": []}


@pytest.fixture
def cleanup(db):
    yield
    db.rollback()
    db.query(Peptide).filter(Peptide.name.like("Sheetmatch%")).delete(synchronize_session=False)
    db.commit()


def names(db):
    return sorted(p.name for p in db.scalars(select(Peptide).where(Peptide.name.like("Sheetmatch%"))))


def test_a_sheet_whose_name_adds_a_parenthetical_joins_the_card_and_renames_it(db, cleanup):
    card = Peptide(name="Sheetmatch Zorbulin", source=PeptideSource.CARD, card_class="old")
    db.add(card)
    db.commit()
    card_id = card.id
    report = load_sheets(db, [sheet("Sheetmatch Zorbulin (ZRB)")])
    assert names(db) == ["Sheetmatch Zorbulin (ZRB)"] and report.created == []
    joined = db.scalar(select(Peptide).where(Peptide.name == "Sheetmatch Zorbulin (ZRB)"))
    assert joined.id == card_id and joined.source is PeptideSource.SHEET and joined.card_class is None


def test_a_sheet_whose_alias_is_the_cards_name_joins_it(db, cleanup):
    db.add(Peptide(name="Sheetmatch Tractocile Old", source=PeptideSource.CARD))
    db.commit()
    load_sheets(db, [sheet("Sheetmatch Atosiban", aliases=["Sheetmatch Tractocile Old"])])
    assert names(db) == ["Sheetmatch Atosiban"]


def test_a_card_the_user_made_or_edited_is_never_taken_over(db, cleanup):
    db.add(Peptide(name="Sheetmatch Mine", source=PeptideSource.CUSTOM, notes="my notes"))
    db.commit()
    load_sheets(db, [sheet("Sheetmatch Mine (Longer Name)")])
    assert names(db) == ["Sheetmatch Mine", "Sheetmatch Mine (Longer Name)"]


def test_two_possible_cards_means_no_guessing(db, cleanup):
    db.add_all([Peptide(name="Sheetmatch Dup", source=PeptideSource.CARD), Peptide(name="Sheetmatch Dup (older)", source=PeptideSource.CARD)])
    db.commit()
    load_sheets(db, [sheet("Sheetmatch Dup (new)")])
    assert len(names(db)) == 3


def test_different_products_stay_separate(db, cleanup):
    db.add(Peptide(name="Sheetmatch CJC With DAC", source=PeptideSource.CARD))
    db.commit()
    load_sheets(db, [sheet("Sheetmatch CJC Without DAC")])
    assert len(names(db)) == 2
