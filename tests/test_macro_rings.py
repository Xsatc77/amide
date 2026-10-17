"""Donut rings of what was eaten: one per day (Food tab and Journal) and one for the chosen range on the Measurements tab."""

from datetime import date, timedelta

import pytest

from app import clock
from app.food import day_overview, rings
from app.models import FoodLog, JournalEntry, User
from tests.test_unit_doses import clean as _clean_unit_doses  # noqa: F401  (removes the pill, its protocol and its dose logs)

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(FoodLog).delete()
    db.query(JournalEntry).delete()
    db.commit()


def eat(db, me, days_ago, protein=0.0, carb=0.0, fat=0.0, meal="lunch"):
    db.add(FoodLog(owner_id=me, eaten_on=TODAY - timedelta(days=days_ago), meal=meal, name="Test food", serving="1", servings=1,
                   calories=protein * 4 + carb * 4 + fat * 9, protein_g=protein, carb_g=carb, fat_g=fat, fiber_g=0))
    db.commit()


def test_the_ring_is_sized_by_the_grams_logged_with_nothing_converted():
    r = rings.macro_ring(50, 100, 20, 10)
    assert [(i["name"], i["grams"], i["pct"]) for i in r["legend"]] == [("Protein", 50, 28), ("Carbs", 100, 56), ("Fat", 20, 11), ("Fiber", 10, 6)]
    assert r["total_g"] == 180 and [s["name"] for s in r["ring"]["slices"]] == ["Protein", "Carbs", "Fat", "Fiber"]


def test_no_macros_means_no_ring():
    assert rings.macro_ring(0, 0, 0) is None


def test_a_macro_with_nothing_is_left_out_of_the_ring_but_stays_in_the_legend():
    r = rings.macro_ring(40, 0, 10)
    assert len(r["ring"]["slices"]) == 2 and [i["name"] for i in r["legend"]] == ["Protein", "Carbs", "Fat", "Fiber"]


def test_a_journal_day_row_carries_that_days_ring(db, me):
    eat(db, me, 2, protein=30, carb=60, fat=10)
    day = TODAY - timedelta(days=2)
    row = day_overview.day_overviews(db, db.get(User, me), [day])[day]
    assert row["macro_ring"]["total_g"] == 100


def test_the_journal_page_draws_the_ring_for_a_day_with_food(client, db, me):
    eat(db, me, 2, protein=30, carb=60, fat=10)
    db.add(JournalEntry(owner_id=me, entry_date=TODAY - timedelta(days=2)))
    db.commit()
    page = client.get("/measurements?tab=journal").text
    assert 'class="macro-ring' in page and "Protein" in page


def test_the_food_tab_draws_what_was_eaten_that_day(client, db, me):
    eat(db, me, 0, protein=30, carb=60, fat=10)
    page = client.get("/measurements?tab=food").text
    assert 'data-eaten-ring' in page


def test_the_measurements_tab_ring_follows_the_chosen_range(client, db, me):
    eat(db, me, 3, protein=100, carb=0, fat=0)           # inside 7 days
    eat(db, me, 20, protein=0, carb=0, fat=100)          # only inside 1 month and longer
    week = client.get("/measurements?tab=measurements&range=7d").text
    month = client.get("/measurements?tab=measurements&range=1mo").text
    assert "data-range-ring" in week and "data-range-ring" in month
    assert "Fat: <strong>100 g" not in week and "Fat: <strong>100 g" in month
    assert "Protein: <strong>100 g" in week


# ---- fiber ------------------------------------------------------------------------------------------------------------------------------

