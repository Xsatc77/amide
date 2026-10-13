"""Split dosing: a total amount over a period divided into equal injections, with the schedule that goes with it.

The person decides the total, the period (days or weeks) and the number of injections; nothing here knows or suggests what is right for a
given peptide. It only does the division and spreads the injections as evenly as the calendar allows, the way the titration ramp only does
its arithmetic. Pure functions: no database, no web."""

import math

MAX_PERIOD_DAYS = 60
MAX_PER_DAY = 4
_TIMES = {1: ["any"], 2: ["am", "pm"], 3: ["am", "pm", "before_bed"], 4: ["waking", "am", "pm", "before_bed"]}
_LETTERS = "MTWRFSU"


class SplitError(ValueError):
    """The numbers cannot be split; the message says why."""


def _positive(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SplitError(f"Enter the {label} as a number.") from None
    if not math.isfinite(number) or number <= 0:
        raise SplitError(f"The {label} must be more than zero.")
    return number


def _whole(value, label: str) -> int:
    number = _positive(value, label)
    if number != int(number):
        raise SplitError(f"The {label} must be a whole number: you cannot give half an injection.")
    return int(number)


def split_dose(total, period, period_unit, injections) -> dict:
    """The per-injection dose and the schedule for `total` over `period` days or weeks in `injections` equal injections."""
    total = _positive(total, "total dose")
    if period_unit not in ("days", "weeks"):
        raise SplitError("Choose days or weeks for the period.")
    period_days = _whole(period, "period") * (7 if period_unit == "weeks" else 1)
    count = _whole(injections, "number of injections")
    if period_days > MAX_PERIOD_DAYS:
        raise SplitError(f"The period can be at most {MAX_PERIOD_DAYS} days.")
    plan = {"total": total, "period_days": period_days, "injections": count, "dose": round(total / count, 4), "weekdays": "",
            "every_n_days": None, "per_day": 1, "times": ["any"]}
    if period_days == 1:
        if count > MAX_PER_DAY:
            raise SplitError(f"Up to {MAX_PER_DAY} injections in one day can be set up here.")
        plan.update(frequency="daily", per_day=count, times=_TIMES[count])
    elif count > period_days:
        raise SplitError(f"{count} injections do not fit in {period_days} days: at most one a day.")
    elif count == period_days:
        plan["frequency"] = "daily"
    elif period_days == 7:
        if count == 1:
            plan["frequency"] = "weekly"
        else:
            plan.update(frequency="weekdays", weekdays="".join(_LETTERS[(i * 7) // count] for i in range(count)))
    elif period_days % count == 0:
        interval = period_days // count
        plan.update(frequency="eod" if interval == 2 else "every_n_days", every_n_days=None if interval == 2 else interval)
    else:
        raise SplitError(f"{count} injections do not divide {period_days} days evenly. Pick a number that does "
                         f"(for example {', '.join(str(n) for n in range(2, period_days) if period_days % n == 0 and n != 1)[:60] or '1'}), "
                         "or set the days yourself.")
    each = f"{plan['dose']:g}"
    plan["note"] = f"{each} per injection, {count} per {period_days} day{'s' if period_days != 1 else ''} ({total:g} in all)."
    if plan["frequency"] == "weekdays" and 7 % count:
        plan["note"] += " The days are as evenly spread as a week allows."
    return plan
