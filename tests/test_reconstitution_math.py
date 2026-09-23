import pytest

from app.calculator import reconstitution as calc


def test_cheat_sheet_example():
    # Owner's cheat sheet / PepPal worked example: 10 mg vial, 2 mL BAC, 250 mcg dose.
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=250, dose_unit="mcg", syringe_ml=1.0)
    assert r.concentration_mg_ml == pytest.approx(5.0)
    assert r.concentration_mcg_ml == pytest.approx(5000.0)
    assert r.draw_ml == pytest.approx(0.05)
    assert r.units == pytest.approx(5.0)
    assert r.doses_per_vial == 40
    assert not r.over_capacity and r.problems == []


def test_cheat_sheet_table_row():
    # 20 mg / 1 mL -> 20 mg/mL; drawing a 5 mg dose -> 25 units (matches the printed table's highlighted cell).
    r = calc.compute(vial_mg=20, water_ml=1, dose_value=5, dose_unit="mg", syringe_ml=1.0)
    assert r.concentration_mg_ml == pytest.approx(20.0)
    assert r.units == pytest.approx(25.0)


def test_mg_and_mcg_dose_agree():
    a = calc.compute(vial_mg=10, water_ml=2, dose_value=0.25, dose_unit="mg", syringe_ml=1.0)
    b = calc.compute(vial_mg=10, water_ml=2, dose_value=250, dose_unit="mcg", syringe_ml=1.0)
    assert a.draw_ml == pytest.approx(b.draw_ml) and a.units == pytest.approx(b.units)


@pytest.mark.parametrize("vial,water,dose", [(0, 2, 250), (10, 0, 250), (10, 2, 0), (-5, 2, 250),
                                             (10, -1, 250), (10, 2, -1), (None, 2, 250)])
def test_blank_and_zero_inputs(vial, water, dose):
    r = calc.compute(vial_mg=vial, water_ml=water, dose_value=dose, dose_unit="mg", syringe_ml=1.0)
    assert r.problems and r.draw_ml is None and r.units is None and r.doses_per_vial is None


def test_missing_values_are_blank_not_zero():
    r = calc.compute(vial_mg=None, water_ml=None, dose_value=None, dose_unit="mg", syringe_ml=1.0)
    assert set(r.problems) == {"vial", "water", "dose"}


@pytest.mark.parametrize("syringe_ml,units,expected_over", [
    (0.3, 30.0, False), (0.3, 30.1, True), (0.3, 29.9, False),
    (0.5, 50.0, False), (0.5, 50.1, True),
    (1.0, 100.0, False), (1.0, 100.1, True),
])
def test_syringe_capacity_flag(syringe_ml, units, expected_over):
    # Choose vial/water/dose so units lands exactly on the target (concentration 1 mg/mL, dose_mg = units/100).
    r = calc.compute(vial_mg=100, water_ml=100, dose_value=units / 100, dose_unit="mg", syringe_ml=syringe_ml)
    assert r.units == pytest.approx(units)
    assert r.over_capacity is expected_over


def test_doses_per_vial_floors():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=3, dose_unit="mg", syringe_ml=1.0)
    assert r.doses_per_vial == 3  # 10/3 = 3.33 -> 3


def test_reverse_solver_round_trips():
    for vial_mg, dose_mg, target in [(10, 0.25, 20), (24, 2, 15), (5, 0.5, 12.5), (100, 10, 100)]:
        water = calc.water_for_target_units(vial_mg=vial_mg, dose_mg=dose_mg, target_units=target)
        assert water is not None and water > 0
        r = calc.compute(vial_mg=vial_mg, water_ml=water, dose_value=dose_mg, dose_unit="mg", syringe_ml=1.0)
        assert r.units == pytest.approx(target)


@pytest.mark.parametrize("vial,dose,target", [(0, 1, 20), (10, 0, 20), (10, 1, 0), (-1, 1, 20)])
def test_reverse_solver_invalid_inputs(vial, dose, target):
    assert calc.water_for_target_units(vial_mg=vial, dose_mg=dose, target_units=target) is None


def test_syringe_capacities_table():
    assert calc.SYRINGE_CAPACITIES_UNITS == {0.3: 30, 0.5: 50, 1.0: 100}


def test_unknown_dose_unit_is_a_problem():
    r = calc.compute(vial_mg=10, water_ml=2, dose_value=1, dose_unit="grams", syringe_ml=1.0)
    assert "dose" in r.problems and r.draw_ml is None
