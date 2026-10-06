"""Logging food: scaled snapshots, editing servings from the entry's own numbers, and deleting."""

import math
from datetime import date

from sqlalchemy.orm import Session

from app.models import FoodLog

_FIELDS = ("calories", "protein_g", "carb_g", "fat_g", "fiber_g")
MAX_SERVINGS = 50


def parse_servings(raw) -> tuple[float | None, str | None]:
    try:
        value = float(str(raw if raw is not None else "").strip())
    except ValueError:
        return None, "Servings must be a number."
    if not math.isfinite(value) or value <= 0 or value > MAX_SERVINGS:
        return None, f"Servings must be more than 0 and at most {MAX_SERVINGS}."
    return value, None


def snapshot(values: dict, servings: float) -> dict:
    """The entry's stored numbers: per-serving values times servings, rounded to 2 decimals."""
    out = {f: round(float(values[f]) * servings, 2) for f in _FIELDS}
    out["name"], out["serving"] = values["name"], values["serving"]
    return out


def add_entry(session: Session, uid: int, day: date, meal: str, values: dict, servings: float, food_id: int | None) -> FoodLog:
    log = FoodLog(owner_id=uid, eaten_on=day, meal=meal, food_id=food_id, servings=servings, **snapshot(values, servings))
    session.add(log)
    session.commit()
    return log


def own_entry(session: Session, uid: int, log_id: int) -> FoodLog:
    log = session.get(FoodLog, log_id)
    if log is None or log.owner_id != uid:
        raise LookupError("entry not found")
    return log


def edit_entry(session: Session, uid: int, log_id: int, *, servings: float | None, meal: str | None) -> FoodLog:
    """Change servings (the numbers are rescaled from this entry's own, so a food changed since never leaks in) and/or meal."""
    log = own_entry(session, uid, log_id)
    if servings is not None and servings != log.servings:
        factor = servings / log.servings
        for field in _FIELDS:
            setattr(log, field, round(getattr(log, field) * factor, 2))
        log.servings = servings
    if meal is not None:
        log.meal = meal
    session.commit()
    return log


def delete_entry(session: Session, uid: int, log_id: int) -> None:
    session.delete(own_entry(session, uid, log_id))
    session.commit()
