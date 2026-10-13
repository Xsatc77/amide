"""Metrics worked out from numbers already logged: weight trend, fat and lean mass, waist ratios, dose-change markers, lab ratios."""

from datetime import date, timedelta

import pytest

from app.measurements import derived

D = date(2026, 9, 1)


def days(n):
    return D + timedelta(days=n)


def test_the_rolling_average_uses_only_weigh_ins_in_the_trailing_week():
    points = [(days(0), 200.0), (days(2), 198.0), (days(4), 196.0), (days(9), 190.0)]
    avg = dict(derived.rolling_average(points, 7))
    assert avg[days(0)] == 200.0 and avg[days(2)] == 199.0 and avg[days(4)] == 198.0
    assert avg[days(9)] == pytest.approx((196 + 190) / 2)             # day 0 and day 2 are more than 6 days back


def test_two_weigh_ins_on_one_day_count_as_their_mean():
    avg = dict(derived.rolling_average([(days(0), 200.0), (days(0), 202.0)], 7))
    assert avg[days(0)] == 201.0


def test_the_weekly_rate_is_the_change_in_the_average_over_the_week_before():
    avg = [(days(0), 200.0), (days(7), 198.0), (days(14), 195.5)]
    assert derived.weekly_rate(avg) == [(days(7), -2.0), (days(14), -2.5)]


def test_the_weekly_rate_skips_a_day_with_no_average_about_a_week_earlier():
    assert derived.weekly_rate([(days(0), 200.0), (days(3), 199.0)]) == []
    assert derived.weekly_rate([(days(0), 200.0), (days(30), 190.0)]) == []


def test_the_rate_scales_to_a_week_when_the_gap_is_not_exactly_seven_days():
    [(d, rate)] = derived.weekly_rate([(days(0), 200.0), (days(9), 197.0)])
    assert d == days(9) and rate == pytest.approx(-3.0 * 7 / 9, abs=0.01)


def test_fat_and_lean_mass_split_the_weight_by_body_fat_percent():
    assert derived.composition(200.0, 25.0) == (50.0, 150.0)
    assert derived.composition(None, 25.0) is None and derived.composition(200.0, None) is None
    assert derived.composition(200.0, 0) is None and derived.composition(200.0, 100) is None


def test_waist_ratios():
    assert derived.waist_to_height(35, 70) == 0.5
    assert derived.waist_to_hip(34, 40) == 0.85
    assert derived.waist_to_height(None, 70) is None and derived.waist_to_hip(34, 0) is None and derived.waist_to_height(35, None) is None


def test_a_dose_change_marker_appears_when_a_peptide_starts_or_its_dose_changes():
    doses = [(days(0), "Zorvex", 1.0, "mg"), (days(7), "Zorvex", 1.0, "mg"), (days(14), "Zorvex", 2.0, "mg"),
             (days(14), "Quillamine", 5.0, "mcg"), (days(21), "Zorvex", 2.0, "mg")]
    marks = derived.dose_markers(doses)
    assert [(m["date"], m["label"]) for m in marks] == [
        (days(0), "Zorvex 1 mg (start)"), (days(14), "Quillamine 5 mcg (start)"), (days(14), "Zorvex 2 mg")]


def test_dose_markers_ignore_rows_without_a_dose_value():
    assert derived.dose_markers([(days(0), "Zorvex", None, "mg")]) == []


def test_lab_ratios():
    assert derived.homa_ir(insulin=10.0, glucose=90.0) == pytest.approx(2.22, abs=0.01)
    assert derived.tg_hdl(150, 50) == 3.0
    assert derived.non_hdl(200, 55) == 145
    assert derived.ldl_hdl(100, 50) == 2.0
    for fn, args in ((derived.homa_ir, (None, 90.0)), (derived.tg_hdl, (150, 0)), (derived.non_hdl, (200, None)), (derived.ldl_hdl, (100, None))):
        assert fn(*args) is None
