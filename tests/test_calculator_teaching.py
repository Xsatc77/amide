"""Units (mg, mcg, IU), the IU-aware calculator math, the "why" explanations, and parsing a library dose like "500mcg"."""

import pytest

from app.calculator import reconstitution as calc
from app.calculator import teaching, units


# ---------------------------------------------------------------- units

@pytest.mark.parametrize("amount,src,dst,iu,expected", [
    (250, "mcg", "mg", None, 0.25), (0.25, "mg", "mcg", None, 250), (5, "mg", "mg", None, 5), (3, "IU", "IU", None, 3),
    (1, "mg", "IU", 3, 3), (500, "mcg", "IU", 3, 1.5), (3, "IU", "mg", 3, 1), (3, "IU", "mcg", 3, 1000),
])
def test_units_convert_between_mass_and_iu_with_a_factor(amount, src, dst, iu, expected):
    assert units.convert(amount, src, dst, iu) == pytest.approx(expected)


@pytest.mark.parametrize("src,dst,iu", [("mg", "IU", None), ("IU", "mcg", 0), ("IU", "mg", -1), ("mg", "furlongs", 3), ("IU", "mg", float("nan"))])
def test_a_conversion_that_cannot_be_made_is_none(src, dst, iu):
    assert units.convert(1, src, dst, iu) is None


def test_hgh_type_peptides_get_a_default_iu_per_mg_and_others_do_not():
    assert units.default_iu_per_mg("HGH191AA") == 3.0 and units.default_iu_per_mg("HGH (Somatropin)") == 3.0
    assert units.default_iu_per_mg("Retatrutide") is None and units.default_iu_per_mg(None) is None


@pytest.mark.parametrize("text,expected", [("4mg", (4.0, "mg")), ("500mcg", (500.0, "mcg")), ("3 IU", (3.0, "IU")), ("0.03 IU", (0.03, "IU")),
                                           ("250 µg", (250.0, "mcg")), ("2.4MG", (2.4, "mg")), ("750 mcg/kg", None), ("1mL", None), ("", None), (None, None)])
def test_a_library_dose_is_parsed_into_an_amount_and_a_unit(text, expected):
    assert units.parse_dose_text(text) == expected


# ---------------------------------------------------------------- the math with IU

def test_an_iu_vial_with_an_iu_dose_needs_no_conversion():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=2, dose_unit="IU", syringe_ml=1.0, vial_unit="IU")
    assert r.problems == [] and r.concentration == pytest.approx(5.0) and r.concentration_unit == "IU"
    assert r.draw_ml == pytest.approx(0.4) and r.units == pytest.approx(40.0) and r.doses_per_vial == 5


def test_an_iu_vial_with_a_mass_dose_converts_through_iu_per_mg():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=500, dose_unit="mcg", syringe_ml=1.0, vial_unit="IU", iu_per_mg=3)
    assert r.dose_in_vial_unit == pytest.approx(1.5) and r.draw_ml == pytest.approx(0.3) and r.units == pytest.approx(30.0)
    assert r.doses_per_vial == 6 and "500 mcg" in r.conversion_note and "1.5 IU" in r.conversion_note


def test_a_mass_dose_on_an_iu_vial_without_a_factor_names_what_is_missing():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=500, dose_unit="mcg", syringe_ml=1.0, vial_unit="IU")
    assert r.problems == ["iu_per_mg"] and r.draw_ml is None


def test_an_iu_dose_on_a_mass_vial_also_needs_the_factor():
    assert calc.compute(vial_mg=10, water_ml=2, dose_value=3, dose_unit="IU", syringe_ml=1.0).problems == ["iu_per_mg"]
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=3, dose_unit="IU", syringe_ml=1.0, iu_per_mg=3)
    assert r.dose_in_vial_unit == pytest.approx(1.0) and r.units == pytest.approx(20.0)


def test_an_mcg_vial_is_measured_in_mcg():
    r = calc.compute(vial_mg=500, water_ml=1, dose_value=250, dose_unit="mcg", syringe_ml=1.0, vial_unit="mcg")
    assert r.concentration == pytest.approx(500.0) and r.concentration_unit == "mcg" and r.units == pytest.approx(50.0) and r.doses_per_vial == 2


def test_the_mass_vial_results_are_unchanged():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=250, dose_unit="mcg", syringe_ml=1.0)
    assert (r.concentration_mg_ml, r.concentration_mcg_ml, r.units) == (pytest.approx(5.0), pytest.approx(5000.0), pytest.approx(5.0))
    assert r.concentration_unit == "mg" and r.conversion_note is None


# ---------------------------------------------------------------- the teaching explanations

