"""Workout log: a weight and reps for each set, and a To Failure mark, per exercise."""
import html
import re

from app.models import WeightUnit
from test_workout_logging import plan_with, post_log, logged, weigh_in  # noqa: F401  (weigh_in is a fixture)
from test_workout_log_form import page


def _fields(ex, **more):
    return {f"completed[{ex.id}]": "on", f"weight_unit[{ex.id}]": "lb", f"sets_value[{ex.id}]": "3",
            f"reps_value[{ex.id}]": "8", **more}


def test_each_set_can_have_its_own_weight_and_reps_and_the_heaviest_is_the_exercises_weight(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Basic Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    r = post_log(client, day, **_fields(ex, **{f"per_set[{ex.id}]": "on", f"set_weight_1[{ex.id}]": "135",
                                               f"set_weight_2[{ex.id}]": "155", f"set_weight_3[{ex.id}]": "145",
                                               f"set_reps_1[{ex.id}]": "10", f"set_reps_2[{ex.id}]": "6", f"set_reps_3[{ex.id}]": "8"}))
    assert r.status_code == 303
    row = logged(db, "Bench Press")
    assert row.set_weight_list == [135.0, 155.0, 145.0] and row.set_rep_list == [10, 6, 8]
    assert (row.weight_value, row.reps_value) == (155.0, 6)           # history, progress and "stalled" read the heaviest set and its reps
    assert row.volume_lb == 135 * 10 + 155 * 6 + 145 * 8              # volume adds each set at its own weight and reps
    assert row.set_weights_text == "135 × 10, 155 × 6, 145 × 8"


def test_without_the_box_the_single_weight_is_used_and_no_set_weights_are_kept(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Off Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"weight_value[{ex.id}]": "185", f"set_weight_1[{ex.id}]": "99"}))
    row = logged(db, "Bench Press")
    assert row.set_weight_list == [] and row.weight_value == 185.0 and row.volume_lb == 185 * 3 * 8
    assert row.to_failure is False


def test_a_missing_set_weight_is_refused_and_keeps_the_old_log(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Missing Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"weight_value[{ex.id}]": "100"}))
    r = post_log(client, day, **_fields(ex, **{f"per_set[{ex.id}]": "on", f"set_weight_1[{ex.id}]": "135", f"set_reps_1[{ex.id}]": "8",
                                               f"set_weight_2[{ex.id}]": "145"}))
    assert r.status_code == 422 and "set 2" in html.unescape(r.text)
    assert logged(db, "Bench Press").weight_value == 100.0


def test_to_failure_is_saved_and_the_reps_stay_the_reps_reached(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Failure Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"weight_value[{ex.id}]": "185", f"to_failure[{ex.id}]": "on"}))
    row = logged(db, "Bench Press")
    assert row.to_failure is True and row.reps_value == 8 and row.net_kcal


def test_the_form_has_both_checkboxes_and_shows_what_was_saved(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press", "Plank"], name="Setw Form Plan")
    day, ex, plank = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    text = page(client, day)
    assert f'name="per_set[{ex.id}]"' in text and f'name="to_failure[{ex.id}]"' in text
    assert f'name="per_set[{plank.id}]"' not in text                  # a timed exercise has no sets or weights
    assert not re.search(rf'name="to_failure\[{ex.id}\]"[^>]*checked', text)
    post_log(client, day, **_fields(ex, **{f"per_set[{ex.id}]": "on", f"set_weight_1[{ex.id}]": "135",
                                           f"set_weight_2[{ex.id}]": "155", f"set_weight_3[{ex.id}]": "145",
                                           f"set_reps_1[{ex.id}]": "10", f"set_reps_2[{ex.id}]": "6", f"set_reps_3[{ex.id}]": "8",
                                           f"to_failure[{ex.id}]": "on"}))
    text = page(client, day)
    assert re.search(rf'name="per_set\[{ex.id}\]"[^>]*checked', text) and re.search(rf'name="to_failure\[{ex.id}\]"[^>]*checked', text)
    assert re.search(rf'name="set_weight_2\[{ex.id}\]"[^>]*value="155"', text)
    assert re.search(rf'name="set_reps_2\[{ex.id}\]"[^>]*value="6"', text)


def test_an_exercise_added_on_the_day_takes_both_too(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Extra Plan")
    day = plan.days[0]
    post_log(client, day, extra={"extra-0-exercise": "Back Squat", "extra-0-weight_unit": "lb", "extra-0-sets_value": "2",
                                 "extra-0-reps_value": "5", "extra-0-per_set": "on", "extra-0-set_weight_1": "225",
                                 "extra-0-set_weight_2": "245", "extra-0-set_reps_1": "5", "extra-0-set_reps_2": "3",
                                 "extra-0-to_failure": "on"})
    row = logged(db, "Back Squat")
    assert row.set_weight_list == [225.0, 245.0] and row.to_failure is True and (row.weight_value, row.reps_value) == (245.0, 3)
    assert 'name="extra-0-per_set"' in page(client, day) and 'name="extra-0-to_failure"' in page(client, day)


def test_kilograms_keep_their_unit_per_set(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Kg Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"weight_unit[{ex.id}]": "kg", f"per_set[{ex.id}]": "on", f"set_weight_1[{ex.id}]": "60",
                                           f"set_weight_2[{ex.id}]": "62.5", f"set_weight_3[{ex.id}]": "65",
                                           f"set_reps_1[{ex.id}]": "8", f"set_reps_2[{ex.id}]": "8", f"set_reps_3[{ex.id}]": "6"}))
    row = logged(db, "Bench Press")
    assert row.weight_unit == WeightUnit.KG and row.set_weights_text == "60 × 8, 62.5 × 8, 65 × 6" and row.weight_value == 65.0


def test_last_time_and_the_export_say_so(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Setw Last Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"per_set[{ex.id}]": "on", f"set_weight_1[{ex.id}]": "135", f"set_weight_2[{ex.id}]": "155",
                                           f"set_weight_3[{ex.id}]": "145", f"set_reps_1[{ex.id}]": "10", f"set_reps_2[{ex.id}]": "6",
                                           f"set_reps_3[{ex.id}]": "8", f"to_failure[{ex.id}]": "on"}))
    text = client.get("/workouts/last", params={"name": "Bench Press"}).json()["last"]["text"]
    assert "135 × 10, 155 × 6, 145 × 8 lb" in text and "to failure" in text
    from app.workouts.export import HEADERS
    assert "Weight and reps each set" in HEADERS and "To failure" in HEADERS


def test_the_script_shows_a_weight_box_for_every_set():
    js = open("app/static/js/workout-log.js", encoding="utf-8").read()
    assert "per_set" in js and "set_weight_" in js and "set_reps_" in js
