"""Builder form <-> state <-> validated values.

The builder page submits flat field names:
  name, start_date, end_date, weeks, notes, titration, goal (repeated)
  items-{i}-peptide_id | items-{i}-new_name, items-{i}-dose, -dose_unit, -frequency, -every_n_days,
  -weekdays (repeated letters), -time_of_day, -route, -inventory_item_id, -notes
  items-{i}-steps-{j}-start_week, -end_week, -dose

"State" is the same data as plain strings, nested; the builder's JavaScript renders from it, so it is
what we send back when re-showing the form (after an error, for edit, or for repeat).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.goals import GOALS_BY_SLUG
from app.models import WEEKDAY_LETTERS, DoseUnit, Frequency, Protocol, Route, TimeOfDay

ITEM_FIELDS = ("peptide_id", "new_name", "dose", "dose_unit", "frequency", "every_n_days",
               "time_of_day", "route", "inventory_item_id", "notes")
STEP_FIELDS = ("start_week", "end_week", "dose")

_ITEM_KEY = re.compile(r"^items-(\d+)-(\w+)$")
_STEP_KEY = re.compile(r"^items-(\d+)-steps-(\d+)-(\w+)$")


@dataclass
class ParsedStep:
    start_week: int
    end_week: int | None
    dose: float


@dataclass
class ParsedItem:
    peptide_id: int | None
    new_name: str | None
    dose: float | None
    dose_unit: DoseUnit
    frequency: Frequency
    every_n_days: int | None
    weekdays: str | None
    time_of_day: TimeOfDay
    route: Route
    inventory_item_id: int | None
    notes: str | None
    steps: list[ParsedStep] = field(default_factory=list)


@dataclass
class ParsedProtocol:
    name: str
    start_date: date | None
    end_date: date | None
    notes: str | None
    titration_enabled: bool
    goals: list[str]
    items: list[ParsedItem]


# ---------------------------------------------------------------- state

def _first(form: Mapping[str, list[str]], key: str) -> str:
    values = form.get(key) or [""]
    return (values[0] or "").strip()


def _num(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def blank_state(goals: list[str], today: date) -> dict:
    return {"name": "", "start_date": today.isoformat(), "end_date": "", "weeks": "", "notes": "",
            "titration": False, "goals": list(goals), "items": []}


def state_from_form(form: Mapping[str, list[str]]) -> dict:
    items: dict[int, dict] = {}
    for key in form:
        if m := _STEP_KEY.match(key):
            i, j, name = int(m[1]), int(m[2]), m[3]
            if name in STEP_FIELDS:
                item = items.setdefault(i, {"steps": {}})
                item["steps"].setdefault(j, {})[name] = _first(form, key)
        elif m := _ITEM_KEY.match(key):
            i, name = int(m[1]), m[2]
            item = items.setdefault(i, {"steps": {}})
            if name == "weekdays":
                item["weekdays"] = "".join(v.strip() for v in form[key])
            elif name in ITEM_FIELDS:
                item[name] = _first(form, key)

    out_items = []
    for i in sorted(items):
        raw = items[i]
        item = {f: raw.get(f, "") for f in ITEM_FIELDS}
        item["weekdays"] = raw.get("weekdays", "")
        item["steps"] = [{f: raw["steps"][j].get(f, "") for f in STEP_FIELDS} for j in sorted(raw["steps"])]
        out_items.append(item)

    return {
        "name": _first(form, "name"),
        "start_date": _first(form, "start_date"),
        "end_date": _first(form, "end_date"),
        "weeks": _first(form, "weeks"),
        "notes": _first(form, "notes"),
        "titration": bool(_first(form, "titration")),
        "goals": [g.strip() for g in form.get("goal", []) if g.strip()],
        "items": out_items,
    }


def state_from_protocol(p: Protocol, *, repeat: bool = False, today: date | None = None) -> dict:
    start, end = p.start_date, p.end_date
    name = p.name
    if repeat:
        today = today or date.today()
        name = f"{p.name} (repeat)"
        end = today + (end - start) if end else None
        start = today
    return {
        "name": name,
        "start_date": start.isoformat(),
        "end_date": end.isoformat() if end else "",
        "weeks": "",
        "notes": p.notes or "",
        "titration": p.titration_enabled,
        "goals": p.goal_slugs,
        "items": [
            {
                "peptide_id": str(it.peptide_id),
                "new_name": "",
                "dose": _num(it.dose),
                "dose_unit": it.dose_unit.value,
                "frequency": it.frequency.value,
                "every_n_days": "" if it.every_n_days is None else str(it.every_n_days),
                "weekdays": it.weekdays or "",
                "time_of_day": it.time_of_day.value,
                "route": it.route.value,
                "inventory_item_id": "" if it.inventory_item_id is None else str(it.inventory_item_id),
                "notes": it.notes or "",
                "steps": [{"start_week": str(s.start_week), "end_week": "" if s.end_week is None else str(s.end_week),
                           "dose": _num(s.dose)} for s in it.steps],
            }
            for it in p.items
        ],
    }


# ---------------------------------------------------------------- parsing

def _parse_date(raw: str, key: str, errors: dict) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[key] = "Enter a valid date."
        return None


def _parse_int(raw: str, key: str, errors: dict, *, minimum: int, label: str) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        errors[key] = f"{label} must be a whole number."
        return None
    if value < minimum:
        errors[key] = f"{label} must be at least {minimum}."
    return value


def _parse_dose(raw: str, key: str, errors: dict) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        errors[key] = "Dose must be a number."
        return None
    if value <= 0:
        errors[key] = "Dose must be greater than 0."
    return value


def _parse_choice(enum_cls, raw: str, default, key: str, errors: dict):
    if not raw:
        return default
    try:
        return enum_cls(raw)
    except ValueError:
        errors[key] = "Pick an option from the list."
        return default


def _parse_steps(item_state: dict, prefix: str, titration: bool, errors: dict) -> list[ParsedStep]:
    """With titration on, every non-blank step is validated. With it off, steps are kept only if valid."""
    parsed: list[tuple[int, ParsedStep]] = []
    for j, raw in enumerate(item_state["steps"]):
        if not any(raw.values()):
            continue
        key = f"{prefix}-steps-{j}"
        step_errors: dict[str, str] = {}
        start = _parse_int(raw["start_week"], f"{key}-start_week", step_errors, minimum=1, label="Start week")
        if start is None and f"{key}-start_week" not in step_errors:
            step_errors[f"{key}-start_week"] = "Start week is required."
        end = _parse_int(raw["end_week"], f"{key}-end_week", step_errors, minimum=1, label="End week")
        if start is not None and end is not None and end < start and f"{key}-end_week" not in step_errors:
            step_errors[f"{key}-end_week"] = "End week can't be before the start week."
        dose = _parse_dose(raw["dose"], f"{key}-dose", step_errors)
        if dose is None and f"{key}-dose" not in step_errors:
            step_errors[f"{key}-dose"] = "Dose is required."
        if step_errors:
            if titration:
                errors.update(step_errors)
            continue
        parsed.append((j, ParsedStep(start, end, dose)))

    parsed.sort(key=lambda pair: pair[1].start_week)
    if titration:
        for (ja, a), (jb, b) in zip(parsed, parsed[1:]):
            if a.end_week is None:
                errors[f"{prefix}-steps-{ja}-end_week"] = "Only the last step can be open-ended."
            elif b.start_week <= a.end_week:
                errors[f"{prefix}-steps-{jb}-start_week"] = "Overlaps the previous step."
    return [step for _, step in parsed]


def parse_protocol_form(form: Mapping[str, list[str]], *, peptide_ids: set[int],
                        inventory_ids: set[int]) -> tuple[ParsedProtocol, dict[str, str]]:
    """Returns (values, field name -> error message)."""
    state = state_from_form(form)
    errors: dict[str, str] = {}

    name = state["name"]
    if not name:
        errors["name"] = "Protocol name is required."
    elif len(name) > 200:
        errors["name"] = "Keep the name under 200 characters."

    goals = list(dict.fromkeys(state["goals"]))
    if not goals:
        errors["goal"] = "Pick at least one goal."
    elif any(g not in GOALS_BY_SLUG for g in goals):
        errors["goal"] = "Unknown goal."

    start = _parse_date(state["start_date"], "start_date", errors)
    if start is None and "start_date" not in errors:
        errors["start_date"] = "Start date is required."
    end = _parse_date(state["end_date"], "end_date", errors)
    weeks = _parse_int(state["weeks"], "weeks", errors, minimum=1, label="Length in weeks")
    if end is None and weeks and start and "weeks" not in errors:
        end = start + timedelta(days=weeks * 7 - 1)
    if start and end and end < start:
        errors["end_date"] = "End date can't be before the start date."

    titration = state["titration"]
    items: list[ParsedItem] = []
    seen_peptides: set[int] = set()
    for i, raw in enumerate(state["items"]):
        key = f"items-{i}"
        peptide_id = new_name = None
        if raw["peptide_id"]:
            try:
                peptide_id = int(raw["peptide_id"])
            except ValueError:
                peptide_id = -1
            if peptide_id not in peptide_ids:
                errors[f"{key}-peptide_id"] = "Unknown peptide."
            elif peptide_id in seen_peptides:
                errors[f"{key}-peptide_id"] = "This peptide is already in the protocol."
            seen_peptides.add(peptide_id)
        elif raw["new_name"]:
            new_name = raw["new_name"]
            if len(new_name) > 120:
                errors[f"{key}-new_name"] = "Keep the peptide name under 120 characters."
        else:
            errors[f"{key}-peptide_id"] = "Pick a peptide."

        frequency = _parse_choice(Frequency, raw["frequency"], Frequency.DAILY, f"{key}-frequency", errors)
        every_n_days = weekdays = None
        if frequency is Frequency.EVERY_N_DAYS:
            every_n_days = _parse_int(raw["every_n_days"], f"{key}-every_n_days", errors, minimum=2, label="Days")
            if every_n_days is None and f"{key}-every_n_days" not in errors:
                errors[f"{key}-every_n_days"] = "How many days between doses?"
        elif frequency is Frequency.WEEKDAYS:
            chosen = set(raw["weekdays"].upper())
            weekdays = "".join(d for d in WEEKDAY_LETTERS if d in chosen) or None
            if not weekdays:
                errors[f"{key}-weekdays"] = "Pick at least one day."

        inventory_item_id = None
        if raw["inventory_item_id"]:
            try:
                inventory_item_id = int(raw["inventory_item_id"])
            except ValueError:
                inventory_item_id = -1
            if inventory_item_id not in inventory_ids:
                errors[f"{key}-inventory_item_id"] = "That inventory item no longer exists."

        notes = raw["notes"] or None
        if notes and len(notes) > 300:
            errors[f"{key}-notes"] = "Keep notes under 300 characters."

        items.append(ParsedItem(
            peptide_id=peptide_id,
            new_name=new_name,
            dose=_parse_dose(raw["dose"], f"{key}-dose", errors),
            dose_unit=_parse_choice(DoseUnit, raw["dose_unit"], DoseUnit.MG, f"{key}-dose_unit", errors),
            frequency=frequency,
            every_n_days=every_n_days,
            weekdays=weekdays,
            time_of_day=_parse_choice(TimeOfDay, raw["time_of_day"], TimeOfDay.ANY, f"{key}-time_of_day", errors),
            route=_parse_choice(Route, raw["route"], Route.SUBQ, f"{key}-route", errors),
            inventory_item_id=inventory_item_id,
            notes=notes,
            steps=_parse_steps(raw, key, titration, errors),
        ))

    if not items:
        errors["items"] = "Add at least one peptide."

    return ParsedProtocol(name=name, start_date=start, end_date=end, notes=state["notes"] or None,
                          titration_enabled=titration, goals=goals, items=items), errors
