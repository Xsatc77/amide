"""Reading a body-composition scale's export (the Renpho app's CSV). Each row is one weigh-in with everything the scale worked out from it. Columns are found by
their names, so a file with more or fewer columns, or the same ones in another order, still reads. Dashes mean the scale had no value (a weigh-in where it did
not pick up the body-composition reading). Masses are stored in pounds, so a kilogram export is converted."""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from app.units import LB_PER_KG

# column name (without its unit) -> (field on BodyMeasurement, is a mass in the file's weight unit)
COLUMNS = {
    "weight": ("weight_lbs", True),
    "bmi": ("scale_bmi", False),
    "body fat percentage": ("scale_body_fat_pct", False),
    "body fat mass": ("scale_fat_mass_lb", True),
    "muscle percentage": ("scale_muscle_pct", False),
    "muscle mass": ("scale_muscle_mass_lb", True),
    "skeletal muscle percentage": ("scale_skeletal_muscle_pct", False),
    "skeletal muscle mass": ("scale_skeletal_muscle_mass_lb", True),
    "bone percentage": ("scale_bone_pct", False),
    "bone mass": ("scale_bone_mass_lb", True),
    "protein percentage": ("scale_protein_pct", False),
    "protein mass": ("scale_protein_mass_lb", True),
    "body water percentage": ("scale_water_pct", False),
    "body water mass": ("scale_water_mass_lb", True),
    "fat-free mass": ("scale_fat_free_mass_lb", True),
    "subcutaneous fat": ("scale_subcutaneous_fat_pct", False),
    "visceral fat": ("scale_visceral_fat", False),
    "bmr": ("scale_bmr_kcal", False),
    "metabolic age": ("scale_metabolic_age", False),
    "whr": ("scale_whr", False),
}
_DATE_FORMATS = ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d")
_MAX_BYTES = 2_000_000


class ImportError_(ValueError):
    """The file is not a scale export Amide can read; the message says why."""


@dataclass(frozen=True)
class Reading:
    stamp: datetime
    values: dict[str, float]


@dataclass
class Parsed:
    readings: list[Reading] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _name_and_unit(header: str) -> tuple[str, str]:
    """"Weight(lb)" -> ("weight", "lb"); "WHR (Waist-to-Hip Ratio)" -> ("whr", "")."""
    text = (header or "").strip()
    name, _, rest = text.partition("(")
    unit = rest.rstrip(") ").strip().lower() if rest else ""
    name = name.strip().lower()
    return name, unit if unit in ("lb", "kg", "%", "kcal") else ""


def _number(text: str) -> float | None:
    text = (text or "").strip()
    if not text or set(text) <= {"-"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _when(date_text: str, time_text: str) -> datetime:
    for fmt in _DATE_FORMATS:
        try:
            day = datetime.strptime(date_text.strip(), fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError("date")
    t = (time_text or "").strip() or "00:00:00"
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.combine(day, datetime.strptime(t, fmt).time())
        except ValueError:
            continue
    raise ValueError("time")


def parse(text: str) -> Parsed:
    """The readings in a Renpho CSV, oldest first (so a later reading is stored after an earlier one), with a line for each row that could not be read."""
    if len(text.encode("utf-8", "ignore")) > _MAX_BYTES:
        raise ImportError_("That file is too large to be a scale export.")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header = next((r for r in rows if r and any(c.strip() for c in r)), None)
    if header is None:
        raise ImportError_("That file is empty.")
    names = [_name_and_unit(h) for h in header]
    where = {name: i for i, (name, _) in enumerate(names)}
    if "date" not in where or "weight" not in where:
        raise ImportError_("That does not look like a Renpho export: it needs Date and Weight columns.")
    id_col = where.get("no.")
    result = Parsed()
    for row in rows[rows.index(header) + 1:]:
        if not row or not any(c.strip() for c in row):
            continue
        label = (row[id_col].strip() if id_col is not None and id_col < len(row) else "") or "?"
        try:
            stamp = _when(row[where["date"]], row[where["time"]] if "time" in where and where["time"] < len(row) else "")
        except (ValueError, IndexError):
            result.errors.append(f"Row {label}: the date or time could not be read.")
            continue
        values: dict[str, float] = {}
        for name, (field_name, is_mass) in COLUMNS.items():
            i = where.get(name)
            if i is None or i >= len(row):
                continue
            value = _number(row[i])
            if value is None:
                continue
            if is_mass and names[i][1] == "kg":
                value = round(value * LB_PER_KG, 1)
            values[field_name] = value
        if "weight_lbs" not in values:
            result.errors.append(f"Row {label}: no weight.")
            continue
        result.readings.append(Reading(stamp, values))
    result.readings.sort(key=lambda r: r.stamp)
    return result


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: int          # readings already in Amide (same scale date and time)
    errors: list[str]


def import_readings(session, owner_id: int, parsed: Parsed) -> ImportResult:
    """Add each reading as a measurement on its date, oldest first. A reading already imported (same scale date and time) is skipped, so a file can be
    imported again. The caller commits."""
    from sqlalchemy import select

    from app.models import BodyMeasurement
    have = set(session.scalars(select(BodyMeasurement.scale_stamp).where(BodyMeasurement.owner_id == owner_id, BodyMeasurement.scale_stamp.is_not(None))))
    imported = skipped = 0
    for reading in parsed.readings:
        stamp = reading.stamp.strftime("%Y-%m-%d %H:%M:%S")
        if stamp in have:
            skipped += 1
            continue
        session.add(BodyMeasurement(owner_id=owner_id, measured_at=reading.stamp.date(), scale_stamp=stamp, **reading.values))
        have.add(stamp)
        imported += 1
    session.flush()
    return ImportResult(imported, skipped, parsed.errors)
