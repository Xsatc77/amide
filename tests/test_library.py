import html
from datetime import date

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.library.loader import load_cards
from app.models import DoseUnit, GoalPeptide, Peptide, PeptideSource, Protocol, ProtocolGoal, ProtocolItem

CARD = {
    "card_number": 2, "name": "BPC-157", "subtitle": "Synthetic pentadecapeptide derived from a gastric protein",
    "class": "Cytoprotective peptide", "category": "Tissue repair / gastrointestinal",
    "evidence_level": "Low / experimental", "status": "Not approved — investigational use",
    "applications": [{"title": "Tissue healing", "detail": "Experimental models"}],
    "mechanism_flow": ["BPC-157", "Repair signaling"],
    "mechanism": ["Cytoprotective action described in experimental models."],
    "clinical_use_note": "Established clinical use: not defined.",
    "evidence": {"level": "LOW EXPERIMENTAL", "points": ["Mostly preclinical data."]},
    "cautions": ["Long-term safety not established."],
    "quick_info": {"Half-life": "Not established in humans", "Routes studied": "Oral and injectable (research)"},
    "regulatory": {"FDA": "Not approved as a drug", "WADA": "Check the current list"},
    "quick_read": ["Peptide associated with tissue repair."],
    "references": ["Sikiric et al., 2018 — Curr Pharm Des (review)"],
    "image": "002.jpg", "page": 2,
}
OWNER_COLUMNS = ("aliases", "dose_low", "dose_mid", "dose_high", "dose_unit", "typical_frequency", "notes",
                 "normally_supplied_amount", "normally_supplied_unit", "library_specifications",
                 "card_class", "category", "evidence_level", "status", "card_details", "card_image")


@pytest.fixture(autouse=True)
def restore_library():
    """Library rows are shared seed data: put back peptides' fields and goal stacks after each test."""
    with SessionLocal() as s:
        peptides = {p.id: {c: getattr(p, c) for c in OWNER_COLUMNS} for p in s.scalars(select(Peptide))}
        stacks = [(g.goal, g.peptide_id, g.position) for g in s.scalars(select(GoalPeptide))]
    yield
    with SessionLocal() as s:
        for p in s.scalars(select(Peptide)):
            for column, value in peptides.get(p.id, {}).items():
                setattr(p, column, value)
        s.query(GoalPeptide).delete()
        s.add_all(GoalPeptide(goal=g, peptide_id=pid, position=pos) for g, pid, pos in stacks)
        s.commit()
    for f in config.CARDS_DIR.glob("*"):
        f.unlink()


def bpc(db) -> Peptide:
    return db.scalar(select(Peptide).where(Peptide.name == "BPC-157"))


def with_card(db) -> Peptide:
    load_cards(db, [CARD])
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CARDS_DIR / "002.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return bpc(db)


def text(r) -> str:
    assert r.status_code == 200, r.status_code
    return html.unescape(r.text)


# ---------------------------------------------------------------- list

def test_list_page(client, db):
    """KPV (used here previously as an example of a card-less peptide) is now permanently
    SHEET-sourced from this session's earlier import work -- use a fresh synthetic CUSTOM
    peptide instead. See ledger Task 5."""
    with_card(db)
    bare = Peptide(name="Test-List-Bare-Peptide", source=PeptideSource.CUSTOM)
    db.add(bare)
    db.commit()
    t = text(client.get("/library"))
    assert t.count('class="lib-tile') >= 105
    assert 'data-search="bpc-157' in t and "cytoprotective peptide" in t
    assert 'data-goals="muscle-recovery skin-beauty wellness"' in t or "muscle-recovery" in t
    for label in ("All", "Fat Loss / Metabolic", "Added by me"):
        assert label in t
    assert "No card" in t  # a peptide with neither card nor sheet data
    assert 'href="/library"' in t  # nav link


# ---------------------------------------------------------------- detail

