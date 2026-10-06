"""Validation for the owner-editable part of a library peptide."""

from collections.abc import Mapping

from app.goals import GOALS_BY_SLUG
from app.models import DoseUnit

TEXT_FIELDS = ("aliases", "dose_low", "dose_mid", "dose_high", "dose_unit", "typical_frequency", "notes",
               "normally_supplied_amount", "normally_supplied_unit")
DOSE_FIELDS = (("dose_low", "Low"), ("dose_mid", "Mid"), ("dose_high", "High"))


def state_from_form(form: Mapping[str, list[str]]) -> dict:
    first = lambda k: ((form.get(k) or [""])[0] or "").strip()  # noqa: E731
    state = {k: first(k) for k in TEXT_FIELDS}
    state["goals"] = [g.strip() for g in form.get("goal", []) if g.strip()]
    return state


def state_from_peptide(p, goals: list[str]) -> dict:
    num = lambda v: "" if v is None else f"{v:g}"  # noqa: E731
    return {
        "aliases": p.aliases or "", "dose_low": num(p.dose_low), "dose_mid": num(p.dose_mid),
        "dose_high": num(p.dose_high), "dose_unit": p.dose_unit.value if p.dose_unit else "",
        "typical_frequency": p.typical_frequency or "", "notes": p.notes or "", "goals": goals,
        "normally_supplied_amount": num(p.normally_supplied_amount),
        "normally_supplied_unit": p.normally_supplied_unit.value if p.normally_supplied_unit else "",
    }


def parse_peptide_form(state: dict) -> tuple[dict, dict[str, str]]:
    """Returns (column values + "goals", field name -> error message)."""
    errors: dict[str, str] = {}
    values: dict = {}

    for key, limit, label in (("aliases", 300, "Aliases"), ("typical_frequency", 100, "Typical frequency")):
        values[key] = state[key] or None
        if values[key] and len(values[key]) > limit:
            errors[key] = f"{label} must be under {limit} characters."
    values["notes"] = state["notes"] or None

    for key, label in DOSE_FIELDS:
        values[key] = None
        if state[key]:
            try:
                values[key] = float(state[key])
            except ValueError:
                errors[key] = f"{label} dose must be a number."
                continue
            if values[key] <= 0:
                errors[key] = f"{label} dose must be greater than 0."

    given = [(k, label, values[k]) for k, label in DOSE_FIELDS if values[k] is not None and k not in errors]
    for (_, a_label, a), (b_key, b_label, b) in zip(given, given[1:]):
        if b < a:
            errors[b_key] = f"{b_label} dose can't be below the {a_label.lower()} dose."

    values["dose_unit"] = None
    if state["dose_unit"]:
        try:
            values["dose_unit"] = DoseUnit(state["dose_unit"])
        except ValueError:
            errors["dose_unit"] = "Pick a unit from the list."
    elif given:
        values["dose_unit"] = DoseUnit.MG

    values["normally_supplied_amount"] = values["normally_supplied_unit"] = None
    if state["normally_supplied_amount"]:
        try:
            amount = float(state["normally_supplied_amount"])
        except ValueError:
            errors["normally_supplied_amount"] = "Vial size must be a number."
        else:
            if amount <= 0:
                errors["normally_supplied_amount"] = "Vial size must be greater than 0."
            else:
                values["normally_supplied_amount"] = amount
                values["normally_supplied_unit"] = DoseUnit.MG
        if state["normally_supplied_unit"]:
            try:
                values["normally_supplied_unit"] = DoseUnit(state["normally_supplied_unit"])
            except ValueError:
                errors["normally_supplied_unit"] = "Pick a unit from the list."

    goals = list(dict.fromkeys(state["goals"]))
    if any(g not in GOALS_BY_SLUG for g in goals):
        errors["goal"] = "Unknown goal."
    values["goals"] = goals
    return values, errors
