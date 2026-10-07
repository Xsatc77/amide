"""Calories come from the nearest exercise when a name is not an exact or confident match."""

import pytest

from app.workouts.exercise_match import approximate_exercise, match_exercise


@pytest.mark.parametrize("typed,expected", [
    ("Exercise Ball Crunches", "Crunch"),
    ("Oblique Crunches", "Crunch"),
    ("Lat Pull Down", "Cable Lat Pulldown"),
    ("Lying Leg Raise", "Leg Raise"),
    ("Leg Curl", "Cable Leg Curl"),
    ("Goblet Squat", "Dumbbell Goblet Squat"),
])
def test_names_that_were_unmatched_now_find_a_close_exercise(typed, expected):
    assert approximate_exercise(typed).name == expected


def test_a_confident_match_is_unchanged():
    assert approximate_exercise("Dumbbell Shrugs").name == match_exercise("Dumbbell Shrugs").exercise.name


def test_nonsense_still_finds_nothing():
    assert approximate_exercise("Zzz Qwerty") is None and approximate_exercise("") is None


def test_the_strict_matcher_still_does_not_guess():
    assert match_exercise("Oblique Crunches").exercise is None
