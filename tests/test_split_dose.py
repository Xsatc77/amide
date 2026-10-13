"""Split dosing: a total over a period divided into equal injections, and the schedule that goes with it (free to be any period in days or weeks)."""

from pathlib import Path

import pytest

from app.protocols.split import SplitError, split_dose


def test_a_weekly_total_split_in_two_and_three():
    two = split_dose(5, 7, "days", 2)
    assert two["dose"] == 2.5 and two["frequency"] == "weekdays" and two["weekdays"] == "MR" and two["per_day"] == 1
    three = split_dose(6, 1, "weeks", 3)
    assert three["dose"] == 2 and three["weekdays"] == "MWF"
    assert three["total"] == 6 and three["period_days"] == 7 and three["injections"] == 3


def test_every_even_split_of_a_week_picks_that_many_distinct_days():
    for k in range(2, 7):
        plan = split_dose(7, 7, "days", k)
        assert len(plan["weekdays"]) == k and len(set(plan["weekdays"])) == k and plan["dose"] == pytest.approx(7 / k, abs=1e-4)
    assert split_dose(7, 7, "days", 7)["frequency"] == "daily"
    assert split_dose(5, 7, "days", 1)["frequency"] == "weekly" and split_dose(5, 7, "days", 1)["dose"] == 5


def test_longer_periods_use_every_n_days_when_they_divide_evenly():
    plan = split_dose(10, 2, "weeks", 2)
    assert plan["frequency"] == "every_n_days" and plan["every_n_days"] == 7 and plan["dose"] == 5
    assert split_dose(10, 14, "days", 7)["frequency"] == "eod"                     # every other day
    with pytest.raises(SplitError) as err:
        split_dose(10, 14, "days", 3)
    assert "evenly" in str(err.value)


def test_several_injections_in_one_day_become_time_of_day_slots():
    plan = split_dose(1, 1, "days", 2)
    assert plan["frequency"] == "daily" and plan["per_day"] == 2 and plan["times"] == ["am", "pm"] and plan["dose"] == 0.5
    assert split_dose(3, 1, "days", 3)["times"] == ["am", "pm", "before_bed"]
    assert split_dose(1, 1, "days", 1)["per_day"] == 1 and split_dose(1, 1, "days", 1)["times"] == ["any"]


def test_doses_are_rounded_to_a_usable_precision():
    assert split_dose(1, 7, "days", 3)["dose"] == pytest.approx(0.3333, abs=1e-4)


@pytest.mark.parametrize("args", [
    (0, 7, "days", 2), (-1, 7, "days", 2), ("x", 7, "days", 2), (5, 0, "days", 2), (5, 7, "months", 2), (5, 7, "days", 0),
    (5, 7, "days", 2.5), (5, 7, "days", 8), (5, 61, "days", 2), (5, 1, "days", 9), (float("inf"), 7, "days", 2),
])
def test_bad_numbers_are_refused_with_a_reason(args):
    with pytest.raises(SplitError):
        split_dose(*args)


def test_the_endpoint_returns_the_plan_and_422_with_a_reason(client):
    ok = client.get("/protocols/split-dose", params={"total": "5", "period": "7", "period_unit": "days", "injections": "2"})
    assert ok.status_code == 200 and ok.json()["dose"] == 2.5 and ok.json()["weekdays"] == "MR"
    bad = client.get("/protocols/split-dose", params={"total": "5", "period": "14", "period_unit": "days", "injections": "3"})
    assert bad.status_code == 422 and "evenly" in bad.json()["detail"]


def test_the_builder_script_has_the_split_helper():
    js = (Path(__file__).parent.parent / "app" / "static" / "js" / "protocol-builder.js").read_text(encoding="utf-8")
    assert "Split a dose" in js and "/protocols/split-dose" in js
