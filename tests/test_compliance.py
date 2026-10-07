"""The dashboard's compliance bars: Protocol, H2O, Diet and Workout, each 0-100% over a chosen window."""

import html
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import compliance as comp
from app.db import SessionLocal
from app.models import (
    DoseLog, DoseStatus, DoseUnit, FoodLog, Frequency, Peptide, Protocol, ProtocolItem, Route, TimeOfDay, User, WaterLog, WorkoutLog, WorkoutPlan,
    WorkoutPlanDay, WorkoutSource,
)

TODAY = date.today()


@pytest.fixture(autouse=True)
def _clean_compliance_rows(client, me):
    """Water logs and the water goal are not cleared between tests by the shared cleanup, so these tests do it themselves."""
    def wipe():
        with SessionLocal() as s:
            s.query(WaterLog).delete()
            s.get(User, me).water_goal_oz = None
            s.commit()
    wipe()
    yield
    wipe()


def day(n):
    return TODAY - timedelta(days=n)


def bars_by_key(user_id, window=30):
    with SessionLocal() as s:
        user = s.get(User, user_id)
        return {b["key"]: b for b in comp.bars(s, user, TODAY, window)}


# ---------------------------------------------------------------- the rules

@pytest.mark.parametrize("raw,expected", [("7", 7), ("14", 14), ("30", 30), ("90", 90), ("180", 180), ("360", 360), ("lifetime", 0), ("0", 0),
                                          ("", 30), ("5", 30), ("junk", 30), (None, 30)])
def test_the_window_is_one_of_the_offered_choices_or_30(raw, expected):
    assert comp.parse_window(raw) == expected


@pytest.mark.parametrize("oz,ok", [(100, True), (90, True), (110, True), (89.9, False), (0, False), (150, True), (1000, True)])
def test_a_water_day_is_compliant_at_ninety_percent_of_the_goal_or_more(oz, ok):
    assert comp.water_day_ok(oz, 100) is ok
    assert comp.water_day_ok(10, 0) is False


@pytest.mark.parametrize("calories,protein,ok", [(1800, 140, True), (2000, 135, True), (2001, 200, False), (1500, 134, False), (0, 0, False)])
def test_a_diet_day_needs_calories_within_the_limit_and_protein_at_ninety_percent(calories, protein, ok):
    assert comp.diet_day_ok(calories, protein, calorie_limit=2000, protein_target=150) is ok


# ---------------------------------------------------------------- protocol

def add_protocol(uid, start_days_ago=5, every=5):
    with SessionLocal() as s:
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide")) or Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        protocol = Protocol(name="Fat Loss", start_date=day(start_days_ago), owner_id=uid)
        s.add(protocol)
        s.flush()
        item = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG, frequency=Frequency.EVERY_N_DAYS,
                            every_n_days=every, route=Route.SUBQ)
        s.add(item)
        s.commit()
        return protocol.id, item.id, peptide.id


def log_dose(uid, protocol_id, item_id, peptide_id, days_ago):
    with SessionLocal() as s:
        s.add(DoseLog(owner_id=uid, protocol_id=protocol_id, protocol_item_id=item_id, peptide_id=peptide_id, peptide_name="Retatrutide", dose_value=2.0,
                      dose_unit=DoseUnit.MG, route="subq", scheduled_date=day(days_ago), scheduled_time_of_day=TimeOfDay.ANY, status=DoseStatus.ON_TIME,
                      logged_at=datetime.now(timezone.utc)))
        s.commit()


def test_protocol_counts_logged_doses_over_doses_due_and_a_dose_pending_today_is_not_yet_missed(client, db, me):
    protocol_id, item_id, peptide_id = add_protocol(me)                    # due 5 days ago and today
    assert bars_by_key(me)["protocol"]["pct"] == 0 and bars_by_key(me)["protocol"]["total"] == 1       # today's still pending: only the old one counts
    log_dose(me, protocol_id, item_id, peptide_id, 5)
    assert bars_by_key(me)["protocol"]["pct"] == 100
    log_dose(me, protocol_id, item_id, peptide_id, 0)
    assert (bars_by_key(me)["protocol"]["good"], bars_by_key(me)["protocol"]["total"]) == (2, 2)


