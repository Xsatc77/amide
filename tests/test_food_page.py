import html
import re
from datetime import date, datetime, timedelta

import pytest

from app.food import summary as food_summary
from app.food.targets import day_targets
from app.measurements import tdee
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, DietPreset, FoodLog, MacroGoal, User)
from photo_helpers import other_client


def profile(db, me, **kw):
    user = db.get(User, me)
    fields = dict(sex=BiologicalSex.MALE, birth_date=date(1990, 1, 1), height_in=70, activity_level=ActivityLevel.MODERATELY_ACTIVE,
                  macro_goal=MacroGoal.MODERATE_LOSS, diet_preset=DietPreset.BALANCED)
    for key, value in {**fields, **kw}.items():
        setattr(user, key, value)
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 10, 1, 8, 0), weight_lbs=190))
    db.commit()
    return user


def expected_tdee(user):
    today = date.today()
    age = today.year - user.birth_date.year - ((today.month, today.day) < (user.birth_date.month, user.birth_date.day))
    return tdee.report(male=True, age=age, height_in=user.height_in, weight_lb=190, activity_factor=float(user.activity_level.value),
                       life_stage=user.life_stage).tdee


def entry(db, me, day, meal="lunch", name="Test rice", **kw):
    row = dict(owner_id=me, eaten_on=day, meal=meal, name=name, serving="1 cup", servings=1, calories=200, protein_g=4, carb_g=44, fat_g=0.5, fiber_g=1)
    db.add(FoodLog(**{**row, **kw}))
    db.commit()


def page(client, query=""):
    return html.unescape(client.get(f"/measurements?tab=food{query}").text)


def reset_profile(db, me):
    user = db.get(User, me)
    for field in ("sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset", "custom_protein_pct",
                  "custom_carb_pct", "custom_fat_pct"):
        setattr(user, field, None)
    db.commit()


@pytest.fixture(autouse=True)
def clean_profile(client, db, me):
    yield
    reset_profile(db, me)


