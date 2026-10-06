import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.library import populate_library_specs
from app.models import Peptide, PeptideSource


@pytest.fixture
def price_list(monkeypatch):
    def feed(specs_by_name):
        monkeypatch.setattr(populate_library_specs, "parse_price_list_pdf", lambda path: specs_by_name)
    return feed


def add_card(db, name, specs=None):
    db.add(Peptide(name=name, source=PeptideSource.CUSTOM, library_specifications=specs))
    db.commit()


def specs_of(db, name):
    db.expire_all()
    return db.scalar(select(Peptide.library_specifications).where(Peptide.name == name))


def run(db):
    return populate_library_specs.populate_from_price_list(db, "ignored.pdf")


def test_never_creates_cards_and_reports_what_nothing_matched(db, price_list):
    add_card(db, "Zephyrix-9")
    price_list({"Zephyrix-9": "5mg", "Quorbitol 3": "1mg"})
    before = db.scalar(select(func.count()).select_from(Peptide))
    result = run(db)
    assert db.scalar(select(func.count()).select_from(Peptide)) == before
    assert result.updated == 1
    assert [name for name, _ in result.unmatched] == ["Quorbitol 3"]
    assert specs_of(db, "Zephyrix-9") == "5mg"


def test_vendor_spelling_variants_all_land_on_the_one_card(db, price_list):
    add_card(db, "Zephyrix-9")
    price_list({"Zephyrix 9": "5mg, 10mg", "ZEPHYRIX9 Acetate": "20mg", "zephyrix-9": "2mg"})
    result = run(db)
    assert specs_of(db, "Zephyrix-9") == "2mg, 5mg, 10mg, 20mg"
    assert result.updated == 1


def test_specs_already_on_the_card_are_kept(db, price_list):
    add_card(db, "Zephyrix-9", specs="50mg")
    price_list({"Zephyrix 9": "5mg"})
    run(db)
    assert specs_of(db, "Zephyrix-9") == "5mg, 50mg"


def test_with_and_without_variants_stay_separate(db, price_list):
    add_card(db, "Zorvex with B12")
    add_card(db, "Zorvex without B12")
    price_list({"Zorvex without B12": "5mg"})
    run(db)
    assert specs_of(db, "Zorvex without B12") == "5mg"
    assert specs_of(db, "Zorvex with B12") is None


def test_unmatched_names_come_with_close_card_suggestions(db, price_list):
    add_card(db, "Quillamine-7")
    price_list({"Quillamine 7 forte": "5mg"})
    result = run(db)
    assert result.updated == 0
    assert result.unmatched == [("Quillamine 7 forte", ["Quillamine-7"])]


def test_result_says_how_each_name_matched(db, price_list):
    add_card(db, "Zephyrix-9")
    price_list({"Zephyrix 9": "5mg"})
    assert run(db).matches == [("Zephyrix 9", "Zephyrix-9", "normalized")]
