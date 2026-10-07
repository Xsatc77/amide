"""A titration ramp: start dose, increase per step, weeks per step and a target become the step rows of the builder."""

import math

MAX_STEPS = 20


def _number(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter the {label} as a number.") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"The {label} must be more than zero.")
    return number


def ramp(start, increase, weeks, target) -> list[dict]:
    """Steps of `weeks` weeks each, from `start` up by `increase` each time, ending with an open-ended step at `target`."""
    start, increase, target = _number(start, "start dose"), _number(increase, "increase"), _number(target, "target dose")
    weeks_f = _number(weeks, "weeks per step")
    if weeks_f != int(weeks_f):
        raise ValueError("Weeks per step must be a whole number.")
    weeks = int(weeks_f)
    if target < start:
        raise ValueError("The target dose cannot be below the start dose.")
    doses = [start]
    while doses[-1] + increase < target - 1e-9:
        doses.append(round(doses[-1] + increase, 4))
        if len(doses) >= MAX_STEPS:
            raise ValueError(f"That would take more than {MAX_STEPS} steps: raise the increase.")
    if target > start:
        doses.append(target)
    steps = []
    for n, dose in enumerate(doses):
        first = n * weeks + 1
        last = None if n == len(doses) - 1 else first + weeks - 1
        steps.append({"start_week": first, "end_week": last, "dose": dose})
    return steps
