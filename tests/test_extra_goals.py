"""Vitamins & Supplements and Prescriptions: non-peptide protocol cards, filled from library entries the user adds."""

import re
from pathlib import Path

from app.goals import GOALS, GOALS_BY_SLUG

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_the_two_new_goals_exist_with_a_colour_each():
    for slug, label in (("supplements", "Vitamins & Supplements"), ("prescriptions", "Prescriptions")):
        goal = GOALS_BY_SLUG[slug]
        assert goal.label == label and goal.color == slug
        assert f"--goal-{slug}:" in CSS
    assert len({g.slug for g in GOALS}) == len(GOALS) == 10


def test_the_protocols_page_offers_both_cards(client, db):
    page = client.get("/protocols").text.replace("&amp;", "&")
    assert "Vitamins & Supplements" in page and "Prescriptions" in page


def test_the_library_edit_form_can_file_an_entry_under_them(client, db):
    from app.db import SessionLocal
    from app.models import Peptide, PeptideSource
    with SessionLocal() as s:
        card = Peptide(name="Vitamin D3 test", source=PeptideSource.CUSTOM)
        s.add(card)
        s.commit()
        card_id = card.id
    try:
        page = client.get(f"/library/{card_id}/edit").text.replace("&amp;", "&")
        assert re.search(r'name="goal"[^>]*value="supplements"', page) and re.search(r'name="goal"[^>]*value="prescriptions"', page)
    finally:
        with SessionLocal() as s:
            s.query(Peptide).filter_by(id=card_id).delete()
            s.commit()
