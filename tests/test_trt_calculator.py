"""The TRT calculator: weekly dose and vial strength in, the draw per injection (mL and U-100 syringe units) out. Arithmetic only, never a recommendation."""

import pytest

from app.calculator.trt import FREQUENCIES, compute_trt
from app.models import Peptide, PeptideSource


def test_two_injections_a_week_split_the_weekly_dose_and_convert_to_units():
    r = compute_trt(250, 140, "twice_weekly")
    assert r.injections_per_week == 2 and r.mg_per_injection == 70 and r.ml_per_injection == pytest.approx(0.28) and r.units == pytest.approx(28)
    assert r.units_whole == 28 and r.mg_at_whole_units == pytest.approx(70) and not r.over_capacity and not r.problems


@pytest.mark.parametrize("freq, per_week, mg, units", [
    ("weekly", 1, 200, 80), ("twice_weekly", 2, 100, 40), ("eod", 3.5, 200 / 3.5, 200 / 3.5 / 250 * 100), ("daily", 7, 200 / 7, 200 / 7 / 250 * 100),
])
def test_every_schedule(freq, per_week, mg, units):
    r = compute_trt(250, 200, freq)
    assert r.injections_per_week == per_week and r.mg_per_injection == pytest.approx(mg) and r.units == pytest.approx(units)


def test_the_units_to_pull_are_rounded_to_a_mark_and_say_what_that_mark_delivers():
    r = compute_trt(250, 200, "daily")                       # 28.57 mg a day = 11.43 units
    assert r.units == pytest.approx(11.428, abs=0.001) and r.units_whole == 11 and r.mg_at_whole_units == pytest.approx(27.5)


def test_a_draw_bigger_than_the_syringe_is_flagged():
    big = compute_trt(100, 200, "weekly", syringe_ml=1.0)                                # 2 mL is 200 units: more than a 1 mL syringe holds
    assert big.units == 200 and big.over_capacity is True
    assert compute_trt(250, 200, "weekly", syringe_ml=1.0).over_capacity is False        # 80 units fit in 100
    assert compute_trt(250, 200, "weekly", syringe_ml=0.5).over_capacity is True         # the 0.5 mL syringe holds 50


def test_how_long_a_vial_lasts():
    r = compute_trt(250, 200, "weekly", vial_ml=10)                                      # 0.8 mL a week from 10 mL
    assert r.weekly_ml == pytest.approx(0.8) and r.vial_days == pytest.approx(87.5)
    assert compute_trt(250, 200, "weekly").vial_days is None


@pytest.mark.parametrize("conc, weekly, freq, problem", [
    (None, 200, "weekly", "concentration"), (0, 200, "weekly", "concentration"), (250, None, "weekly", "weekly dose"), (250, -5, "weekly", "weekly dose"),
    (250, 200, "hourly", "frequency"), (250, 200, None, "frequency"), (float("nan"), 200, "weekly", "concentration"),
])
def test_missing_or_bad_inputs_give_a_problem_and_no_numbers(conc, weekly, freq, problem):
    r = compute_trt(conc, weekly, freq)
    assert problem in r.problems and r.units is None and r.ml_per_injection is None


def test_the_frequency_list_has_four_schedules():
    assert list(FREQUENCIES) == ["weekly", "twice_weekly", "eod", "daily"]


def test_the_api_returns_the_draw(client):
    data = client.get("/api/calculator/trt", params={"conc": "250", "weekly": "200", "freq": "twice_weekly"}).json()
    assert data["mg_per_injection"] == 100 and data["ml_per_injection"] == pytest.approx(0.4) and data["units"] == pytest.approx(40)


def test_the_api_with_nothing_entered_says_what_is_missing(client):
    data = client.get("/api/calculator/trt").json()
    assert data["units"] is None and "weekly dose" in data["problems"]


def test_the_page_prefills_from_the_link_and_says_it_is_not_a_recommendation(client):
    page = client.get("/calculator/trt", params={"conc": "250", "weekly": "200", "freq": "weekly"}).text
    assert 'id="trt-data"' in page and '"conc": "250"' in page and '"weekly": "200"' in page
    assert "not a dose recommendation" in page.lower() or "does not recommend a dose" in page.lower()


def test_the_calculator_page_links_to_it(client):
    assert 'href="/calculator/trt"' in client.get("/calculator").text


def test_a_testosterone_card_links_to_the_trt_calculator_and_other_cards_do_not(client, db):
    testo = Peptide(name="Zephyr Testosterone Enanthate", source=PeptideSource.CUSTOM)
    other = Peptide(name="Zephyr Other", source=PeptideSource.CUSTOM)
    db.add_all([testo, other])
    db.commit()
    assert "/calculator/trt" in client.get(f"/library/{testo.id}").text
    assert "/calculator/trt" not in client.get(f"/library/{other.id}").text