def _card_test_peptide(db) -> Peptide:
    """A fresh synthetic CARD-sourced peptide, decoupled from the real BPC-157 row (which is now
    permanently SHEET-sourced from this session's earlier peptide-sheet-import work -- see ledger
    Task 4). Cleaned up automatically: CARD-sourced peptides aren't cleared by the autouse `clean`
    fixture, so tests using this must delete it themselves."""
    p = Peptide(
        name="Test-Card-Peptide", source=PeptideSource.CARD, card_number=None,
        card_class="Cytoprotective peptide", category="Tissue repair / gastrointestinal",
        evidence_level="Low / experimental", status="Not approved — investigational use",
        card_image="002.jpg",
        card_details={
            "subtitle": "Synthetic pentadecapeptide derived from a gastric protein",
            "applications": [{"title": "Tissue healing", "detail": "Experimental models"}],
            "mechanism_flow": ["Test-Card-Peptide", "Repair signaling"],
            "mechanism": ["Cytoprotective action described in experimental models."],
            "clinical_use_note": "Established clinical use: not defined.",
            "evidence": {"level": "LOW EXPERIMENTAL", "points": ["Mostly preclinical data."]},
            "cautions": ["Long-term safety not established."],
            "quick_info": {"Half-life": "Not established in humans"},
            "regulatory": {"FDA": "Not approved as a drug"},
            "quick_read": ["Peptide associated with tissue repair."],
            "references": ["Sikiric et al., 2018 — Curr Pharm Des (review)"],
        },
    )
    db.add(p)
    db.commit()
    return p


def test_detail_shows_card_sections(client, db):
    p = _card_test_peptide(db)
    try:
        t = text(client.get(f"/library/{p.id}"))
        for s in ("Test-Card-Peptide", "Synthetic pentadecapeptide derived from a gastric protein",
                  "Cytoprotective peptide", "Tissue repair / gastrointestinal", "Low / experimental",
                  "Tissue healing", "Repair signaling", "Long-term safety not established.",
                  "Not established in humans", "Not approved as a drug", "Sikiric et al., 2018",
                  f'href="/library/{p.id}/card"'):
            assert s in t, s
        assert "Card " not in t  # the "Card N" badge is removed project-wide
    finally:
        db.delete(p)
        db.commit()


def test_detail_card_sourced_peptide_unchanged(client, db):
    """Existing card-sourced rendering must not regress."""
    p = _card_test_peptide(db)
    try:
        resp = client.get(f"/library/{p.id}")
        assert resp.status_code == 200
        assert "No card imported" not in resp.text
        assert "Card " not in resp.text  # the "Card N" badge is removed project-wide
    finally:
        db.delete(p)
        db.commit()


def test_detail_without_card(client, db):
    """No-card-no-sheet empty state, on a fresh synthetic peptide: KPV (used here previously) is
    now permanently SHEET-sourced from this session's earlier import work. See ledger Task 4."""
    p = Peptide(name="Test-No-Card-Peptide", source=PeptideSource.CUSTOM)
    db.add(p)
    db.commit()
    t = text(client.get(f"/library/{p.id}"))
    assert "No card imported" in t and "/card" not in t.split("No card imported")[0][-200:]


def _sheet_test_peptide(db) -> Peptide:
    """A fresh SHEET-sourced peptide with one of everything this task
    renders. Cleaned up automatically by the autouse `clean` fixture
    (conftest.py), which deletes SHEET-sourced peptides -- and their child
    rows via the DB-level ON DELETE CASCADE -- after the test."""
    from app.models import (
        DosingTierLevel, PeptideCycle, PeptideDosingTier, PeptideMonitoringTest,
        PeptideStackRelation, StackRelation, TimeOfDay,
    )
    p = Peptide(
        name="Test-Sheet-Peptide", source=PeptideSource.SHEET,
        summary="A made-up plain-language summary for testing.",
        tags=["Tissue Repair", "Grade A"], half_life_text="~3-5 hours",
        route_summary="Injection",
        sheet_sections_simple={
            "what_is": "A made-up plain-language description for testing.",
            "benefits": "A made-up plain-language benefit for testing.",
        },
        sheet_sections={"what_is": "THE ORIGINAL SCIENTIFIC TEXT MUST NEVER RENDER"},
    )
    db.add(p)
    db.flush()
    p.dosing_tiers = [PeptideDosingTier(level=DosingTierLevel.BEGINNER, dose_text="10mg",
                                         frequency_text="Daily", time_of_day=TimeOfDay.AM)]
    p.cycle = PeptideCycle(on_weeks=6, off_weeks=4, note="A made-up cycle note for testing.")
    p.stack_relations = [PeptideStackRelation(partner_name="Made-Up-Partner", relation=StackRelation.WORKS_WITH,
                                               note="A made-up stacking note for testing.")]
    p.monitoring_tests = [PeptideMonitoringTest(test_name="Made-up test", when_text="Baseline",
                                                 why_text="A made-up reason for testing.")]
    db.commit()
    return p


