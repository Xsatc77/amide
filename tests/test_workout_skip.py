"""Workout log: "Skip this exercise" with the person's own reason, per plan exercise."""
import html

from test_workout_logging import plan_with, post_log, logged, weigh_in  # noqa: F401  (weigh_in is a fixture)
from test_workout_log_form import page


def _fields(ex, **more):
    return {f"weight_value[{ex.id}]": "185", f"weight_unit[{ex.id}]": "lb", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8", **more}


def test_a_skipped_exercise_keeps_its_reason_and_no_numbers(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Skip Basic Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    r = post_log(client, day, **_fields(ex, **{f"skipped[{ex.id}]": "on", f"skip_reason[{ex.id}]": "  Shoulder was sore  "}))
    assert r.status_code == 303
    row = logged(db, "Bench Press")
    assert row.skipped is True and row.skip_reason == "Shoulder was sore"
    assert row.completed is False and row.weight_value is None and row.sets is None and row.reps_value is None and row.net_kcal is None


def test_skip_wins_over_done_and_a_reason_without_a_skip_is_dropped(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press", "Plank"], name="Skip Rules Plan")
    day, bench, plank = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    post_log(client, day, **_fields(bench, **{f"completed[{bench.id}]": "on", f"skipped[{bench.id}]": "on"}),
             extra={f"completed[{plank.id}]": "on", f"skip_reason[{plank.id}]": "not skipped, so not kept"})
    skipped, done = logged(db, "Bench Press"), logged(db, "Plank")
    assert skipped.skipped is True and skipped.completed is False and skipped.skip_reason is None
    assert done.skipped is False and done.completed is True and done.skip_reason is None


def test_a_long_reason_is_cut_to_the_column_size(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Skip Long Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    assert post_log(client, day, **{f"skipped[{ex.id}]": "on", f"skip_reason[{ex.id}]": "x" * 500}).status_code == 303
    assert len(logged(db, "Bench Press").skip_reason) == 200


def test_the_form_offers_the_box_and_the_reason_and_shows_what_was_saved(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Skip Form Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    text = page(client, day)
    assert f'name="skipped[{ex.id}]"' in text and "Skip this exercise" in text
    assert f'name="skip_reason[{ex.id}]"' in text and 'placeholder="Reason for Skip?"' in text
    assert f'name="skipped[{ex.id}]" data-skip checked' not in text
    post_log(client, day, **{f"skipped[{ex.id}]": "on", f"skip_reason[{ex.id}]": "No bench free"})
    text = html.unescape(page(client, day))
    assert f'name="skipped[{ex.id}]" data-skip checked' in text and 'value="No bench free"' in text


def test_a_skip_is_not_a_performance_for_last_time(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Skip Last Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **_fields(ex, **{f"completed[{ex.id}]": "on"}), extra={"log_date": "2026-03-01"})
    post_log(client, day, **{f"skipped[{ex.id}]": "on", f"skip_reason[{ex.id}]": "Travel"}, extra={"log_date": "2026-03-08"})
    assert "185" in client.get("/workouts/last", params={"name": "Bench Press"}).json()["last"]["text"]


def test_the_export_and_the_script_know_about_skips():
    from app.workouts.export import HEADERS
    assert HEADERS[-2:] == ("Skipped", "Skip reason")
    js = open("app/static/js/workout-log.js", encoding="utf-8").read()
    assert "data-skip" in js and "[data-skip]:checked" in js
