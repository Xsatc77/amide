"""'Last time' on a planned workout: whenever an exercise comes up again, show what was last done for it (any plan, ticked done or not)."""

import pytest
from sqlalchemy import select

from app.models import WorkoutPlan
from test_workout_logging import plan_with, post_log, weigh_in  # noqa: F401  (weigh_in is a fixture)

FIRST, SECOND = "2026-04-06", "2026-04-13"


def log_of(client, plan, **fields):
    day, ex = plan.days[0], plan.days[0].exercises[0]
    return day, ex, post_log(client, day, log_date=fields.pop("log_date", FIRST),
                             **{k.replace("_ID", f"[{ex.id}]"): v for k, v in fields.items()})


def next_session(client, day, when=SECOND):
    return client.get(f"/workouts/day/{day.id}/log", params={"log_date": when}).text


def test_hammer_curls_show_the_last_performance_with_its_date(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Lasttime Curls")
    day, ex, _ = log_of(client, plan, **{"completed_ID": "on", "weight_value_ID": "30", "weight_unit_ID": "lb", "sets_value_ID": "3", "reps_value_ID": "10"})
    page = next_session(client, day)
    assert "Last time (04/06/2026)" in page and "30 lb" in page and "3 × 10" in page


def test_it_shows_even_when_the_done_box_was_not_ticked(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Lasttime Unticked")
    day, ex, _ = log_of(client, plan, **{"weight_value_ID": "25", "weight_unit_ID": "lb", "sets_value_ID": "4", "reps_value_ID": "8"})
    page = next_session(client, day)
    assert "Last time" in page and "25 lb" in page and "4 × 8" in page


def test_an_empty_row_that_was_never_filled_in_is_not_a_last_time(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Lasttime Empty")
    day, ex, _ = log_of(client, plan)                                   # saved with nothing entered and nothing ticked
    assert "Last time" not in next_session(client, day)


def test_the_most_recent_session_wins_and_follows_the_exercise_into_another_plan(client, db, weigh_in):
    one = plan_with(client, db, ["Hammer Curls"], name="Lasttime One")
    log_of(client, one, **{"completed_ID": "on", "weight_value_ID": "20", "weight_unit_ID": "lb", "sets_value_ID": "3", "reps_value_ID": "10"})
    log_of(client, one, log_date="2026-04-08", **{"completed_ID": "on", "weight_value_ID": "35", "weight_unit_ID": "lb", "sets_value_ID": "3", "reps_value_ID": "12"})
    two = plan_with(client, db, ["Hammer Curl"], name="Lasttime Two")             # same exercise, another plan and wording
    page = next_session(client, two.days[0], "2026-04-20")
    assert "35 lb" in page and "3 × 12" in page and "20 lb" not in page.split("Last time")[1][:60]


def test_an_exercise_that_is_not_in_the_database_is_matched_by_its_own_name(client, db, weigh_in):
    plan = plan_with(client, db, ["Zork Xylo Press"], name="Lasttime Custom")
    day, ex, _ = log_of(client, plan, **{"completed_ID": "on", "weight_value_ID": "45", "weight_unit_ID": "lb", "sets_value_ID": "2", "reps_value_ID": "6"})
    page = next_session(client, day)
    assert "Last time" in page and "45 lb" in page and "2 × 6" in page
