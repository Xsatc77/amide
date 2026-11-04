"""'Stuck for N weeks' under the Last time line: how long an exercise's weight or reps have not gone up."""

from datetime import date

from app.workouts import logging as workout_logging
from test_workout_logging import plan_with, post_log, weigh_in  # noqa: F401  (weigh_in is a fixture)

WEEKLY = ["2026-04-06", "2026-04-13", "2026-04-20"]
HAMMER = {"completed_ID": "on", "weight_value_ID": "10", "weight_unit_ID": "lb", "sets_value_ID": "3", "reps_value_ID": "10"}


def log_on(client, plan, when, **over):
    day, ex = plan.days[0], plan.days[0].exercises[0]
    fields = {**HAMMER, **over}
    return post_log(client, day, log_date=when, **{k.replace("_ID", f"[{ex.id}]"): v for k, v in fields.items()})


def page_on(client, plan, when):
    return client.get(f"/workouts/day/{plan.days[0].id}/log", params={"log_date": when}).text


def test_three_weeks_at_the_same_weight_and_reps_says_so(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Same")
    for when in WEEKLY:
        log_on(client, plan, when)
    assert "No increase in 3 weeks" in page_on(client, plan, "2026-04-27")


def test_more_weight_resets_it(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Heavier")
    log_on(client, plan, WEEKLY[0])
    log_on(client, plan, WEEKLY[1])
    log_on(client, plan, WEEKLY[2], weight_value_ID="12.5")
    assert "No increase" not in page_on(client, plan, "2026-04-27")


def test_more_reps_at_the_same_weight_resets_it(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Reps")
    log_on(client, plan, WEEKLY[0])
    log_on(client, plan, WEEKLY[1], reps_value_ID="12")
    log_on(client, plan, WEEKLY[2], reps_value_ID="12")
    assert "No increase in 2 weeks" in page_on(client, plan, "2026-04-27")


def test_a_single_session_has_nothing_to_compare(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Once")
    log_on(client, plan, WEEKLY[0])
    assert "No increase" not in page_on(client, plan, "2026-04-27")


def test_under_a_week_is_not_shown(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Days")
    log_on(client, plan, "2026-04-06")
    log_on(client, plan, "2026-04-09")
    assert "No increase" not in page_on(client, plan, "2026-04-10")


def test_one_week_reads_singular(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Singular")
    log_on(client, plan, "2026-04-06")
    log_on(client, plan, "2026-04-09")
    assert "No increase in 1 week" in page_on(client, plan, "2026-04-14") and "1 weeks" not in page_on(client, plan, "2026-04-14")


def test_kilograms_and_pounds_are_compared_as_the_same_load(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Units")
    log_on(client, plan, WEEKLY[0], weight_value_ID="22", weight_unit_ID="lb")
    log_on(client, plan, WEEKLY[1], weight_value_ID="12", weight_unit_ID="kg")      # 26.5 lb: a real gain over 22 lb
    assert "No increase" not in page_on(client, plan, "2026-04-22")


def test_the_same_load_in_another_unit_is_not_a_gain(client, db, weigh_in):
    plan = plan_with(client, db, ["Hammer Curls"], name="Stall Same Unit")
    log_on(client, plan, WEEKLY[0], weight_value_ID="22", weight_unit_ID="lb")
    log_on(client, plan, WEEKLY[1], weight_value_ID="10", weight_unit_ID="kg")      # 22.05 lb: conversion, not progress
    assert "No increase in 3 weeks" in page_on(client, plan, "2026-04-27")


def test_stalled_weeks_function_returns_none_without_history(db):
    assert workout_logging.stalled_weeks(db, 1, "No Such Exercise", "No Such Exercise", date(2026, 4, 27)) is None
