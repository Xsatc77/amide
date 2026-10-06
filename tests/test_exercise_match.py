import pytest

from app.workouts.exercise_match import match_exercise


@pytest.mark.parametrize("typed,how,name", [
    ("Bench Press", "exact", "Bench Press"),
    ("bench press", "exact", "Bench Press"),
    ("DB Incline Press", "alias", "Dumbbell Incline Press"),
    ("Incline DB Press", "normalized", "Dumbbell Incline Press"),
    ("Barbell Bench Press", "normalized", "Bench Press"),
    ("Back Squats", "normalized", "Back Squat"),
    ("Pushups", "normalized", "Push-Up"),
    ("Pull-ups", "normalized", "Pull-Up"),
    ("RDL", "normalized", "Romanian Deadlift"),
    ("Walking Lunges", "normalized", "Walking Lunge"),
    ("Machine Chest Press", "normalized", "Chest Press Machine"),
    ("Running", "alias", "Treadmill Run"),
    ("Military Press", "alias", "Overhead Press"),
    ("Leg Extension", "fuzzy", "Leg Extension Machine"),
])
def test_names_people_actually_write_land_on_the_right_exercise(typed, how, name):
    match = match_exercise(typed)
    assert (match.how, match.exercise.name) == (how, name)
    assert match.confident


def test_candidates_that_burn_the_same_are_interchangeable():
    # Cable and machine lat pulldowns share one calorie profile, so "Lat Pulldown" is accepted.
    assert match_exercise("Lat Pulldown").exercise.name in {"Cable Lat Pulldown", "Lat Pulldown Machine"}


@pytest.mark.parametrize("typed", ["Squats", "Lunges", "Treadmill", "Calf Raises", "Bicep Curl"])
def test_a_generic_name_with_different_candidates_is_a_suggestion_not_a_guess(typed):
    match = match_exercise(typed)
    assert not match.confident and match.exercise is None
    assert 1 <= len(match.suggestions) <= 3


@pytest.mark.parametrize("typed", ["", "   ", None, "Zzz Quasar Lift", "Crunches Of The Gods 9000"])
def test_nonsense_matches_nothing_and_suggests_nothing(typed):
    match = match_exercise(typed)
    assert match.exercise is None and match.suggestions == () and match.how == "none"


def test_equipment_words_keep_different_exercises_apart():
    assert match_exercise("Cable Curl").exercise.name == "Cable Curl"
    assert match_exercise("Barbell Biceps Curl").exercise.name == "Barbell Curl"
    assert match_exercise("Dumbbell Bench Press").exercise.name == "Dumbbell Bench Press"


def test_the_match_is_stable_and_cheap_to_repeat():
    first = match_exercise("Hammer Curls")
    assert all(match_exercise("Hammer Curls") == first for _ in range(50))
