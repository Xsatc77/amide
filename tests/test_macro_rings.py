"""Donut rings of what was eaten: one per day (Food tab and Journal) and one for the chosen range on the Measurements tab."""

from datetime import date, timedelta

import pytest

from app import clock
from app.food import day_overview, rings
from app.models import FoodLog, JournalEntry, User

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


def test_the_ring_splits_calories_by_macro_at_4_4_and_9_per_gram():
    r = rings.macro_ring(50, 100, 20)
    assert [(i["name"], i["grams"], i["pct"]) for i in r["legend"]] == [("Protein", 50, 26), ("Carbs", 100, 51), ("Fat", 20, 23)]
    assert r["kcal"] == 780 and len(r["ring"]["slices"]) == 3


def test_no_macros_means_no_ring():
    assert rings.macro_ring(0, 0, 0) is None


def test_a_macro_with_nothing_is_left_out_of_the_ring_but_stays_in_the_legend():
    r = rings.macro_ring(40, 0, 10)
    assert len(r["ring"]["slices"]) == 2 and [i["name"] for i in r["legend"]] == ["Protein", "Carbs", "Fat"]


def test_a_journal_day_row_carries_that_days_ring(db, me):
    eat(db, me, 2, protein=30, carb=60, fat=10)
    day = TODAY - timedelta(days=2)
    row = day_overview.day_overviews(db, db.get(User, me), [day])[day]
    assert row["macro_ring"]["kcal"] == 450


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
