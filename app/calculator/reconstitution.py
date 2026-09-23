"""Reconstitution math: vial + BAC water + dose -> concentration, draw volume, U-100 syringe units.

Pure functions, no database access. This is the single source of truth for the math; the calculator
page's JavaScript never recomputes it, only calls the API and renders whatever this module returns.

U-100 syringes: 100 units always equals 1 mL, regardless of the syringe's barrel size. Barrel size only
sets how many units fit (its capacity) and how finely it's marked.
"""

import math
from dataclasses import dataclass, field

# Syringe barrel size (mL) -> capacity in U-100 units.
SYRINGE_CAPACITIES_UNITS = {0.3: 30, 0.5: 50, 1.0: 100}

_DOSE_UNITS_TO_MG = {"mg": 1.0, "mcg": 0.001}


@dataclass
class Result:
    concentration_mg_ml: float | None = None
    concentration_mcg_ml: float | None = None
    draw_ml: float | None = None
    units: float | None = None
    doses_per_vial: int | None = None
    over_capacity: bool = False
    problems: list[str] = field(default_factory=list)


def _positive(value) -> float | None:
    """`value` as a float if it's a real, finite, positive number; otherwise None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 and math.isfinite(value) else None


def compute(vial_mg, water_ml, dose_value, dose_unit: str, syringe_ml: float) -> Result:
    """The forward calculation. Anything missing or invalid is named in `problems` instead of raising."""
    problems: list[str] = []

    vial = _positive(vial_mg)
    if vial is None:
        problems.append("vial")
    water = _positive(water_ml)
    if water is None:
        problems.append("water")

    dose_factor = _DOSE_UNITS_TO_MG.get(dose_unit)
    dose_raw = _positive(dose_value)
    dose_mg = dose_raw * dose_factor if (dose_raw is not None and dose_factor is not None) else None
    if dose_mg is None:
        problems.append("dose")

    if problems:
        return Result(problems=problems)

    concentration = vial / water
    draw_ml = dose_mg / concentration
    units = draw_ml * 100
    capacity = SYRINGE_CAPACITIES_UNITS.get(syringe_ml)

    return Result(
        concentration_mg_ml=concentration,
        concentration_mcg_ml=concentration * 1000,
        draw_ml=draw_ml,
        units=units,
        doses_per_vial=int(vial // dose_mg),
        over_capacity=capacity is not None and units > capacity + 1e-9,
    )


def water_for_target_units(vial_mg, dose_mg, target_units) -> float | None:
    """How much BAC water to add so this vial + dose draws to exactly `target_units`. None if any input
    is invalid (a target of 0 has no positive answer, so it's treated the same as invalid)."""
    vial = _positive(vial_mg)
    dose = _positive(dose_mg)
    target = _positive(target_units)
    if vial is None or dose is None or target is None:
        return None
    return vial * (target / 100) / dose
