"""Testosterone (TRT) calculator math: a weekly dose, the vial's strength and how often it is injected -> the draw per injection in mL and U-100 syringe units.

Pure arithmetic, like reconstitution.py, and the single source of truth: the page's JavaScript only calls the API and shows what comes back. The ester
(cypionate, enanthate) does not change the sums, only the strength printed on the vial does. It is volume arithmetic, never a dose recommendation.
"""

import math
from dataclasses import dataclass, field

from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS

# key -> (injections per week, label). "Every other day" averages 3.5 a week.
FREQUENCIES = {
    "weekly": (1.0, "Once weekly"),
    "twice_weekly": (2.0, "Twice weekly"),
    "eod": (3.5, "Every other day"),
    "daily": (7.0, "Daily"),
}
MAX_CONCENTRATION_MG_ML = 1000.0
MAX_WEEKLY_MG = 5000.0


@dataclass
class TrtResult:
    injections_per_week: float | None = None
    mg_per_injection: float | None = None
    ml_per_injection: float | None = None
    units: float | None = None                    # U-100 units: 100 per mL
    units_whole: int | None = None                # the nearest whole mark on the syringe
    mg_at_whole_units: float | None = None        # what that whole mark actually delivers
    weekly_ml: float | None = None
    vial_days: float | None = None                # how long a vial of `vial_ml` lasts at this dose
    over_capacity: bool = False
    problems: list[str] = field(default_factory=list)


def _positive(value, ceiling: float) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 < value <= ceiling else None


def compute_trt(conc_mg_ml, weekly_mg, frequency, syringe_ml: float = 1.0, vial_ml=None) -> TrtResult:
    result = TrtResult()
    conc = _positive(conc_mg_ml, MAX_CONCENTRATION_MG_ML)
    weekly = _positive(weekly_mg, MAX_WEEKLY_MG)
    if conc is None:
        result.problems.append("concentration")
    if weekly is None:
        result.problems.append("weekly dose")
    if frequency not in FREQUENCIES:
        result.problems.append("frequency")
    if result.problems:
        return result
    per_week = FREQUENCIES[frequency][0]
    result.injections_per_week = per_week
    result.mg_per_injection = weekly / per_week
    result.ml_per_injection = result.mg_per_injection / conc
    result.units = result.ml_per_injection * 100
    result.units_whole = round(result.units)
    result.mg_at_whole_units = result.units_whole / 100 * conc
    result.weekly_ml = weekly / conc
    vial = _positive(vial_ml, 1000.0)
    if vial is not None:
        result.vial_days = vial / result.weekly_ml * 7
    result.over_capacity = result.units > SYRINGE_CAPACITIES_UNITS.get(syringe_ml, 100)
    return result