def teach(**kw):
    base = dict(vial=10, vial_unit="mg", water_ml=2, dose_value=250, dose_unit="mcg", syringe_ml=1.0, iu_per_mg=None)
    return teaching.explain(**{**base, **kw})


def test_a_comfortable_dose_is_fine_and_shows_the_three_steps_with_the_numbers():
    t = teach()
    assert t["verdict"] == "ok" and t["issues"] == []
    assert [s["title"] for s in t["steps"]] == ["Concentration", "Draw volume", "Syringe units"]
    assert "10 mg ÷ 2 mL = 5 mg/mL" in t["steps"][0]["text"] and "0.05 mL" in t["steps"][1]["text"] and "5 units" in t["steps"][2]["text"]


def test_a_draw_over_the_syringe_is_explained_with_a_water_range_and_a_split():
    t = teach(water_ml=10, dose_value=4, dose_unit="mg")                      # 10 mg in 10 mL, 4 mg = 4 mL = 400 units
    assert t["verdict"] == "over"
    issue = t["issues"][0]
    assert issue["kind"] == "over_capacity" and "4 mL" in issue["message"] and "100 units" in issue["message"]
    assert t["viable_water"]["high"] == pytest.approx(2.5) and t["viable_water"]["low"] == pytest.approx(0.125)
    assert t["split"] == {"injections": 4, "units_each": pytest.approx(100.0)}


def test_the_suggested_waters_land_on_round_unit_marks_inside_the_range():
    t = teach(water_ml=10, dose_value=4, dose_unit="mg")
    marks = {s["units"]: s["water_ml"] for s in t["suggestions"]}
    assert marks[100] == pytest.approx(2.5) and marks[50] == pytest.approx(1.25) and marks[10] == pytest.approx(0.25)
    assert all(5 <= units_ <= 100 for units_ in marks)


def test_a_draw_too_small_to_measure_is_explained():
    t = teach(water_ml=0.2, dose_value=250, dose_unit="mcg")                  # 0.2 mL water: 0.005 mL = 0.5 units
    assert t["verdict"] == "small" and t["issues"][0]["kind"] == "too_small" and "0.5 units" in t["issues"][0]["message"]
    assert t["viable_water"]["low"] == pytest.approx(2.0)                 # 5 units needs 2 mL of water for 250 mcg in a 10 mg vial


def test_a_dose_bigger_than_the_vial_is_called_out():
    t = teach(vial=5, dose_value=8, dose_unit="mg", water_ml=2)
    assert t["verdict"] == "impossible" and t["issues"][0]["kind"] == "dose_exceeds_vial"
    assert "bigger than the whole vial" in t["issues"][0]["message"]


def test_a_missing_conversion_factor_gets_its_own_explanation():
    t = teach(vial=10, vial_unit="IU", dose_value=500, dose_unit="mcg")
    assert t["verdict"] == "needs_conversion" and t["issues"][0]["kind"] == "needs_conversion" and "IU per mg" in t["issues"][0]["message"]


def test_the_iu_conversion_is_a_step_of_its_own():
    t = teach(vial=10, vial_unit="IU", dose_value=500, dose_unit="mcg", iu_per_mg=3, water_ml=2)
    assert [s["title"] for s in t["steps"]][0] == "Convert the dose" and "1.5 IU" in t["steps"][0]["text"]
    assert t["verdict"] == "ok" and "IU/mL" in t["steps"][1]["text"]


def test_a_very_small_water_range_is_flagged_as_hard_to_do():
    t = teach(vial=2, dose_value=2, dose_unit="mg", water_ml=0.3)               # draw = 0.3 mL = 30 units fits, but the whole range is under 1 mL
    assert t["viable_water"]["high"] == pytest.approx(1.0)
    t2 = teach(vial=1, dose_value=4, dose_unit="mg", water_ml=1)               # dose 4x the vial: impossible, no range
    assert t2["viable_water"] is None


def test_an_empty_or_invalid_input_returns_a_quiet_explanation_not_an_error():
    t = teach(water_ml=None)
    assert t["verdict"] == "incomplete" and t["steps"] == []
    assert teach(dose_value=0)["verdict"] == "incomplete"


def test_the_smaller_syringes_have_smaller_capacity_in_the_explanation():
    t = teach(water_ml=2, dose_value=1, dose_unit="mg", syringe_ml=0.3)       # draw 0.2 mL = 20 units, fits a 0.3 mL (30 unit) syringe
    assert t["verdict"] == "ok"
    t2 = teach(water_ml=4, dose_value=1, dose_unit="mg", syringe_ml=0.3)      # 0.4 mL = 40 units: over 30
    assert t2["verdict"] == "over" and "30 units" in t2["issues"][0]["message"]
