from datetime import date

import pytest

from app.workouts import progress
from app.workouts.charts import nice_ceiling, ring, stacked_bars
from app.workouts.progress import LoggedExercise as L

D = date


def row(day, name, *, area="Chest", equipment="Barbell", load=None, sets=None, reps=None, volume=None, net=None):
    return L(day, name, area, equipment, load, sets, reps, volume, net, None if net is None else net * 1.4)


@pytest.mark.parametrize("value,top", [(0, 4), (3, 3), (950, 1000), (1010, 1200), (2763, 3000), (320, 320), (87, 100), (5, 5)])
def test_nice_ceiling_gives_round_axis_maxima(value, top):
    assert nice_ceiling(value) == top


def test_stacked_bars_stack_in_order_and_start_at_zero():
    chart = stacked_bars([D(2026, 10, 1), D(2026, 10, 2)], {"BMR": [1800, 1800], "Workout": [300, 0]}, line=[2300, 2300])
    first, second = chart["bars"]
    assert [s["name"] for s in first["segments"]] == ["BMR", "Workout"] and [s["name"] for s in second["segments"]] == ["BMR"]
    bmr, workout = first["segments"]
    assert workout["y"] + workout["h"] == pytest.approx(bmr["y"], abs=0.2)           # the workout sits on the BMR segment
    assert bmr["y"] + bmr["h"] == pytest.approx(chart["plot"]["bottom"], abs=0.2)    # and the stack starts at the axis
    assert chart["top"] >= 2300 and chart["y_ticks"][0]["value"] == 0
    assert chart["line"]["points"][0]["value"] == 2300 and chart["line"]["poly"].count(",") == 2


def test_the_axis_top_covers_the_tallest_stack_or_the_target_line():
    low = stacked_bars([D(2026, 10, 1)], {"A": [100]}, line=[900])
    assert low["top"] >= 900
    assert stacked_bars([D(2026, 10, 1)], {"A": [100]})["line"] is None


def test_stacked_bars_with_no_data_draw_nothing():
    assert stacked_bars([], {"A": []}) is None and stacked_bars([D(2026, 10, 1)], {}) is None


def test_negative_values_never_draw_below_the_axis():
    chart = stacked_bars([D(2026, 10, 1)], {"A": [-5], "B": [10]})
    assert [s["name"] for s in chart["bars"][0]["segments"]] == ["B"] and chart["bars"][0]["total"] == 10


def test_a_ring_shares_sum_to_one_and_one_slice_is_still_drawable():
    chart = ring([("Barbell", 300), ("Dumbbell", 100), ("Cable", 0), ("Machine", None)])
    assert [s["name"] for s in chart["slices"]] == ["Barbell", "Dumbbell"]
    assert sum(s["share"] for s in chart["slices"]) == pytest.approx(1.0) and chart["total"] == 400
    assert all(s["path"].startswith("M ") and s["path"].endswith("Z") for s in chart["slices"])
    only = ring([("Barbell", 5)])
    assert only["slices"][0]["share"] == 1.0 and "A 90 90 0 1 1" in only["slices"][0]["path"]
    assert ring([]) is None and ring([("A", 0)]) is None


ROWS = [
    row(D(2026, 9, 1), "Bench Press", load=135, sets=3, reps=8, volume=3240, net=30),
    row(D(2026, 9, 1), "Bench Press", load=155, sets=2, reps=5, volume=1550, net=18),
    row(D(2026, 9, 8), "Bench Press", load=145, sets=3, reps=10, volume=4350, net=34),
    row(D(2026, 9, 9), "Back Squat", area="Quads/Glutes", load=185, sets=3, reps=5, volume=2775, net=40),
    row(D(2026, 9, 22), "Dumbbell Curl", area="Biceps", equipment="Dumbbell", load=30, sets=3, reps=12, volume=1080, net=12),
    row(D(2026, 9, 22), "Plank", area="Core", equipment="Bodyweight", net=9),
]


def test_exercise_history_has_one_point_per_day_with_the_top_load_and_total_volume():
    points = progress.exercise_history(ROWS, "Bench Press")
    assert [(p.log_date, p.top_load_lb, p.volume_lb, p.net_kcal) for p in points] == [
        (D(2026, 9, 1), 155, 4790, 48), (D(2026, 9, 8), 145, 4350, 34)]
    assert progress.exercise_history(ROWS, "Plank")[0].top_load_lb is None
    assert progress.exercise_history(ROWS, "Nothing") == []


def test_exercises_are_listed_most_recent_first():
    assert progress.exercises_by_recency(ROWS) == ["Dumbbell Curl", "Plank", "Back Squat", "Bench Press"]


def test_personal_records_per_exercise_with_dates():
    bench = next(r for r in progress.personal_records(ROWS) if r.name == "Bench Press")
    assert bench.heaviest == (155, D(2026, 9, 1)) and bench.most_reps == (10, D(2026, 9, 8))
    assert bench.biggest_volume == (4790, D(2026, 9, 1))
    plank = next(r for r in progress.personal_records(ROWS) if r.name == "Plank")
    assert (plank.heaviest, plank.most_reps, plank.biggest_volume) == (None, None, None)


def test_weekly_volume_by_area_includes_empty_weeks_and_orders_by_total():
    weeks, areas = progress.weekly_volume_by_area(ROWS)
    assert weeks[0] == D(2026, 8, 31) and weeks[-1] == D(2026, 9, 21) and len(weeks) == 4
    assert list(areas) == ["Chest", "Quads/Glutes", "Biceps"]
    assert areas["Chest"] == [4790, 4350, 0, 0] and areas["Biceps"] == [0, 0, 0, 1080]
    assert progress.weekly_volume_by_area([row(D(2026, 9, 1), "Plank", net=5)]) == ([], {})


def test_burn_breakdowns_and_daily_totals():
    assert progress.burn_by_equipment(ROWS) == [("Barbell", 122), ("Dumbbell", 12), ("Bodyweight", 9)]
    assert progress.top_exercises_by_kcal(ROWS, 2) == [("Bench Press", 82), ("Back Squat", 40)]
    assert progress.daily_net_kcal(ROWS)[D(2026, 9, 22)] == 21 and len(progress.daily_net_kcal(ROWS)) == 4
    assert progress.burn_by_equipment([row(D(2026, 9, 1), "X", equipment=None, net=7)]) == [("Other", 7)]


def test_ranges_start_where_the_label_says():
    today = D(2026, 10, 6)
    assert progress.range_start("30", today) == D(2026, 9, 7) and progress.range_start("all", today) is None
    assert progress.range_start("bogus", today) == D(2026, 7, 9)       # falls back to 90 days
    assert [r.name for r in progress.in_range(ROWS, D(2026, 9, 9), D(2026, 9, 22))] == [
        "Back Squat", "Dumbbell Curl", "Plank"]
