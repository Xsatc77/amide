"""Each dosing tier on a library card can open the calculator with that dose filled in."""

import html

from app.db import SessionLocal
from app.models import DosingTierLevel, Peptide, PeptideDosingTier, PeptideSource


def test_a_tier_with_a_plain_dose_links_to_the_calculator_and_a_weight_based_one_does_not(client, db):
    with SessionLocal() as s:
        card = Peptide(name="Calc Link Test", source=PeptideSource.SHEET)
        s.add(card)
        s.flush()
        s.add_all([PeptideDosingTier(peptide_id=card.id, level=DosingTierLevel.BEGINNER, dose_text="250mcg", frequency_text="daily"),
                   PeptideDosingTier(peptide_id=card.id, level=DosingTierLevel.INTERMEDIATE, dose_text="1.5 mg", frequency_text="daily"),
                   PeptideDosingTier(peptide_id=card.id, level=DosingTierLevel.ADVANCED, dose_text="0.1 mg/kg", frequency_text="daily")])
        s.commit()
        card_id = card.id
    try:
        page = html.unescape(client.get(f"/library/{card_id}").text)
        assert "/calculator?dose_value=250&dose_unit=mcg" in page and "/calculator?dose_value=1.5&dose_unit=mg" in page
        assert page.count("Open in calculator") == 2
    finally:
        with SessionLocal() as s:
            s.query(Peptide).filter_by(id=card_id).delete()
            s.commit()
