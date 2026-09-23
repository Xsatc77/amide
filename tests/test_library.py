import html
from datetime import date

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.library.loader import load_cards
from app.models import GoalPeptide, Peptide, Protocol, ProtocolGoal, ProtocolItem

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
    with_card(db)
    t = text(client.get("/library"))
    assert t.count('class="lib-tile') >= 105
    assert 'data-search="bpc-157' in t and "cytoprotective peptide" in t
    assert 'data-goals="muscle-recovery skin-beauty wellness"' in t or "muscle-recovery" in t
    for label in ("All", "Fat Loss / Metabolic", "Added by me"):
        assert label in t
    assert "No card" in t  # starter peptides (e.g. KPV) have no card
    assert 'href="/library"' in t  # nav link


# ---------------------------------------------------------------- detail

def test_detail_shows_card_sections(client, db):
    p = with_card(db)
    t = text(client.get(f"/library/{p.id}"))
    for s in ("BPC-157", "Card 2", "Synthetic pentadecapeptide derived from a gastric protein",
              "Cytoprotective peptide", "Tissue repair / gastrointestinal", "Low / experimental",
              "Tissue healing", "Repair signaling", "Long-term safety not established.",
              "Not established in humans", "Not approved as a drug", "Sikiric et al., 2018",
              f'href="/library/{p.id}/card"', "Muscle & Recovery"):
        assert s in t, s


def test_detail_without_card(client, db):
    kpv = db.scalar(select(Peptide).where(Peptide.name == "KPV"))
    t = text(client.get(f"/library/{kpv.id}"))
    assert "No card imported" in t and "/card" not in t.split("No card imported")[0][-200:]


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
