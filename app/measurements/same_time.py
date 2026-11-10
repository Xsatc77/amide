"""A second reading within 12 hours of an earlier one is not a new entry: the original stands, and only what it left blank is filled in.

Times are the person's own wall clock. An entry made before Amide kept the time counts as noon of its date."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BodyMeasurement

WINDOW = timedelta(hours=12)
_NOT_VALUES = {"id", "owner_id", "measured_at", "recorded_at", "scale_stamp", "notes"}
VALUE_FIELDS = tuple(c.name for c in BodyMeasurement.__table__.columns if c.name not in _NOT_VALUES)


def noon(day: date) -> datetime:
    return datetime.combine(day, time(12))


def effective_time(row: BodyMeasurement) -> datetime:
    return row.recorded_at or noon(row.measured_at)


def find_original(session: Session, owner_id: int, when: datetime, rows: list[BodyMeasurement] | None = None) -> BodyMeasurement | None:
    """The earliest entry of this owner taken within 12 hours of `when` (searching `rows` when they are already loaded)."""
    if rows is None:
        rows = session.scalars(select(BodyMeasurement).where(
            BodyMeasurement.owner_id == owner_id,
            BodyMeasurement.measured_at.between(when.date() - timedelta(days=1), when.date() + timedelta(days=1)))).all()
    near = [r for r in rows if abs(effective_time(r) - when) <= WINDOW]
    return min(near, key=lambda r: (effective_time(r), r.id or 0)) if near else None


def fill_blanks(original: BodyMeasurement, values: dict, notes: dict | None = None) -> int:
    """Put into `original` what it has no value for. Its own numbers are never replaced. Returns how many boxes were filled."""
    filled = 0
    for field in VALUE_FIELDS:
        if values.get(field) is not None and getattr(original, field) is None:
            setattr(original, field, values[field])
            filled += 1
    if notes:
        merged = dict(original.notes or {})
        for key, text in notes.items():
            if key not in merged:
                merged[key] = text
                filled += 1
        original.notes = merged or None
    return filled