def test_the_journal_day_ring_includes_fiber_from_food_and_a_pill(db, me):
    from app.models import DoseLog, DoseStatus, DoseUnit, Peptide, PeptideSource, Protocol, TimeOfDay
    db.add(FoodLog(owner_id=me, eaten_on=TODAY - timedelta(days=2), meal="lunch", name="Beans", serving="1", servings=1, calories=200, protein_g=10, carb_g=30, fat_g=2, fiber_g=8))
    pep = Peptide(name="Ringfiber Peptide", source=PeptideSource.CUSTOM)
    db.add(pep)
    db.flush()
    proto = Protocol(name="Ringfiber Protocol", start_date=TODAY - timedelta(days=5), owner_id=me)
    db.add(proto)
    db.flush()
    db.add(DoseLog(owner_id=me, protocol_id=proto.id, peptide_id=pep.id, peptide_name="Ringfiber", dose_value=1, dose_unit=DoseUnit.MG, route="oral",
                   scheduled_date=TODAY - timedelta(days=2), scheduled_time_of_day=TimeOfDay.AM, status=DoseStatus.ON_TIME, fiber_g=3))
    db.commit()
    try:
        day = TODAY - timedelta(days=2)
        row = day_overview.day_overviews(db, db.get(User, me), [day])[day]
        grams = {i["name"]: i["grams"] for i in row["macro_ring"]["legend"]}
        assert grams["Fiber"] == 11 and grams["Carbs"] == 30         # carbs are shown as logged, not reduced by the fiber
    finally:
        db.query(DoseLog).delete()
        db.query(Protocol).filter(Protocol.name == "Ringfiber Protocol").delete()
        db.query(Peptide).filter(Peptide.name == "Ringfiber Peptide").delete()
        db.commit()


def test_the_range_ring_has_fiber(client, db, me):
    db.add(FoodLog(owner_id=me, eaten_on=TODAY - timedelta(days=3), meal="lunch", name="Beans", serving="1", servings=1, calories=200, protein_g=10, carb_g=30, fat_g=2, fiber_g=8))
    db.commit()
    page = client.get("/measurements?tab=measurements&range=7d").text
    assert "Fiber: <strong>8 g" in page


def test_the_food_tab_ring_counts_a_fiber_pill_in_the_fiber_slice(client, db, me):
    from tests.test_fiber_supplement import make, log, set_fiber
    from app.models import Medium
    db.add(FoodLog(owner_id=me, eaten_on=TODAY, meal="lunch", name="Beans", serving="1", servings=1, calories=200, protein_g=10, carb_g=30, fat_g=2, fiber_g=8))
    db.commit()
    item_id, pid, piid = make(me, medium=Medium.PILL)
    set_fiber(item_id, 3)
    log(client, pid, piid)
    page = client.get("/measurements?tab=food").text
    seg = page.split("data-eaten-ring", 1)[1][:2500]
    assert "Fiber: <strong>11 g" in seg and "Carbs: <strong>30 g" in seg


def test_the_middle_of_the_ring_is_the_calories_logged_not_worked_out_from_the_grams(db, me):
    db.add(FoodLog(owner_id=me, eaten_on=TODAY - timedelta(days=2), meal="lunch", name="Odd label", serving="1", servings=1, calories=777, protein_g=10, carb_g=10, fat_g=10, fiber_g=1))
    db.commit()
    day = TODAY - timedelta(days=2)
    assert day_overview.day_overviews(db, db.get(User, me), [day])[day]["macro_ring"]["kcal"] == 777


def test_the_food_tab_ring_shows_the_days_logged_calories(client, db, me):
    eat(db, me, 0, protein=30, carb=60, fat=10)
    seg = client.get("/measurements?tab=food").text.split("data-eaten-ring", 1)[1][:2000]
    assert ">450</text>" in seg and ">kcal</text>" in seg


def test_the_range_ring_shows_the_calories_logged_over_the_range(client, db, me):
    eat(db, me, 3, protein=30, carb=60, fat=10)
    eat(db, me, 4, protein=30, carb=60, fat=10)
    seg = client.get("/measurements?tab=measurements&range=7d").text.split("data-range-ring", 1)[1][:2500]
    assert ">900</text>" in seg


def test_nutrition_is_a_choice_in_the_overview_metric_dropdown_not_a_separate_card(client, db, me):
    eat(db, me, 3, protein=30, carb=60, fat=10)
    page = client.get("/measurements?tab=measurements&range=7d").text
    assert '<option value="nutrition">' in page
    assert 'class="overview-chart-panel" data-metric="nutrition" data-range-ring' in page
    assert 'class="card nutrition-range" data-range-ring' not in page          # the old standalone card is gone
    assert page.count("data-range-ring") == 1
