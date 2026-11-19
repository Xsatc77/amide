"""Protocol files: one protocol written out as a small JSON file, and read back in.

A file holds the protocol's plan only: its name, goals, notes, length and each peptide's dose, schedule and short-course timing. It carries no dates,
no inventory, no dose history and nothing about the person. Importing builds the same form the builder submits, so the same checks apply and a
peptide the library does not know is added as a custom one (by name)."""

import re
from datetime import timedelta

from app.models import Protocol

FORMAT = "amide-protocol"
VERSION = 1
MAX_BYTES = 1_000_000
MAX_ITEMS = 100
MAX_ROWS = 60


class TransferError(ValueError):
    """A file that is not a protocol file Amide can read. The message says why and is safe to show."""


def _num(v) -> str:
    return "" if v is None else f"{v:g}"


def file_name(p: Protocol) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", p.name.lower()).strip("-") or "protocol") + ".amide-protocol.json"


def export_protocol(p: Protocol) -> dict:
    out = {
        "format": FORMAT, "version": VERSION, "name": p.name, "goals": p.goal_slugs, "notes": p.notes or "",
        "titration": bool(p.titration_enabled),
        "short_days": p.short_days,
        "length_days": None if p.short_days or not p.end_date else (p.end_date - p.start_date).days + 1,
        "items": [],
    }
    for it in p.items:
        out["items"].append({
            "peptide": it.peptide.name, "dose": it.dose, "dose_unit": it.dose_unit.value, "frequency": it.frequency.value,
            "every_n_days": it.every_n_days, "weekdays": it.weekdays or "", "time_of_day": it.time_of_day.value, "route": it.route.value,
            "notes": it.notes or "",
            "start_day": it.start_day, "run_days": it.run_days, "repeat_months": it.repeat_months, "first_run": it.first_run or 1,
            "steps": [{"start_week": s.start_week, "end_week": s.end_week, "dose": s.dose} for s in it.steps],
            "cycle_offs": [{"start_week": c.start_week, "weeks": c.end_week - c.start_week + 1} for c in it.cycle_offs],
        })
    return out


def _text(value, limit: int) -> str:
    return "" if value is None else str(value).strip()[:limit]


def to_form(data, start_date: str, known_peptide_id) -> dict[str, list[str]]:
    """The builder's flat form fields for a protocol file, starting on `start_date` (ISO). `known_peptide_id(name)` gives a library peptide's id or None."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise TransferError("This is not an Amide protocol file.")
    if data.get("version") != VERSION:
        raise TransferError("This protocol file is from a newer version of Amide. Update Amide and try again.")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise TransferError("The file has no peptides in it.")
    if len(items) > MAX_ITEMS:
        raise TransferError(f"The file has more than {MAX_ITEMS} peptides.")
    goals = data.get("goals") if isinstance(data.get("goals"), list) else []

    form: dict[str, list[str]] = {
        "name": [_text(data.get("name"), 200)], "start_date": [start_date], "notes": [_text(data.get("notes"), 5000)],
        "goal": [_text(g, 40) for g in goals], "repeat_months": ["12"],
    }
    if data.get("titration"):
        form["titration"] = ["1"]
    if data.get("short_days") not in (None, ""):
        form["short_days"] = [_text(data.get("short_days"), 4)]
    elif data.get("length_days") not in (None, ""):
        try:
            from datetime import date
            form["end_date"] = [(date.fromisoformat(start_date) + timedelta(days=int(data["length_days"]) - 1)).isoformat()]
        except (ValueError, TypeError, OverflowError):
            raise TransferError("The protocol's length in the file is not a number of days.") from None

    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise TransferError("A peptide in the file is not written the way Amide expects.")
        name = _text(raw.get("peptide"), 120)
        pid = known_peptide_id(name) if name else None
        key = f"items-{i}"
        form[f"{key}-peptide_id"] = [str(pid) if pid else ""]
        form[f"{key}-new_name"] = ["" if pid else name]
        for field in ("dose", "dose_unit", "frequency", "every_n_days", "time_of_day", "route", "start_day", "run_days", "repeat_months", "first_run"):
            value = raw.get(field)
            form[f"{key}-{field}"] = [_num(value) if isinstance(value, float) else _text(value, 30)]
        form[f"{key}-weekdays"] = [_text(raw.get("weekdays"), 7)]
        form[f"{key}-notes"] = [_text(raw.get("notes"), 300)]
        for list_name, fields in (("steps", ("start_week", "end_week", "dose")), ("cycle_offs", ("start_week", "weeks"))):
            rows = raw.get(list_name) if isinstance(raw.get(list_name), list) else []
            if len(rows) > MAX_ROWS:
                raise TransferError(f"{name or 'A peptide'} has too many {list_name.replace('_', '-')} rows.")
            for j, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise TransferError(f"{name or 'A peptide'} has a {list_name.replace('_', '-')} row Amide cannot read.")
                for field in fields:
                    value = row.get(field)
                    form[f"{key}-{list_name}-{j}-{field}"] = [_num(value) if isinstance(value, float) else _text(value, 30)]
    return form