def test_protocol_has_no_data_without_any_dose_due(client, db, me):
    bar = bars_by_key(me)["protocol"]
    assert bar["pct"] is None and bar["total"] == 0


def test_the_protocol_window_limits_which_doses_count(client, db, me):
    protocol_id, item_id, peptide_id = add_protocol(me, start_days_ago=20, every=5)      # due 20, 15, 10, 5 days ago and today
    log_dose(me, protocol_id, item_id, peptide_id, 5)
    assert bars_by_key(me, 7)["protocol"]["pct"] == 100                                     # only the dose 5 days ago falls in the last 7 days
    assert bars_by_key(me, 30)["protocol"]["pct"] == 25                                     # 1 of the 4 completed doses
    assert bars_by_key(me, 0)["protocol"]["pct"] == 25                                      # lifetime starts when the protocol did


# ---------------------------------------------------------------- water

def set_goal(uid, oz=100):
    with SessionLocal() as s:
        s.get(User, uid).water_goal_oz = oz
        s.commit()


def log_water(uid, days_ago, oz):
    with SessionLocal() as s:
        s.add(WaterLog(owner_id=uid, logged_at=day(days_ago), ounces=oz))
        s.commit()


def test_water_counts_days_at_ninety_percent_or_more_from_the_first_log_and_ignores_today(client, db, me):
    set_goal(me)
    for days_ago, oz in ((6, 100), (4, 120), (3, 85), (2, 95), (1, 100)):             # day 5 has nothing logged
        log_water(me, days_ago, oz)
    bar = bars_by_key(me)["h2o"]
    assert (bar["good"], bar["total"], bar["pct"]) == (4, 6, 67)                       # 100, 120 (over the goal still counts), 95 and 100 pass; 85 and the empty day do not
    log_water(me, 0, 10)                                                                     # today is unfinished: it changes nothing
    assert bars_by_key(me)["h2o"]["total"] == 6


def test_water_windows_and_lifetime(client, db, me):
    set_goal(me)
    for days_ago, oz in ((6, 100), (4, 120), (3, 85), (2, 95), (1, 100)):
        log_water(me, days_ago, oz)
    assert bars_by_key(me, 3)["h2o"]["pct"] == 100                                           # window 3 = today plus the 2 days before: 95 and 100 pass
    assert bars_by_key(me, 7)["h2o"]["total"] == 6 and bars_by_key(me, 0)["h2o"]["total"] == 6


def test_water_has_no_data_without_logs_or_a_goal(client, db, me):
    assert bars_by_key(me)["h2o"]["pct"] is None
    log_water(me, 2, 100)
    assert "goal" in bars_by_key(me)["h2o"]["note"].lower() and bars_by_key(me)["h2o"]["pct"] is None     # logs but no weight or goal to compare with


# ---------------------------------------------------------------- diet

def log_food(uid, days_ago, calories, protein):
    with SessionLocal() as s:
        s.add(FoodLog(owner_id=uid, eaten_on=day(days_ago), meal="lunch", name="Test food", serving="1", servings=1, calories=calories, protein_g=protein,
                      carb_g=0, fat_g=0, fiber_g=0))
        s.commit()


def test_diet_counts_days_with_calories_in_the_limit_and_enough_protein(client, db, me, monkeypatch):
    monkeypatch.setattr(comp, "_diet_targets", lambda session, user, today: (2000, 150))
    for days_ago, cal, pro in ((4, 1800, 140), (3, 2100, 150), (2, 1900, 120)):             # day 1 has nothing logged
        log_food(me, days_ago, cal, pro)
    bar = bars_by_key(me)["diet"]
    assert (bar["good"], bar["total"], bar["pct"]) == (1, 4, 25)


def test_several_foods_in_a_day_add_up(client, db, me, monkeypatch):
    monkeypatch.setattr(comp, "_diet_targets", lambda session, user, today: (2000, 150))
    log_food(me, 2, 900, 70)
    log_food(me, 2, 900, 70)                                                                  # 1800 cal, 140 g protein together
    log_food(me, 1, 1000, 100)
    bar = bars_by_key(me)["diet"]
    assert (bar["good"], bar["total"]) == (1, 2)