def test_the_food_tab_replaces_macros_and_the_old_address_still_works(client, db, me):
    r = client.get("/measurements?tab=macros", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/measurements?tab=food"
    text = client.get("/measurements?tab=measurements").text
    assert "tab=food" in text and ">Food<" in text and "tab=macros" not in text


def test_without_a_profile_the_tab_says_what_is_missing_but_the_log_still_shows(client, db, me):
    entry(db, me, date.today(), name="Plain toast")
    text = page(client)
    assert "body profile" in text.lower() and "Plain toast" in text


def test_without_a_weigh_in_the_tab_asks_for_one(client, db, me):
    profile(db, me)
    db.query(BodyMeasurement).delete()
    db.commit()
    assert "weigh-in" in page(client).lower()


def test_a_custom_diet_with_no_percentages_asks_for_them(client, db, me):
    profile(db, me, diet_preset=DietPreset.CUSTOM)
    assert "custom" in page(client).lower() and "percent" in page(client).lower()


def test_the_calorie_limit_is_the_energy_tabs_tdee_plus_the_goal_offset(client, db, me):
    user = profile(db, me)
    limit = day_targets(expected_tdee(user), user.macro_goal, user.diet_preset, None, user.sex).calories
    assert f"{limit:,}" in page(client)


def test_eaten_remaining_and_the_deficit_with_workout_burn(client, db, me, monkeypatch):
    user = profile(db, me)
    monkeypatch.setattr(food_summary, "workout_burn", lambda session, uid, day: 300.0)
    entry(db, me, date.today(), calories=500)
    entry(db, me, date.today(), calories=400, meal="dinner", name="Second")
    t = expected_tdee(user)
    limit = day_targets(t, user.macro_goal, user.diet_preset, None, user.sex).calories
    text = page(client)
    assert "900" in text and f"{limit - 900:,}" in text                      # eaten and remaining
    assert f"{t + 300 - 900:,}" in text and "estimate" in text.lower()       # potential deficit
    summary = food_summary.day_summary(db, user, date.today())
    assert summary["deficit"]["kcal"] == t + 300 - 900 and summary["workout_burn"] == 300


def test_eating_more_than_burned_is_shown_as_a_surplus(client, db, me, monkeypatch):
    profile(db, me)
    monkeypatch.setattr(food_summary, "workout_burn", lambda session, uid, day: 0.0)
    entry(db, me, date.today(), calories=4000, protein_g=10, carb_g=10, fat_g=10)
    assert "surplus" in page(client).lower()


def test_the_pie_chart_and_legend_follow_the_diet_type(client, db, me):
    profile(db, me, diet_preset=DietPreset.KETO)
    text = page(client)
    assert "<svg" in text and re.search(r"Fat[^%]{0,80}75%", text) and re.search(r"Protein[^%]{0,80}20%", text)
    profile_user = db.get(User, me)
    profile_user.diet_preset = DietPreset.HIGH_PROTEIN
    db.commit()
    text = page(client)
    assert re.search(r"Protein[^%]{0,80}40%", text) and re.search(r"Carbs[^%]{0,80}30%", text)


def test_fulfilment_bars_show_eaten_against_target_with_a_state(client, db, me):
    profile(db, me)
    entry(db, me, date.today(), calories=100, protein_g=1, carb_g=1, fat_g=1, fiber_g=0)
    text = page(client)
    for label in ("Calories", "Protein", "Carbs", "Fat", "Fiber"):
        assert label in text
    assert 'data-state="under"' in text


def test_meals_are_listed_in_order_with_their_subtotals(client, db, me):
    profile(db, me)
    entry(db, me, date.today(), meal="dinner", name="Supper dish", calories=600)
    entry(db, me, date.today(), meal="breakfast", name="Morning oats", calories=250)
    text = page(client)
    assert text.index("Morning oats") < text.index("Supper dish")
    for heading in ("Breakfast", "Lunch", "Dinner", "Snacks"):
        assert heading in text


def test_another_day_shows_that_days_entries_and_a_bad_date_means_today(client, db, me):
    profile(db, me)
    yesterday = date.today() - timedelta(days=1)
    entry(db, me, yesterday, name="Yesterday stew")
    assert "Yesterday stew" in page(client, f"&date={yesterday.isoformat()}")
    assert "Yesterday stew" not in page(client)
    assert "Yesterday stew" not in page(client, "&date=garbage")
    assert "Yesterday stew" not in page(client, "&date=1999-01-01")


def test_other_peoples_entries_never_appear(client, db, me):
    profile(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        entry(db, other_id, date.today(), name="Someone elses lunch")
        assert "Someone elses lunch" not in page(client)
        assert "Someone elses lunch" in html.unescape(other.get("/measurements?tab=food").text)


def test_changing_the_diet_type_and_goal_saves_to_the_profile(client, db, me):
    profile(db, me)
    r = client.post("/food/settings", data={"diet_preset": "keto", "macro_goal": "0"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/measurements?tab=food")
    db.expire_all()
    user = db.get(User, me)
    assert (user.diet_preset, user.macro_goal) == (DietPreset.KETO, MacroGoal.MAINTAIN)


def test_a_custom_split_must_total_100_and_is_saved_when_it_does(client, db, me):
    profile(db, me)
    bad = client.post("/food/settings", data={"diet_preset": "custom", "macro_goal": "0", "custom_protein_pct": "30",
                                              "custom_carb_pct": "30", "custom_fat_pct": "30"})
    assert bad.status_code == 422 and "100" in html.unescape(bad.text)
    db.expire_all()
    assert db.get(User, me).diet_preset == DietPreset.BALANCED
    ok = client.post("/food/settings", data={"diet_preset": "custom", "macro_goal": "0", "custom_protein_pct": "30",
                                             "custom_carb_pct": "40", "custom_fat_pct": "30"}, follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all()
    assert (db.get(User, me).custom_protein_pct, db.get(User, me).diet_preset) == (30, DietPreset.CUSTOM)


def test_unknown_diet_or_goal_values_are_rejected(client, db, me):
    profile(db, me)
    assert client.post("/food/settings", data={"diet_preset": "carnivore", "macro_goal": "0"}).status_code == 422
    assert client.post("/food/settings", data={"diet_preset": "keto", "macro_goal": "99"}).status_code == 422


def test_the_dashboard_water_link_points_at_the_food_tab(client, db, me):
    profile(db, me)
    assert "/measurements?tab=food" in client.get("/dashboard").text
