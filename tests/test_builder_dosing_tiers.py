"""The Protocol Builder's Beginner / Intermediate / Advanced checkboxes: what each library tier offers to fill in."""

import json
import re

import pytest
from sqlalchemy import select

from app.models import DosingTierLevel, Peptide, PeptideDosingTier
from app.protocols.tiers import builder_tiers, frequency_value


@pytest.fixture
def peptide(db):
    p = Peptide(name="Zephyrtide Tiers")
    db.add(p)
    db.commit()
    yield p
    db.rollback()
    db.query(PeptideDosingTier).filter(PeptideDosingTier.peptide_id == p.id).delete()
    db.delete(p)
    db.commit()


def add_tier(db, peptide, level, dose, freq, tod=None):
    db.add(PeptideDosingTier(peptide_id=peptide.id, level=level, dose_text=dose, frequency_text=freq, time_of_day=tod))
    db.commit()


@pytest.mark.parametrize("text,expected", [
    ("Daily", "daily"), ("once daily", "daily"), ("once daily, under the skin", "daily"), ("once daily by mouth", "daily"),
    ("EOD", "eod"), ("Weekly", "weekly"), ("once weekly, under the skin", "weekly"), ("As needed, max 1 dose/24h", "as_needed"),
    ("2x Daily", None), ("twice daily", None), ("3x/week", None), ("Single dose", None), ("Not established", None), ("", None), (None, None),
])
def test_only_unambiguous_frequencies_fill_the_frequency_box(text, expected):
    assert frequency_value(text) == expected


def test_a_plain_tier_offers_its_dose_unit_frequency_and_time(db, peptide):
    from app.models import TimeOfDay
    add_tier(db, peptide, DosingTierLevel.BEGINNER, "250mcg", "Daily", TimeOfDay.AM)
    tiers = builder_tiers(db)[str(peptide.id)]
    assert tiers["Beginner"] == {"amount": 250.0, "unit": "mcg", "text": "250mcg", "frequency": "daily", "frequency_text": "Daily", "time_of_day": "am"}
    assert set(tiers) == {"Beginner"}                       # no Intermediate or Advanced row: those boxes stay grey


def test_a_weight_based_dose_is_a_recommendation_but_fills_no_amount(db, peptide):
    add_tier(db, peptide, DosingTierLevel.INTERMEDIATE, "0.25 mg/kg", "2x Daily")
    tier = builder_tiers(db)[str(peptide.id)]["Intermediate"]
    assert tier["amount"] is None and tier["unit"] is None and tier["frequency"] is None
    assert tier["text"] == "0.25 mg/kg" and tier["frequency_text"] == "2x Daily"       # shown to the person, who enters the amount


def test_not_established_is_not_a_recommendation(db, peptide):
    add_tier(db, peptide, DosingTierLevel.ADVANCED, "Not established", "Not established")
    add_tier(db, peptide, DosingTierLevel.BEGINNER, "", "Daily")
    assert str(peptide.id) not in builder_tiers(db)


def test_the_builder_page_carries_the_tiers(client, db, peptide):
    add_tier(db, peptide, DosingTierLevel.BEGINNER, "1mg", "Weekly")
    page = client.get("/protocols/new").text
    data = json.loads(re.search(r'id="builder-data">(.*?)</script>', page, re.S).group(1))
    assert data["tiers"][str(peptide.id)]["Beginner"]["amount"] == 1.0
    assert data["tiers"][str(peptide.id)]["Beginner"]["frequency"] == "weekly"