def test_diet_has_no_data_without_food_or_a_profile(client, db, me):
    assert bars_by_key(me)["diet"]["pct"] is None
    log_food(me, 3, 1800, 140)
    bar = bars_by_key(me)["diet"]
    assert bar["pct"] is None and "profile" in bar["note"].lower()                           # the Food tab needs a profile and a weigh-in for targets


# ---------------------------------------------------------------- workouts

def add_plan(uid, started_days_ago=5, weekdays="MTWRFSU"):
    with SessionLocal() as s:
        plan = WorkoutPlan(owner_id=uid, name="Plan", source=WorkoutSource.MANUAL, started_on=day(started_days_ago))
        s.add(plan)
        s.flush()
        plan_day = WorkoutPlanDay(plan_id=plan.id, position=0, label="Day", weekdays=weekdays)
        s.add(plan_day)
        s.commit()
        return plan_day.id


def log_workout(uid, plan_day_id, days_ago):
    with SessionLocal() as s:
        s.add(WorkoutLog(owner_id=uid, plan_day_id=plan_day_id, log_date=day(days_ago), day_label="Day", plan_name="Plan"))
        s.commit()


def test_workouts_count_scheduled_days_done_and_a_day_still_to_do_today_is_not_missed(client, db, me):
    plan_day = add_plan(me)                                                                  # every day, since 5 days ago
    log_workout(me, plan_day, 4)
    log_workout(me, plan_day, 3)
    bar = bars_by_key(me)["workout"]
    assert (bar["good"], bar["total"], bar["pct"]) == (2, 5, 40)                              # days 5..1 ago; today is still open
    log_workout(me, plan_day, 0)
    assert (bars_by_key(me)["workout"]["good"], bars_by_key(me)["workout"]["total"]) == (3, 6)


def test_workouts_only_count_days_the_plan_has_scheduled(client, db, me):
    wd = "MTWRFSU"[(TODAY - timedelta(days=1)).weekday()]                                    # only yesterday's weekday is a workout day
    plan_day = add_plan(me, started_days_ago=6, weekdays=wd)
    log_workout(me, plan_day, 1)
    bar = bars_by_key(me)["workout"]
    assert (bar["good"], bar["total"]) == (1, 1) and bar["pct"] == 100


def test_workouts_have_no_data_without_an_active_plan(client, db, me):
    assert bars_by_key(me)["workout"]["pct"] is None


# ---------------------------------------------------------------- the dashboard

def test_the_dashboard_shows_four_labelled_bars_scaled_to_percent(client, db, me):
    set_goal(me)
    for days_ago, oz in ((3, 100), (2, 100), (1, 50)):
        log_water(me, days_ago, oz)
    page = html.unescape(client.get("/dashboard").text)
    for label in ("Protocol", "H2O", "Diet", "Workout"):
        assert f'data-bar="{label}"' in page or label in page
    assert page.count('class="compliance-bar') == 4
    assert "height: 67%" in page                                                              # H2O: 2 of 3 completed days
    assert page.count("No data") == 3


def test_the_window_choice_comes_from_the_query_and_the_selected_one_is_marked(client, db, me):
    for choice, label in ((7, "7 days"), (14, "14 days"), (30, "30 days"), (90, "90 days"), (180, "180 days"), (360, "360 days"), ("lifetime", "Lifetime")):
        page = html.unescape(client.get(f"/dashboard?compliance={choice}").text)
        assert label in page and f'href="/dashboard?compliance={choice}"' in page
    page = client.get("/dashboard?compliance=7").text
    assert 'aria-current="true"' in page.split('class="compliance-windows"')[1].split("</nav>")[0]
    assert "30 days" in html.unescape(client.get("/dashboard?compliance=999").text)           # an unknown choice falls back to 30


def test_the_old_adherence_percentage_still_reads_the_same_for_a_protocol(client, db, me):
    protocol_id, item_id, peptide_id = add_protocol(me)
    log_dose(me, protocol_id, item_id, peptide_id, 0)
    log_dose(me, protocol_id, item_id, peptide_id, 5)
    assert "100%" in html.unescape(client.get("/dashboard").text)
