"""The "why" behind a reconstitution result: the steps with the user's own numbers, what makes a draw unusable, the water
amounts that would work, and how to split a dose that cannot fit one syringe. Pure functions; the page only displays this."""

import math

from app.calculator import units as unit_math
from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS, _positive

MIN_UNITS = 5                                   # below this a draw is too small to measure on a U-100 syringe
ROUND_MARKS = (5, 10, 15, 20, 25, 30, 40, 50, 75, 100)


def _g(value: float, digits: int = 4) -> str:
    return f"{float(f'{value:.{digits}g}'):g}"


def explain(*, vial, vial_unit: str, water_ml, dose_value, dose_unit: str, syringe_ml: float, iu_per_mg=None) -> dict:
    out = {"verdict": "incomplete", "steps": [], "issues": [], "viable_water": None, "suggestions": [], "split": None}
    vial_v, water, dose = _positive(vial), _positive(water_ml), _positive(dose_value)
    if vial_v is None or water is None or dose is None:
        return out
    vial_unit = unit_math._canon(vial_unit) or "mg"
    dose_unit_c = unit_math._canon(dose_unit) or "mg"
    capacity = SYRINGE_CAPACITIES_UNITS.get(syringe_ml, 100)
    dose_in_vial = unit_math.convert(dose, dose_unit_c, vial_unit, iu_per_mg)
    if dose_in_vial is None:
        mass, other = ("a mass", "IU") if vial_unit == "IU" else ("an IU", vial_unit)
        out["verdict"] = "needs_conversion"
        out["issues"].append({"kind": "needs_conversion", "message": (
            f"The vial is in {vial_unit} but the dose is in {dose_unit_c}. Enter the IU per mg for this product so the dose can be "
            f"converted (for HGH it is about 3 IU per mg).")})
        return out

    if "IU" in (dose_unit_c, vial_unit) and dose_unit_c != vial_unit:                  # mass against mass needs no step of its own
        mg_dose = unit_math.convert(dose, dose_unit_c, "mg", iu_per_mg)
        out["steps"].append({"title": "Convert the dose", "text": (
            f"The vial is measured in {vial_unit}, so the dose has to be too: {_g(dose)} {dose_unit_c}"
            + (f" = {_g(mg_dose)} mg" if dose_unit_c != "mg" and mg_dose is not None and vial_unit == "IU" else "")
            + f" = {_g(dose_in_vial)} {vial_unit} (at {_g(float(iu_per_mg))} IU per mg).")})

    concentration = vial_v / water
    draw_ml = dose_in_vial / concentration
    units_ = draw_ml * 100
    out["steps"] += [
        {"title": "Concentration", "text": f"{_g(vial_v)} {vial_unit} ÷ {_g(water)} mL = {_g(concentration)} {vial_unit}/mL"},
        {"title": "Draw volume", "text": f"{_g(dose_in_vial)} {vial_unit} ÷ {_g(concentration)} {vial_unit}/mL = {_g(draw_ml)} mL"},
        {"title": "Syringe units", "text": f"{_g(draw_ml)} mL × 100 = {_g(units_)} units on a U-100 syringe (a {_g(syringe_ml)} mL syringe holds {capacity})"},
    ]

    verdict = "ok"
    if dose_in_vial > vial_v + 1e-9:
        verdict = "impossible"
        out["issues"].append({"kind": "dose_exceeds_vial", "message": (
            f"One dose ({_g(dose_in_vial)} {vial_unit}) is bigger than the whole vial ({_g(vial_v)} {vial_unit}). No amount of water fixes "
            f"that: use a larger vial, or combine more than one vial for a dose.")})
    else:
        low = (MIN_UNITS / 100) * vial_v / dose_in_vial
        high = (capacity / 100) * vial_v / dose_in_vial
        out["viable_water"] = {"low": low, "high": high}
        out["suggestions"] = [{"units": u, "water_ml": (u / 100) * vial_v / dose_in_vial} for u in ROUND_MARKS if MIN_UNITS <= u <= capacity]
        if units_ > capacity + 1e-9:
            verdict = "over"
            out["issues"].append({"kind": "over_capacity", "message": (
                f"The draw is {_g(draw_ml)} mL ({_g(units_)} units), but a {_g(syringe_ml)} mL syringe holds {capacity} units. The mix is too "
                f"dilute for this dose: add no more than {_g(high)} mL of water, or split the dose.")})
            n = math.ceil(units_ / capacity - 1e-9)
            out["split"] = {"injections": n, "units_each": units_ / n}
        elif units_ < MIN_UNITS - 1e-9:
            verdict = "small"
            out["issues"].append({"kind": "too_small", "message": (
                f"The draw is only {_g(units_)} units ({_g(draw_ml)} mL), too small to measure reliably. The mix is too concentrated for "
                f"this dose: add at least {_g(low)} mL of water.")})
        if high < 1.0 and verdict == "ok":
            out["issues"].append({"kind": "little_water", "message": (
                f"For this dose the water has to stay under {_g(high)} mL. That is a small amount to add and mix, so a smaller dose per "
                f"vial or a larger vial would be easier.")})
    out["verdict"] = verdict
    return out
