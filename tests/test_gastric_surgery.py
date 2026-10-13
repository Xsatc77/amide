"""Prior gastric surgery: no published formula adjusts TDEE for it, so Amide records the flag, says the estimate is less certain, and lets a
measured daily calorie need (from a dietitian or an indirect-calorimetry test) replace the formula everywhere."""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.food import day_overview
from app.measurements import tdee
from app.models import ActivityLevel, BiologicalSex, BodyMeasurement, User

BASE = dict(male=True, age=40, height_in=70, weight_lb=190, activity_factor=1.55)
PROFILE = {"sex": "Male", "birth_date": "1985-01-15", "height_in": "70", "activity_level": "1.55", "macro_goal": "-500", "diet_preset": "balanced"}


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    user = db.get(User, me)
    for field in ("sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset", "measured_tdee"):
        setattr(user, field, None)
    user.prior_gastric_surgery = False
    db.query(BodyMeasurement).delete()
    db.commit()


def test_a_measured_need_replaces_the_formula_and_everything_built_on_it():
    formula = tdee.report(**BASE)
    measured = tdee.report(**BASE, measured=1700)
    assert formula.tdee != 1700 and measured.tdee == 1700
    assert [kcal for _, delta, kcal in measured.goals] == [1700 + delta for _, delta, _ in measured.goals]
    assert abs(sum(measured.split) - 1700) <= 1 and min(measured.split) >= 0
    assert "1,700" in measured.life_stage_note and "measured" in measured.life_stage_note


def test_without_a_measured_need_nothing_changes():
    assert tdee.report(**BASE, measured=None) == tdee.report(**BASE)


def test_the_settings_page_has_the_checkbox_and_the_field(client):
    page = client.get("/settings").text
    assert 'name="prior_gastric_surgery"' in page and 'name="measured_tdee"' in page


def test_the_flag_and_a_measured_need_are_saved(client, db, me):
    r = client.post("/settings/body-profile", data={**PROFILE, "prior_gastric_surgery": "on", "measured_tdee": "1650"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    user = db.get(User, me)
    assert user.prior_gastric_surgery is True and user.measured_tdee == 1650


def test_unticking_the_box_drops_the_measured_need(client, db, me):
    client.post("/settings/body-profile", data={**PROFILE, "prior_gastric_surgery": "on", "measured_tdee": "1650"})
    client.post("/settings/body-profile", data={**PROFILE, "measured_tdee": "1650"})
    db.expire_all()
    user = db.get(User, me)
    assert user.prior_gastric_surgery is False and user.measured_tdee is None


@pytest.mark.parametrize("bad", ["abc", "100", "9000", "-5"])
def test_a_measured_need_must_be_a_sensible_number(client, db, me, bad):
    r = client.post("/settings/body-profile", data={**PROFILE, "prior_gastric_surgery": "on", "measured_tdee": bad}, follow_redirects=False)
    assert r.status_code == 422
    db.expire_all()
    assert db.get(User, me).measured_tdee is None


def test_the_deficit_baseline_uses_the_measured_need_only_when_the_box_is_ticked(db, me):
    user = db.get(User, me)
    user.sex, user.birth_date, user.height_in, user.activity_level = BiologicalSex.MALE, date(1985, 1, 15), 70, ActivityLevel.MODERATELY_ACTIVE
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1, 8, 0), weight_lbs=190))
    user.measured_tdee = 1650
    db.commit()
    assert day_overview.maintenance(db, user)[0] != 1650                       # a stored number is ignored while the box is unticked
    user.prior_gastric_surgery = True
    db.commit()
    assert day_overview.maintenance(db, user) == (1650, None)
    user.measured_tdee = None
    db.commit()
    assert day_overview.maintenance(db, user)[0] not in (None, 1650)           # ticked with no number: the formula, with the caution


def test_the_food_page_says_the_estimate_is_less_certain_after_surgery(client, db, me):
    client.post("/settings/body-profile", data={**PROFILE, "prior_gastric_surgery": "on"})
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1, 8, 0), weight_lbs=190))
    db.commit()
    page = client.get("/measurements?tab=food").text
    assert "prior gastric surgery" in page.lower() and "less certain" in page
    client.post("/settings/body-profile", data=PROFILE)
    assert "less certain" not in client.get("/measurements?tab=food").text
