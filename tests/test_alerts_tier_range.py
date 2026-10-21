"""The protocol alerts' "library dose range" also comes from a card's Beginner / Intermediate / Advanced dosing when the card has no range of its own."""

import pytest

from app.library.conflicts import check
from app.models import DoseUnit, DosingTierLevel, Peptide, PeptideDosingTier
from app.protocols.alerts import item_data


@pytest.fixture
def peptide(db):
    p = Peptide(name="Zephyrtide Range")
    db.add(p)
    db.commit()
    yield p
    db.rollback()
    db.query(PeptideDosingTier).filter(PeptideDosingTier.peptide_id == p.id).delete()
    db.delete(p)
    db.commit()


def tier(db, p, level, dose):
    db.add(PeptideDosingTier(peptide_id=p.id, level=level, dose_text=dose, frequency_text="Daily"))
    db.commit()
    db.refresh(p)


def messages(p, dose, unit="mcg"):
    return [f.message for f in check([item_data(p, dose, unit, "am")], [])]


def test_a_card_with_tiers_but_no_range_of_its_own_gets_a_range_from_them(db, peptide):
    tier(db, peptide, DosingTierLevel.BEGINNER, "250mcg")
    tier(db, peptide, DosingTierLevel.INTERMEDIATE, "500mcg")
    tier(db, peptide, DosingTierLevel.ADVANCED, "1mg")
    data = item_data(peptide, 500, "mcg", "am")
    assert (data.lib_low, data.lib_mid, data.lib_high, data.lib_unit) == (250, 500, 1000, "mcg")           # mg and mcg tiers meet in mcg
    assert messages(peptide, 500) == []                                                                       # inside the range: nothing to say
    assert any("above the library's high dose of 1000 mcg" in m for m in messages(peptide, 2000))
    assert any("well below the library's low dose of 250 mcg" in m for m in messages(peptide, 50))


def test_the_cards_own_range_still_wins(db, peptide):
    peptide.dose_low, peptide.dose_mid, peptide.dose_high, peptide.dose_unit = 1, 2, 3, DoseUnit.MG
    db.commit()
    tier(db, peptide, DosingTierLevel.BEGINNER, "250mcg")
    tier(db, peptide, DosingTierLevel.ADVANCED, "10mg")
    data = item_data(peptide, 1, "mg", "am")
    assert (data.lib_low, data.lib_high, data.lib_unit) == (1, 3, "mg")


def test_one_tier_or_dosing_that_cannot_be_compared_is_still_no_range(db, peptide):
    tier(db, peptide, DosingTierLevel.BEGINNER, "250mcg")
    assert any("no dose range" in m for m in messages(peptide, 250))                       # one number is not a range
    tier(db, peptide, DosingTierLevel.ADVANCED, "0.25 mg/kg")
    assert any("no dose range" in m for m in messages(peptide, 250))                       # a weight-based tier has no plain amount
    tier(db, peptide, DosingTierLevel.INTERMEDIATE, "3 IU")
    assert any("no dose range" in m for m in messages(peptide, 250))                       # IU and mass cannot be put on one scale
