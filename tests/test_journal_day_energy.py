"""The Journal's day rows pull in that day's water, food calories, workout calories burned and the estimated deficit."""

from datetime import date, datetime

import pytest

from app.food import day_overview
from app.measurements import tdee
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, FoodLog, JournalEntry, User, WaterLog)

DAY = date(2026, 9, 2)
OTHER = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    for model in (WaterLog, JournalEntry, FoodLog, BodyMeasurement):
        db.query(model).delete()
    user = db.get(User, me)
    for field in ("sex", "birth_date", "height_in", "activity_level"):
        setattr(user, field, None)
    db.commit()


def log(db, me, calories=500, water=0.0, day=DAY):
    if calories:
        db.add(FoodLog(owner_id=me, eaten_on=day, meal="lunch", name="Test rice", serving="1 cup", servings=1, calories=calories,
                       protein_g=4, carb_g=44, fat_g=0.5, fiber_g=1))
    if water:
        db.add(WaterLog(owner_id=me, logged_at=day, ounces=water))
    db.commit()


def profile(db, me):
    user = db.get(User, me)
    user.sex, user.birth_date, user.height_in, user.activity_level = BiologicalSex.MALE, date(1990, 1, 1), 70, ActivityLevel.MODERATELY_ACTIVE
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1, 8, 0), weight_lbs=190))
    db.commit()
    return user


def test_water_and_food_are_totalled_per_day_and_a_quiet_day_is_absent(db, me):
    log(db, me, calories=300, water=16)
    log(db, me, calories=450, water=8)
    out = day_overview.day_overviews(db, db.get(User, me), [DAY, OTHER])
    assert out[DAY]["water_oz"] == 24 and out[DAY]["eaten_kcal"] == 750
    assert OTHER not in out


def test_without_a_profile_there_is_no_deficit_and_the_reason_is_given(db, me):
    log(db, me)
    row = day_overview.day_overviews(db, db.get(User, me), [DAY])[DAY]
    assert row["deficit"] is None and "profile" in row["deficit_note"]


def test_the_deficit_is_maintenance_plus_burn_minus_eaten(db, me, monkeypatch):
    user = profile(db, me)
    log(db, me, calories=600)
    monkeypatch.setattr(day_overview, "burn_by_day", lambda session, uid: {DAY: 250.0})
    maintenance = tdee.report(male=True, age=date.today().year - 1990 - ((date.today().month, date.today().day) < (1, 1)), height_in=70,
                              weight_lb=190, activity_factor=float(ActivityLevel.MODERATELY_ACTIVE.value), life_stage=user.life_stage).tdee
    row = day_overview.day_overviews(db, user, [DAY])[DAY]
    assert row["burn_kcal"] == 250
    assert row["deficit"]["kcal"] == round(maintenance + 250 - 600) and not row["deficit"]["surplus"]


def test_a_day_with_only_a_workout_still_shows_its_burn(db, me, monkeypatch):
    monkeypatch.setattr(day_overview, "burn_by_day", lambda session, uid: {DAY: 180.0})
    assert day_overview.day_overviews(db, db.get(User, me), [DAY])[DAY]["burn_kcal"] == 180


def test_the_journal_page_shows_the_day_summary(client, db, me, monkeypatch):
    profile(db, me)
    log(db, me, calories=640, water=40)
    db.add(JournalEntry(owner_id=me, entry_date=DAY, mood=3))
    db.commit()
    monkeypatch.setattr(day_overview, "burn_by_day", lambda session, uid: {DAY: 220.0})
    page = client.get("/measurements?tab=journal").text
    assert "Water 40 oz" in page and "640 kcal eaten" in page and "220 kcal burned" in page and "Estimated deficit" in page


def test_a_day_with_nothing_logged_shows_a_dash(client, db, me):
    db.add(JournalEntry(owner_id=me, entry_date=DAY, mood=3))
    db.commit()
    page = client.get("/measurements?tab=journal").text
    assert "kcal eaten" not in page and "Estimated deficit" not in page and "Estimated surplus" not in page
