"""Filling the shipped base library into a database: missing and empty entries are filled, entries with data and custom ones never are."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.library.base_library import is_empty_entry, load_base_library
from app.models import Peptide, PeptideSource


def rec(name, **kw):
    base = {"name": name, "aliases": [f"{name} alias"], "tags": ["Test"], "summary": f"Summary of {name}.", "usage_tips": [],
            "sheet_sections": {"summary": "x"}, "sheet_sections_simple": {"summary": "x"},
            "dosing_tiers": [{"level": "Beginner", "dose_text": "1 mg", "frequency_text": "daily", "time_of_day": None}],
            "cycle": {"on_weeks": 8, "off_weeks": 4, "note": None}, "stack_relations": [], "monitoring_tests": [],
            "cost_estimate_text": None}
    return {**base, **kw}


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s
        s.query(Peptide).filter(Peptide.name.like("Basetest %")).delete(synchronize_session=False)
        s.commit()


def get(db, name):
    return db.scalar(select(Peptide).where(Peptide.name == name))


def test_a_missing_peptide_is_created_with_its_card(db):
    assert load_base_library(db, [rec("Basetest New")]) == ["Basetest New"]
    p = get(db, "Basetest New")
    assert p.source is PeptideSource.SHEET and p.aliases == "Basetest New alias" and p.summary and p.dosing_tiers[0].dose_text == "1 mg"
    assert p.cycle.on_weeks == 8


def test_an_empty_seed_entry_is_filled_keeping_its_id_and_a_starter_entry_too(db):
    card = Peptide(name="Basetest Seed", source=PeptideSource.CARD, card_number=999)
    starter = Peptide(name="Basetest Starter", source=PeptideSource.STARTER)
    db.add_all([card, starter])
    db.commit()
    ids = (card.id, starter.id)
    assert is_empty_entry(card)
    load_base_library(db, [rec("Basetest Seed"), rec("Basetest Starter")])
    assert (get(db, "Basetest Seed").id, get(db, "Basetest Starter").id) == ids
    assert get(db, "Basetest Seed").summary and get(db, "Basetest Starter").summary and not is_empty_entry(get(db, "Basetest Seed"))


def test_an_entry_with_data_and_a_custom_entry_are_left_exactly_as_they_are(db):
    db.add_all([Peptide(name="Basetest Has Data", source=PeptideSource.SHEET, summary="My own words.", tags=["Mine"]),
                Peptide(name="Basetest Custom", source=PeptideSource.CUSTOM, notes="private")])
    db.commit()
    assert load_base_library(db, [rec("Basetest Has Data"), rec("Basetest Custom")]) == []
    assert get(db, "Basetest Has Data").summary == "My own words." and get(db, "Basetest Custom").notes == "private"
    assert get(db, "Basetest Custom").summary is None


def test_owner_notes_survive_a_fill_and_a_second_run_changes_nothing(db):
    db.add(Peptide(name="Basetest Notes", source=PeptideSource.CARD, notes="keep me"))
    db.commit()
    assert load_base_library(db, [rec("Basetest Notes")]) == ["Basetest Notes"]
    assert get(db, "Basetest Notes").notes == "keep me"
    assert load_base_library(db, [rec("Basetest Notes")]) == []


@pytest.mark.parametrize("field,value", [("summary", "s"), ("tags", ["t"]), ("card_class", "c"), ("card_details", {"a": 1}), ("sheet_sections", {"a": 1})])
def test_any_card_data_means_the_entry_is_not_empty(field, value):
    assert not is_empty_entry(Peptide(name="x", **{field: value}))


def test_the_real_shipped_file_gives_a_bare_seed_105_plus_1_full_cards_on_a_throwaway_database(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base
    from test_base_library_data import seed_names

    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for i, name in enumerate(seed_names()):                                  # the bare seed a fresh install starts with
            s.add(Peptide(name=name, source=PeptideSource.CARD if i < 100 else PeptideSource.STARTER, card_number=i + 1 if i < 100 else None))
        s.commit()
        assert len(load_base_library(s)) == 106                                   # the 105 seed entries filled, the extra created
        rows = s.scalars(select(Peptide)).all()
        assert len(rows) == 106 and all(not is_empty_entry(p) and len(p.dosing_tiers) == 3 for p in rows)
        assert next(p for p in rows if p.name == "Ipamorelin").aliases and next(p for p in rows if p.name == "Kisspeptin-10").summary
        assert load_base_library(s) == []                                         # idempotent


def test_a_completely_blank_entry_the_person_added_is_filled_but_one_with_aliases_or_notes_is_not(db):
    db.add_all([Peptide(name="Basetest Blank", source=PeptideSource.CUSTOM),
                Peptide(name="Basetest Aliased", source=PeptideSource.CUSTOM, aliases="my nickname"),
                Peptide(name="Basetest Noted", source=PeptideSource.CUSTOM, notes="mine")])
    db.commit()
    blank_id = get(db, "Basetest Blank").id
    assert load_base_library(db, [rec("Basetest Blank"), rec("Basetest Aliased"), rec("Basetest Noted")]) == ["Basetest Blank"]
    assert get(db, "Basetest Blank").id == blank_id and get(db, "Basetest Blank").summary
    assert get(db, "Basetest Aliased").summary is None and get(db, "Basetest Noted").summary is None
