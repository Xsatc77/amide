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


def test_load_sheets_creates_a_new_peptide(db):
    from app.library.loader import load_sheets
    sheet = {
        "name": "Test-Compound-9", "aliases": ["TC9"], "tags": ["Recovery"],
        "half_life_text": "~3-5 hours", "route_summary": "Injection", "cycle_shorthand": "6w on / 4w off",
        "summary": "A made-up summary.", "dosing_tiers": [
            {"level": "Beginner", "dose_text": "10mg", "frequency_text": "Daily", "time_of_day": None},
        ],
        "cycle": {"on_weeks": 6, "off_weeks": 4, "note": "A made-up note."},
        "stack_relations": [{"partner_name": "Made-Up-Partner-A", "relation": "works_with", "note": "n"}],
        "monitoring_tests": [{"test_name": "Made-up test", "when_text": "Baseline", "why_text": "y", "target_text": None}],
        "bioavailability_text": None, "tmax_text": "~1 hour",
        "storage_before_text": "a", "storage_after_text": "b", "storage_temperature_text": "c",
        "legal_status_text": "d", "cost_estimate_text": "e", "sheet_sections": {"what_is": "f"},
        "sheet_sections_simple": {"what_is": "A made-up plain-language rewrite."},
        "usage_tips": ["Take each morning on an empty stomach."],
    }
    report = load_sheets(db, [sheet])
    assert report.created == ["Test-Compound-9"]
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.source == PeptideSource.SHEET
    assert p.usage_tips == ["Take each morning on an empty stomach."]
    assert len(p.dosing_tiers) == 1
    assert p.tags == ["Recovery"]
    assert p.summary == "A made-up summary."
    assert p.sheet_sections == {"what_is": "f"}
    assert p.sheet_sections_simple == {"what_is": "A made-up plain-language rewrite."}


def test_load_sheets_defaults_sheet_sections_simple_to_empty_dict_when_absent(db):
    """sheet_sections_simple is hand-curated per file, like usage_tips -- a sheet dict that
    doesn't supply it (e.g. during the parser-only build phase, before curation exists) must
    default cleanly rather than raise or store None."""
    from app.library.loader import load_sheets
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None, "stack_relations": [], "monitoring_tests": [], "bioavailability_text": None,
            "tmax_text": None, "storage_before_text": None, "storage_after_text": None,
            "storage_temperature_text": None, "legal_status_text": None, "cost_estimate_text": None,
            "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.sheet_sections_simple == {}


def test_load_sheets_replaces_an_existing_card_sourced_peptide(db):
    """Review Focus item 2: a name match against a CARD-sourced peptide must fully clear its old
    card fields, not leave them alongside the new sheet fields."""
    from app.library.loader import load_sheets
    existing = Peptide(name="Test-Compound-9", source=PeptideSource.CARD, card_class="Old class",
                       category="Old category", card_details={"old": "data"})
    db.add(existing)
    db.commit()
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None, "stack_relations": [], "monitoring_tests": [], "bioavailability_text": None,
            "tmax_text": None, "storage_before_text": None, "storage_after_text": None,
            "storage_temperature_text": None, "legal_status_text": None, "cost_estimate_text": None,
            "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])
    db.refresh(existing)
    assert existing.source == PeptideSource.SHEET
    assert existing.card_class is None and existing.category is None and existing.card_details is None


def _blank_sheet(name):
    return {"name": name, "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None, "stack_relations": [], "monitoring_tests": [], "bioavailability_text": None,
            "tmax_text": None, "storage_before_text": None, "storage_after_text": None,
            "storage_temperature_text": None, "legal_status_text": None, "cost_estimate_text": None,
            "sheet_sections": {}, "usage_tips": []}


