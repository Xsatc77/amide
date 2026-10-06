import html
import re

import pytest
from sqlalchemy import select

from app.models import BodyMeasurement, WorkoutPlan
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401  (weigh_in is a fixture)


def page(client, day, date=DAY):
    return html.unescape(client.get(f"/workouts/day/{day.id}/log", params={"log_date": date}).text)


def test_the_form_has_a_body_weight_field_and_whole_number_inputs(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Basic Plan", sets="3", reps="8-12")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    text = page(client, day)
    assert re.search(r'name="body_weight_lb" value="200"', text)
    assert f'name="sets_value[{ex.id}]" value="3"' in text            # a single number on the plan pre-fills sets
    assert re.search(rf'name="reps_value\[{ex.id}\]" value=""', text)   # a range pre-fills nothing: the real count is typed
    assert re.search(rf'<input type="number"[^>]*min="1"[^>]*step="1"[^>]*name="reps_value\[{ex.id}\]"', text)


def test_a_range_on_the_plan_does_not_prefill_sets_either(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Range Plan", sets="3-4")
    ex = plan.days[0].exercises[0]
    assert f'name="sets_value[{ex.id}]" value=""' in page(client, plan.days[0])


def test_cardio_rows_ask_for_minutes_and_speed_or_grade(client, db, weigh_in):
    plan = plan_with(client, db, ["Treadmill Run", "Treadmill Incline Walk", "Plank"], name="Form Cardio Plan")
    day = plan.days[0]
    run, incline, plank = day.exercises
    text = page(client, day)
    assert f'name="minutes_value[{run.id}]"' in text and f'name="speed_value[{run.id}]"' in text
    assert f'name="grade_value[{incline.id}]"' in text and f'name="speed_value[{incline.id}]"' not in text
    assert f'name="minutes_value[{plank.id}]"' in text and f'name="sets_value[{plank.id}]"' not in text


def test_rep_rows_offer_style_and_implements_under_adjust(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Adjust Plan")
    ex = plan.days[0].exercises[0]
    text = page(client, plan.days[0])
    assert f'name="style_value[{ex.id}]"' in text and f'name="implements_value[{ex.id}]"' in text
    assert re.search(r'<option value="Heavy Strength" selected>', text)      # the exercise's own default style


def test_an_unmatched_exercise_says_it_has_no_estimate(client, db, weigh_in):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Form Unmatched Plan")
    assert "no calorie estimate" in page(client, plan.days[0])


def test_the_page_offers_the_exercise_list_and_an_add_row(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Extra Plan")
    text = page(client, plan.days[0])
    assert 'id="exercise-names"' in text and text.count("<option value=") > 230
    assert 'id="extra-row-template"' in text and 'data-action="add-extra"' in text and "extra-__I__-exercise" in text


def test_exercises_added_on_the_day_come_back_prefilled(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Extra Saved Plan")
    day = plan.days[0]
    post_log(client, day, extra={"extra-0-exercise": "DB incline press", "extra-0-weight_value": "40",
                                 "extra-0-sets_value": "3", "extra-0-reps_value": "10"})
    text = page(client, day)
    assert 'name="extra-0-exercise" value="Dumbbell Incline Press"' in text
    assert 'name="extra-0-weight_value" value="40"' in text and "kcal" in text


def test_no_weigh_in_tells_the_person_how_to_get_estimates(client, db, me):
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()
    plan = plan_with(client, db, ["Bench Press"], name="Form No Weight Plan")
    text = page(client, plan.days[0])
    assert 'name="body_weight_lb" value=""' in text and "Measurements" in text


def test_estimates_are_labeled_as_estimates(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Form Label Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    text = page(client, day)
    assert "estimated" in text and re.search(r"≈ \d+ kcal", text)
