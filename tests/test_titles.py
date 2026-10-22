"""title_case: every word capitalised, units in parentheses left alone."""

import pytest

from app.titles import title_case


@pytest.mark.parametrize("text, expected", [
    ("Walking and running distance (mi)", "Walking And Running Distance (mi)"), ("Flights climbed", "Flights Climbed"), ("Weight (lbs)", "Weight (lbs)"),
    ("Today's entry", "Today's Entry"), ("two-factor code", "Two-Factor Code"), ("Dosing (community)", "Dosing (Community)"), ("Vial size (optional)", "Vial Size (Optional)"),
    ("Already Title Case", "Already Title Case"), ("Scale: body fat %", "Scale: Body Fat %"), ("Heart rate (bpm)", "Heart Rate (bpm)"), ("", ""), (None, ""),
    ("Walking speed (mi/hr)", "Walking Speed (mi/hr)"), ("Resting energy (kcal)", "Resting Energy (kcal)"),
])
def test_title_case(text, expected):
    assert title_case(text) == expected
