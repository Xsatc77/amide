"""Setting the real amount in a vial that was open before Amide tracked it: how it was mixed and what has been taken out (or, for BAC Water, how
many mL remain). Answering marks the vial confirmed, so its gauge is no longer an estimate. Vials made by reconstituting in Amide start
confirmed, since the mix and every logged dose are known."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import now_utc
from app.calculator import units as unit_math
from app.models import ActiveVial, Category, InventoryItem

MAX_WATER_ML = 200.0
MAX_DOSES = 1000


@dataclass(frozen=True)
class Result:
    concentration: float
    volume_remaining_ml: float


def remaining_after(*, size: float, vial_unit: str, water_ml: float, doses_taken: int, dose_amount: float | None, dose_unit: str,
                    iu_per_mg: float | None, ml_left: float | None = None) -> Result:
    """The mix's concentration (the vial's amount over the water) and the volume left once `doses_taken` doses of `dose_amount` are out. A
    measured `ml_left` wins over the arithmetic; the volume never goes below zero."""
    concentration = size / water_ml
    if ml_left is not None:
        return Result(concentration, round(min(max(ml_left, 0.0), water_ml), 4))
    drawn = 0.0
    if doses_taken and dose_amount:
        per_dose = unit_math.convert(dose_amount, dose_unit, vial_unit, iu_per_mg)
        drawn = (per_dose or 0.0) * doses_taken
    return Result(concentration, round(max(water_ml - drawn / concentration, 0.0), 4))


def pending(session: Session, uid: int) -> list[ActiveVial]:
    """The person's open vials that have not been confirmed and not been asked about yet."""
    return list(session.scalars(select(ActiveVial).where(
        ActiveVial.owner_id == uid, ActiveVial.discarded_at.is_(None), ActiveVial.confirmed.is_(False), ActiveVial.prompted_at.is_(None))
        .order_by(ActiveVial.discard_by, ActiveVial.id)))


def dialog_vials(session: Session, uid: int, target_raw=None) -> list[dict]:
    """The vials the prompt asks about, each as {vial, item, bac}: the ones never asked about, plus the one a card's Update link names."""
    vials = pending(session, uid)
    if target_raw and str(target_raw).isdigit() and int(target_raw) not in {v.id for v in vials}:
        target = session.get(ActiveVial, int(target_raw))
        if target is not None and target.owner_id == uid and target.discarded_at is None:
            vials.append(target)
    out = []
    for vial in vials:
        item = session.get(InventoryItem, vial.inventory_item_id)
        if item is not None:
            out.append({"vial": vial, "item": item, "bac": item.category is Category.BAC_WATER})
    return out


def skip(session: Session, uid: int) -> None:
    for vial in pending(session, uid):
        vial.prompted_at = now_utc().replace(tzinfo=None)
    session.commit()


def _number(raw, field: str, errors: dict, *, minimum: float | None = None, maximum: float | None = None, whole: bool = False, required: bool = True):
    text = str(raw or "").strip()
    if not text:
        if required:
            errors[field] = "Enter a number."
        return None
    try:
        value = int(text) if whole else float(text)
    except ValueError:
        errors[field] = "Enter a whole number." if whole else "Enter a number."
        return None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        errors[field] = f"Enter a number between {minimum:g} and {maximum:g}." if minimum is not None and maximum is not None else "That number is out of range."
        return None
    return value


def apply(session: Session, uid: int, form) -> dict[str, str]:
    """Save the answers for every vial named in `vial_ids` (the person's own open vials; others are ignored). Nothing is saved unless every
    answer is valid. Returns {field: message}, empty on success."""
    errors: dict[str, str] = {}
    changes = []
    for raw_id in form.getlist("vial_ids"):
        vial = session.get(ActiveVial, int(raw_id)) if str(raw_id).isdigit() else None
        if vial is None or vial.owner_id != uid or vial.discarded_at is not None:
            continue
        item = session.get(InventoryItem, vial.inventory_item_id)
        key = lambda name: f"{name}[{vial.id}]"
        if item is not None and item.category is Category.BAC_WATER:
            left = _number(form.get(key("ml_left")), key("ml_left"), errors, minimum=0, maximum=vial.water_ml)
            if left is not None:
                changes.append((vial, vial.water_ml, vial.concentration_mg_ml, round(left, 4)))
            continue
        water = _number(form.get(key("water_ml")), key("water_ml"), errors, minimum=0.01, maximum=MAX_WATER_ML)
        taken = _number(form.get(key("doses_taken")), key("doses_taken"), errors, minimum=0, maximum=MAX_DOSES, whole=True, required=False) or 0
        amount = _number(form.get(key("dose_amount")), key("dose_amount"), errors, minimum=0.0001, required=bool(taken))
        unit = str(form.get(key("dose_unit")) or "mg")
        if unit not in unit_math.UNITS:
            errors[key("dose_unit")] = "Pick a unit."
        left = _number(form.get(key("ml_left")), key("ml_left"), errors, minimum=0, maximum=water, required=False) if water else None
        if water is None or key("doses_taken") in errors or key("dose_amount") in errors or key("ml_left") in errors or key("dose_unit") in errors:
            continue
        size = item.vial_size_mg if item is not None and item.vial_size_mg else (vial.concentration_mg_ml or 0.0) * vial.water_ml
        if not size:
            errors[key("water_ml")] = "This vial's size is not known, so Amide cannot work out what is left."
            continue
        factor = (item.iu_per_mg if item is not None else None) or vial.iu_per_mg or unit_math.default_iu_per_mg(item.name if item else None)
        result = remaining_after(size=size, vial_unit=item.vial_size_unit.value if item is not None else (vial.vial_unit or "mg"), water_ml=water,
                                 doses_taken=taken, dose_amount=amount, dose_unit=unit, iu_per_mg=factor, ml_left=left)
        changes.append((vial, water, result.concentration, result.volume_remaining_ml))
    if errors:
        return errors
    stamp = now_utc().replace(tzinfo=None)
    for vial, water, concentration, left in changes:
        vial.water_ml, vial.concentration_mg_ml, vial.volume_remaining_ml = water, concentration, left
        vial.confirmed, vial.prompted_at = True, stamp
    for vial in pending(session, uid):                  # a vial left out of the answers is not asked about again either
        vial.prompted_at = stamp
    session.commit()
    return {}