def test_detail_renders_sheet_sourced_peptide(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get(f"/library/{p.id}")
    assert resp.status_code == 200
    body = resp.text
    assert "A made-up plain-language summary for testing." in body
    assert "A made-up plain-language description for testing." in body
    assert "10mg" in body and "Daily" in body
    assert "A made-up cycle note for testing." in body
    assert "Made-Up-Partner" in body
    assert "Made-up test" in body
    # The original scientific text must never appear -- only the simplified version.
    assert "THE ORIGINAL SCIENTIFIC TEXT MUST NEVER RENDER" not in body


def test_detail_renders_goal_stack_chips_with_their_goal_color(client, db):
    """Coverage gap the final review found: no test exercised the detail page's goal-stack chips
    (a peptide's membership in a protocol Goal), which render for both sheet-sourced and
    card-sourced peptides alike. A regression here (e.g. a typo in the goal_label filter lookup)
    would previously have gone undetected -- see ledger's corrected Task 4/5 note."""
    p = _sheet_test_peptide(db)
    db.add(GoalPeptide(goal="muscle-recovery", peptide_id=p.id, position=0))
    db.commit()
    body = text(client.get(f"/library/{p.id}"))
    assert "Muscle & Recovery" in body
    assert "var(--goal-muscle-recovery)" in body


def test_detail_sheet_sourced_tags_get_goal_colors(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get(f"/library/{p.id}")
    body = resp.text
    assert "var(--goal-muscle-recovery)" in body  # "Tissue Repair" tag
    assert "tag-plain" in body  # "Grade A" tag renders neutral


def test_detail_sheet_sourced_peptide_missing_some_sections_renders_only_present_ones(client, db):
    """Review Focus: a sheet-sourced peptide missing most of the 9 narrative keys must not
    render empty headings for the missing ones."""
    p = _sheet_test_peptide(db)
    p.sheet_sections_simple = {"what_is": "Only this one section exists for testing."}
    db.commit()
    resp = client.get(f"/library/{p.id}")
    body = resp.text
    assert "Only this one section exists for testing." in body
    assert "Side Effects" not in body
    assert "Contraindications" not in body


def test_detail_product_quality_renders_as_separate_lines_not_one_run_on_paragraph(client, db):
    """Real product_quality text is a risk sentence followed by several checklist items joined by
    newlines (confirmed across all 150 real sheet-sourced peptides) -- it must render as distinct
    lines/items, not collapse into one run-on paragraph."""
    p = _sheet_test_peptide(db)
    p.sheet_sections_simple = {
        "product_quality": "high risk\nFirst checklist item for testing.\nSecond checklist item for testing.",
    }
    db.commit()
    resp = client.get(f"/library/{p.id}")
    body = resp.text
    assert "<li>First checklist item for testing.</li>" in body
    assert "<li>Second checklist item for testing.</li>" in body


def test_detail_no_card_no_sheet_peptide_shows_empty_state(client, db):
    p = Peptide(name="Test-Bare-Peptide", source=PeptideSource.CUSTOM)
    db.add(p)
    db.commit()
    resp = client.get(f"/library/{p.id}")
    assert "No card imported for this peptide." in resp.text


def test_detail_lists_protocols_using_peptide(client, db, me):
    p = bpc(db)
    proto = Protocol(name="Heal fast", start_date=date(2026, 9, 1), owner_id=me)
    proto.goals = [ProtocolGoal(goal="muscle-recovery")]
    proto.items = [ProtocolItem(peptide_id=p.id, position=0)]
    db.add(proto)
    db.commit()
    t = text(client.get(f"/library/{p.id}"))
    assert "Heal fast" in t and f'href="/protocols/{proto.id}/edit"' in t


def test_detail_404(client):
    assert client.get("/library/99999").status_code == 404


def test_list_sorts_alphabetically_case_insensitively(client, db):
    p1 = Peptide(name="zzz-test-first", source=PeptideSource.CUSTOM)
    p2 = Peptide(name="AAA-test-second", source=PeptideSource.CUSTOM)
    db.add_all([p1, p2])
    db.commit()
    resp = client.get("/library")
    body = resp.text
    assert body.index("AAA-test-second") < body.index("zzz-test-first")


def test_list_no_card_number_badge_anywhere(client, db):
    resp = client.get("/library")
    assert "Card #" not in resp.text and "lib-num" not in resp.text


def test_list_sheet_sourced_tile_shows_tags_not_no_card(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get("/library")
    body = resp.text
    tile_start = body.index(f'href="/library/{p.id}"')
    tile_end = body.index("</a>", tile_start)
    tile = body[tile_start:tile_end]
    assert "Tissue Repair" in tile
    assert "No card" not in tile


def test_list_card_sourced_tile_unchanged(client, db):
    """Exercised on a fresh synthetic CARD-sourced peptide, not the real BPC-157 row (now
    permanently SHEET-sourced -- see ledger Task 5)."""
    p = _card_test_peptide(db)
    try:
        resp = client.get("/library")
        body = resp.text
        tile_start = body.index(f'href="/library/{p.id}"')
        tile_end = body.index("</a>", tile_start)
        tile = body[tile_start:tile_end]
        assert p.card_class in tile
    finally:
        db.delete(p)
        db.commit()


# ---------------------------------------------------------------- card image

def test_card_image_route(client, db):
    p = with_card(db)
    r = client.get(f"/library/{p.id}/card")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    kpv = db.scalar(select(Peptide).where(Peptide.name == "KPV"))
    assert client.get(f"/library/{kpv.id}/card").status_code == 404

    p.card_image = "../amide.db"
    db.commit()
    assert client.get(f"/library/{p.id}/card").status_code == 404

    p.card_image = "003.jpg"  # well-formed but missing on disk
    db.commit()
    assert client.get(f"/library/{p.id}/card").status_code == 404


# ---------------------------------------------------------------- edit

def edit_form(**overrides):
    fields = {"aliases": "Body Protection Compound", "dose_low": "250", "dose_mid": "500", "dose_high": "750",
              "dose_unit": "mcg", "typical_frequency": "Daily", "notes": "My notes",
              "goal": ["muscle-recovery", "skin-beauty", "wellness"]}
    return {**fields, **overrides}


def test_edit_page_prefills(client, db):
    p = bpc(db)
    p.dose_low, p.notes = 100, "prefill me"
    db.commit()
    t = text(client.get(f"/library/{p.id}/edit"))
    assert 'name="dose_low"' in t and 'value="100"' in t and "prefill me" in t
    assert 'value="muscle-recovery" checked' in t


def test_edit_saves_owner_fields(client, db):
    p = with_card(db)
    r = client.post(f"/library/{p.id}", data=edit_form(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/library/{p.id}"
    db.expire_all()
    p = bpc(db)
    assert (p.aliases, p.dose_low, p.dose_mid, p.dose_high, p.dose_unit.value, p.typical_frequency, p.notes) == (
        "Body Protection Compound", 250, 500, 750, "mcg", "Daily", "My notes")
    assert p.card_class == "Cytoprotective peptide"  # card fields untouched
    t = text(client.get(f"/library/{p.id}"))
    assert "250 – 500 – 750 mcg" in t


def test_edit_validation(client, db):
    p = bpc(db)
    for bad in ({"dose_low": "0"}, {"dose_low": "abc"}, {"dose_low": "800", "dose_high": "750"},
                {"goal": ["nope"]}, {"dose_unit": "grams"}):
        r = client.post(f"/library/{p.id}", data=edit_form(**bad))
        assert r.status_code == 422, bad
        assert "Please fix" in html.unescape(r.text)
    db.expire_all()
    assert bpc(db).dose_low is None  # nothing saved


def test_edit_saves_normally_supplied_vial_size(client, db):
    p = bpc(db)
    client.post(f"/library/{p.id}", data=edit_form(normally_supplied_amount="5", normally_supplied_unit="mg"))
    db.expire_all()
    p = bpc(db)
    assert (p.normally_supplied_amount, p.normally_supplied_unit.value) == (5, "mg")
    assert "5 mg" in text(client.get(f"/library/{p.id}"))


def test_edit_page_prefills_normally_supplied_vial_size(client, db):
    p = bpc(db)
    p.normally_supplied_amount, p.normally_supplied_unit = 10, DoseUnit.MG
    db.commit()
    t = text(client.get(f"/library/{p.id}/edit"))
    assert 'name="normally_supplied_amount"' in t and 'value="10"' in t
    assert 'value="mg" selected' in t


def test_normally_supplied_unit_defaults_to_mg_and_blank_amount_clears_both(client, db):
    p = bpc(db)
    client.post(f"/library/{p.id}", data=edit_form(normally_supplied_amount="5", normally_supplied_unit=""))
    db.expire_all()
    assert bpc(db).normally_supplied_unit == DoseUnit.MG
    client.post(f"/library/{p.id}", data=edit_form(normally_supplied_amount="", normally_supplied_unit="mcg"))
    db.expire_all()
    p = bpc(db)
    assert p.normally_supplied_amount is None and p.normally_supplied_unit is None


def test_normally_supplied_validation(client, db):
    p = bpc(db)
    for bad in ({"normally_supplied_amount": "0"}, {"normally_supplied_amount": "-5"},
                {"normally_supplied_amount": "abc"},
                {"normally_supplied_amount": "5", "normally_supplied_unit": "grams"}):
        r = client.post(f"/library/{p.id}", data=edit_form(**bad))
        assert r.status_code == 422, bad
    db.expire_all()
    assert bpc(db).normally_supplied_amount is None


def test_edit_goal_membership(client, db):
    p = bpc(db)
    before_fat = [g.peptide_id for g in db.scalars(
        select(GoalPeptide).where(GoalPeptide.goal == "fat-loss").order_by(GoalPeptide.position))]
    client.post(f"/library/{p.id}", data=edit_form(goal=["skin-beauty", "fat-loss"]))
    db.expire_all()
    goals = {g.goal for g in db.scalars(select(GoalPeptide).where(GoalPeptide.peptide_id == p.id))}
    assert goals == {"skin-beauty", "fat-loss"}
    fat = [g.peptide_id for g in db.scalars(
        select(GoalPeptide).where(GoalPeptide.goal == "fat-loss").order_by(GoalPeptide.position))]
    assert fat == before_fat + [p.id]  # appended at the end, others unchanged


def test_edit_blank_doses_clear_values(client, db):
    p = bpc(db)
    p.dose_low = 5
    db.commit()
    client.post(f"/library/{p.id}", data=edit_form(dose_low="", dose_mid="", dose_high="", dose_unit=""))
    db.expire_all()
    p = bpc(db)
    assert p.dose_low is None and p.dose_unit is None


# ---------------------------------------------------------------- API + builder

def test_api_peptides_has_card_fields(client, db):
    with_card(db)
    row = next(r for r in client.get("/api/peptides").json() if r["name"] == "BPC-157")
    assert row["card_class"] == "Cytoprotective peptide" and row["has_card"] is True
    assert row["evidence_level"] == "Low / experimental"


def test_builder_script_links_to_library(client):
    js = client.get("/static/js/protocol-builder.js").text
    assert "/library/" in js and "View card" in js