def test_load_sheets_does_not_overwrite_an_existing_starter_sourced_peptide(db):
    """A name collision with a STARTER-sourced peptide must not modify it -- only CARD-sourced
    peptides get the full-clear-and-convert-to-SHEET treatment."""
    from app.library.loader import load_sheets
    existing = Peptide(name="Test-Compound-9", source=PeptideSource.STARTER, notes="my own notes",
                        dose_low=100)
    db.add(existing)
    db.commit()
    try:
        report = load_sheets(db, [_blank_sheet("Test-Compound-9")])
        db.refresh(existing)
        assert existing.source == PeptideSource.STARTER
        assert existing.notes == "my own notes" and existing.dose_low == 100
        assert existing.half_life_text is None  # sheet fields never applied
        assert report.created == [] and report.updated == []
        assert "Test-Compound-9" in report.skipped_existing
    finally:
        # STARTER-sourced rows aren't cleared by the autouse `clean` fixture (only CUSTOM/SHEET
        # are), so remove it ourselves to avoid leaking into later tests.
        db.delete(existing)
        db.commit()


def test_load_sheets_does_not_overwrite_an_existing_custom_sourced_peptide(db):
    from app.library.loader import load_sheets
    existing = Peptide(name="Test-Compound-9", source=PeptideSource.CUSTOM, notes="my own notes")
    db.add(existing)
    db.commit()
    report = load_sheets(db, [_blank_sheet("Test-Compound-9")])
    db.refresh(existing)
    assert existing.source == PeptideSource.CUSTOM
    assert existing.notes == "my own notes"
    assert report.created == [] and report.updated == []
    assert "Test-Compound-9" in report.skipped_existing


def test_load_sheets_force_names_overwrites_a_named_starter_sourced_peptide(db):
    """force_names is an explicit, per-call opt-in for the user to say 'yes, overwrite this
    specific one' -- everything else with a STARTER/CUSTOM name collision is still skipped."""
    from app.library.loader import load_sheets
    existing = Peptide(name="Test-Compound-9", source=PeptideSource.STARTER, notes="my own notes",
                        dose_low=100)
    other = Peptide(name="Other-Starter", source=PeptideSource.STARTER, notes="leave me alone")
    db.add_all([existing, other])
    db.commit()
    try:
        sheet = _blank_sheet("Test-Compound-9")
        sheet["half_life_text"] = "~3-5 hours"
        report = load_sheets(
            db, [sheet, _blank_sheet("Other-Starter")], force_names={"Test-Compound-9"}
        )
        db.refresh(existing)
        db.refresh(other)
        assert existing.source == PeptideSource.SHEET
        assert existing.half_life_text == "~3-5 hours"
        assert existing.notes == "my own notes"  # notes is never touched, even when forced
        assert existing.dose_low == 100  # not a sheet column, untouched
        assert "Test-Compound-9" in report.updated
        assert other.source == PeptideSource.STARTER  # not in force_names -- still skipped
        assert "Other-Starter" in report.skipped_existing
    finally:
        db.delete(existing)
        db.delete(other)
        db.commit()


def test_load_sheets_does_not_crash_on_a_dosing_tier_or_monitoring_test_missing_a_cell(db):
    """Spec: a missing field/table row leaves that field/table empty, never a crash -- a real row
    can genuinely be missing a cell (parse_sheet already produces None positionally for these)."""
    from app.library.loader import load_sheets
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None,
            "dosing_tiers": [{"level": "Beginner", "dose_text": None, "frequency_text": None, "time_of_day": None}],
            "cycle": None, "stack_relations": [],
            "monitoring_tests": [{"test_name": "Made-up test", "when_text": None, "why_text": None, "target_text": None}],
            "bioavailability_text": None, "tmax_text": None,
            "storage_before_text": None, "storage_after_text": None, "storage_temperature_text": None,
            "legal_status_text": None, "cost_estimate_text": None, "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])  # must not raise IntegrityError
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.dosing_tiers[0].dose_text is None and p.dosing_tiers[0].frequency_text is None
    assert p.monitoring_tests[0].when_text is None and p.monitoring_tests[0].why_text is None


def test_load_sheets_stack_relation_partner_not_matching_any_peptide_is_fine(db):
    from app.library.loader import load_sheets
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None,
            "stack_relations": [{"partner_name": "Nonexistent Drug Class", "relation": "avoid", "note": "n"}],
            "monitoring_tests": [], "bioavailability_text": None, "tmax_text": None,
            "storage_before_text": None, "storage_after_text": None, "storage_temperature_text": None,
            "legal_status_text": None, "cost_estimate_text": None, "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])  # must not raise
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.stack_relations[0].partner_name == "Nonexistent Drug Class"
