import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import make_engine
from app.library.loader import load_cards
from app.migrate import upgrade_db
from app.models import Peptide, PeptideSource


@pytest.fixture
def s(tmp_path):
    url = f"sqlite:///{(tmp_path / 'lib.db').as_posix()}"
    upgrade_db(url)
    engine = make_engine(url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def card(number, name, **extra):
    base = {
        "card_number": number, "name": name, "subtitle": f"{name} subtitle",
        "class": "Cytoprotective peptide", "category": "Tissue repair", "evidence_level": "Low / experimental",
        "status": "Not approved", "applications": [{"title": "Tissue healing", "detail": "Experimental models"}],
        "mechanism": ["Does things."], "quick_info": {"Half-life": "Short"}, "regulatory": {"FDA": "Not approved"},
        "references": ["Someone, 2020"], "image": f"{number:03d}.webp", "page": number,
    }
    return {**base, **extra}


def pep(s, name):
    return s.scalar(select(Peptide).where(Peptide.name == name))


def test_load_updates_existing_by_number(s):
    report = load_cards(s, [card(2, "BPC-157")])
    p = pep(s, "BPC-157")
    assert report.updated == ["BPC-157"] and report.created == [] and report.mismatched == []
    assert p.card_class == "Cytoprotective peptide" and p.category == "Tissue repair"
    assert p.evidence_level == "Low / experimental" and p.status == "Not approved"
    assert p.card_image == "002.webp"
    assert p.card_details["subtitle"] == "BPC-157 subtitle"
    assert p.card_details["quick_info"] == {"Half-life": "Short"}
    assert "class" not in p.card_details and "card_number" not in p.card_details


def test_load_matches_name_case_insensitively(s):
    report = load_cards(s, [card(2, "bpc-157")])
    assert report.updated == ["BPC-157"]


def test_load_creates_unknown_number(s):
    report = load_cards(s, [card(101, "New Peptide")])
    p = pep(s, "New Peptide")
    assert report.created == ["New Peptide"]
    assert p.card_number == 101 and p.source is PeptideSource.CARD


def test_load_mismatch_skipped(s):
    report = load_cards(s, [card(2, "Something Else")])
    assert report.mismatched == ["#2: card says 'Something Else', library has 'BPC-157'"]
    assert pep(s, "BPC-157").card_class is None


def test_load_keeps_owner_fields(s):
    p = pep(s, "BPC-157")
    p.aliases, p.dose_low, p.dose_high, p.notes, p.typical_frequency = "Body Protection Compound", 250, 500, "mine", "Daily"
    s.commit()
    load_cards(s, [card(2, "BPC-157")])
    load_cards(s, [card(2, "BPC-157", **{"class": "Changed"})])
    s.expire_all()
    p = pep(s, "BPC-157")
    assert (p.aliases, p.dose_low, p.dose_high, p.notes, p.typical_frequency) == (
        "Body Protection Compound", 250, 500, "mine", "Daily")
    assert p.card_class == "Changed"


def test_load_is_idempotent(s):
    load_cards(s, [card(2, "BPC-157")])
    report = load_cards(s, [card(2, "BPC-157")])
    assert report.updated == ["BPC-157"] and report.created == []
    assert s.scalar(select(Peptide).where(Peptide.card_number == 2)).name == "BPC-157"


def test_load_adopts_same_named_peptide_without_number(s):
    p = pep(s, "BPC-157")
    p.card_number = None
    s.commit()
    report = load_cards(s, [card(2, "BPC-157")])
    s.expire_all()
    p = pep(s, "BPC-157")
    assert report.updated == ["BPC-157"] and report.created == []
    assert p.card_number == 2 and p.card_class == "Cytoprotective peptide"
