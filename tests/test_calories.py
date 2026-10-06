import pytest

from app.workouts import calories, exercise_db
from app.workouts.calories import band_for, estimate

BW = 200.0
KG = BW / 2.2046226218


def per_min(met):
    return met * 3.5 * KG / 200


def test_a_rep_based_estimate_follows_the_workbook_sheet():
    # Bench Press: 4.5 s/rep, 3 min rest per set. 185 lb x 4 sets x 8 reps in the Hypertrophy / General style (MET 3.5).
    burn, missing = estimate(exercise_db.get("Bench Press"), body_weight_lb=BW, sets=4, reps=8, load_lb=185,
                             style="Hypertrophy / General")
    assert missing == []
    assert burn.active_min == pytest.approx(2.4) and burn.rest_min == pytest.approx(9.0)
    assert burn.met == 3.5
    assert burn.gross_kcal == pytest.approx(per_min(3.5) * 2.4 + per_min(1.5) * 9.0)      # 34.77
    assert burn.net_kcal == pytest.approx(per_min(2.5) * 2.4 + per_min(0.5) * 9.0)        # 16.67
    assert burn.volume_lb == 185 * 4 * 8


def test_the_style_chooses_the_met_and_defaults_to_the_exercises_own_style():
    bench = exercise_db.get("Bench Press")                                                 # default style Heavy Strength
    heavy, _ = estimate(bench, body_weight_lb=BW, sets=3, reps=5)
    light, _ = estimate(bench, body_weight_lb=BW, sets=3, reps=5, style="Isolation")
    assert heavy.met == 5.0 and light.met == 3.5 and heavy.gross_kcal > light.gross_kcal


def test_one_set_has_no_rest_and_two_implements_double_the_volume_only():
    curl = exercise_db.get("Dumbbell Curl")
    one, _ = estimate(curl, body_weight_lb=BW, sets=1, reps=10, load_lb=30)
    two, _ = estimate(curl, body_weight_lb=BW, sets=1, reps=10, load_lb=30, implements=2)
    assert one.rest_min == 0 and one.volume_lb == 300 and two.volume_lb == 600
    assert two.gross_kcal == pytest.approx(one.gross_kcal)


def test_missing_inputs_give_no_estimate_and_name_what_is_missing():
    bench = exercise_db.get("Bench Press")
    assert estimate(bench, body_weight_lb=BW, sets=3) == (None, ["reps per set"])
    assert estimate(bench, body_weight_lb=None, sets=3, reps=8)[1] == ["body weight"]
    assert estimate(bench, body_weight_lb=BW)[1] == ["sets", "reps per set"]
    assert estimate(bench, body_weight_lb=BW, sets=0, reps=8)[1] == ["sets"]
    assert estimate(bench, body_weight_lb=-5, sets=3, reps=8)[1] == ["body weight"]


def test_load_is_optional_because_it_only_feeds_volume():
    burn, missing = estimate(exercise_db.get("Push-Up"), body_weight_lb=BW, sets=3, reps=15)
    assert missing == [] and burn.volume_lb is None and burn.gross_kcal > 0


def test_a_duration_exercise_uses_minutes_and_its_own_met():
    burn, missing = estimate(exercise_db.get("Elliptical"), body_weight_lb=BW, minutes=30)
    assert missing == [] and burn.met == 5.0 and burn.rest_min == 0
    assert burn.gross_kcal == pytest.approx(per_min(5.0) * 30) and burn.net_kcal == pytest.approx(per_min(4.0) * 30)
    assert estimate(exercise_db.get("Plank"), body_weight_lb=BW)[1] == ["minutes"]


def test_running_picks_its_met_from_the_compendium_by_speed():
    run = exercise_db.get("Treadmill Run")
    burn, missing = estimate(run, body_weight_lb=180, minutes=30, speed_mph=6.0)
    assert missing == [] and burn.met == 9.3 and burn.code == "12050"
    assert burn.gross_kcal == pytest.approx(9.3 * 3.5 * (180 / 2.2046226218) / 200 * 30)
    assert estimate(run, body_weight_lb=180, minutes=30)[1] == ["speed"]


def test_walking_uses_the_treadmill_walking_rows():
    walk = exercise_db.get("Treadmill Walk")
    burn, _ = estimate(walk, body_weight_lb=180, minutes=20, speed_mph=4.5)
    assert burn.met == 6.8 and burn.code == "17364"
    assert burn.gross_kcal == pytest.approx(6.8 * 3.5 * (180 / 2.2046226218) / 200 * 20)


def test_incline_walking_is_chosen_by_grade_and_needs_at_least_one_percent():
    incline = exercise_db.get("Treadmill Incline Walk")
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=8)[0].met == 7.0
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=15)[0].code == "17036"
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=0.5)[1] == ["incline grade of at least 1%"]
    assert estimate(incline, body_weight_lb=180, minutes=20)[1] == ["incline grade"]


@pytest.mark.parametrize("table,value,met", [
    ("run", 2.0, 3.3), ("run", 4.1, 6.5), ("run", 4.8, 7.8), ("run", 5.3, 8.5), ("run", 6.5, 9.3), ("run", 9.5, 14.8),
    ("run", 20.0, 23.0), ("treadmill_walk", 0.5, 2.1), ("treadmill_walk", 1.1, 2.3), ("treadmill_walk", 2.5, 3.5),
    ("treadmill_walk", 3.49, 3.8), ("treadmill_walk", 5.5, 8.3), ("treadmill_walk", 7.0, 8.3)])
def test_a_speed_falls_in_the_band_that_contains_it(table, value, met):
    assert band_for(table, value).met == met
