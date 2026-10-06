"""Foods: validating a food, searching the starter list and a person's own foods, and loading the starter list."""

import json
import math
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Food

LIMITS = {"calories": 5000, "protein_g": 500, "carb_g": 500, "fat_g": 500, "fiber_g": 500}
LABELS = {"calories": "Calories", "protein_g": "Protein", "carb_g": "Carbs", "fat_g": "Fat", "fiber_g": "Fiber",
          "serving_g": "Serving grams"}
STARTER_PATH = Path(__file__).with_name("starter_foods.json")


def _number(raw, field: str, errors: dict, upper: float) -> float | None:
    text = str(raw if raw is not None else "").strip()
    try:
        value = float(text)
    except ValueError:
        errors[field] = f"{LABELS.get(field, 'This')} must be a number."
        return None
    if not math.isfinite(value) or value < 0 or value > upper:
        errors[field] = f"{LABELS.get(field, 'This')} must be between 0 and {upper:g}."
        return None
    return value


def parse_food(raw: dict) -> tuple[dict, dict]:
    errors: dict[str, str] = {}
    values: dict = {}
    name = str(raw.get("name", "")).strip()
    serving = str(raw.get("serving", "")).strip()
    if not 1 <= len(name) <= 120:
        errors["name"] = "Name is required (120 characters at most)."
    if not 1 <= len(serving) <= 60:
        errors["serving"] = "Serving is required, for example 1 cup cooked (60 characters at most)."
    values["name"], values["serving"] = name, serving
    for field, upper in LIMITS.items():
        values[field] = _number(raw.get(field), field, errors, upper)
    grams = str(raw.get("serving_g", "") or "").strip()
    values["serving_g"] = _number(grams, "serving_g", errors, 5000) if grams else None
    return values, errors


def _words(query: str) -> list[str]:
    return [w for w in query.replace("%", " ").replace("_", " ").split() if w]


def search(session: Session, uid: int, query: str, limit: int = 25) -> list[Food]:
    """Foods visible to this person (their own and the starter list) whose name has every word of the query, their
    own first. An empty query lists their own foods, newest first."""
    if not query.strip():
        stmt = select(Food).where(Food.owner_id == uid).order_by(Food.created_at.desc(), Food.id.desc())
        return list(session.scalars(stmt.limit(limit)))
    words = _words(query)
    if not words:                          # only wildcard characters: nothing to match
        return []
    stmt = select(Food).where(or_(Food.owner_id == uid, Food.owner_id.is_(None)))
    for word in words:
        stmt = stmt.where(Food.name.ilike(f"%{word}%"))
    return list(session.scalars(stmt.order_by(Food.owner_id.is_(None), Food.name).limit(limit)))   # own foods first


def own_food(session: Session, food_id: int, uid: int) -> Food:
    food = session.get(Food, food_id)
    if food is None or food.owner_id != uid:
        raise LookupError("food not found")
    return food


def find_own(session: Session, uid: int, name: str, serving: str) -> Food | None:
    return session.scalar(select(Food).where(Food.owner_id == uid, Food.name == name, Food.serving == serving))


def own_or_create(session: Session, uid: int, values: dict) -> Food:
    """The person's food with this name and serving, creating it (or updating its numbers) from `values`."""
    food = find_own(session, uid, values["name"], values["serving"])
    if food is None:
        food = Food(owner_id=uid, source="mine", **values)
        session.add(food)
    else:
        for key, value in values.items():
            setattr(food, key, value)
    session.commit()
    return food


def copy_to_mine(session: Session, uid: int, food: Food) -> Food:
    existing = find_own(session, uid, food.name, food.serving)
    if existing is not None:
        return existing
    mine = Food(owner_id=uid, source="mine", name=food.name, serving=food.serving, serving_g=food.serving_g,
                calories=food.calories, protein_g=food.protein_g, carb_g=food.carb_g, fat_g=food.fat_g, fiber_g=food.fiber_g)
    session.add(mine)
    session.commit()
    return mine


def load_starter(session: Session, path: Path | None = None) -> dict:
    """Insert missing starter foods and refresh changed ones from the shipped JSON. Never deletes (a logged starter food
    must stay). A malformed row is skipped."""
    rows = json.loads((path or STARTER_PATH).read_text(encoding="utf-8"))
    existing = {(f.name, f.serving): f for f in session.scalars(select(Food).where(Food.owner_id.is_(None)))}
    added = updated = 0
    for row in rows:
        values, errors = parse_food(row)
        if errors:
            continue
        food = existing.get((values["name"], values["serving"]))
        if food is None:
            session.add(Food(owner_id=None, source="starter", **values))
            added += 1
        elif any(getattr(food, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(food, k, v)
            updated += 1
    session.commit()
    return {"added": added, "updated": updated}
