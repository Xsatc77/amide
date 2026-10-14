"""The gauge on an Active Vials card: what is left in the vial (from the volume still in it and the reconstitution concentration), and what
the upcoming protocol will take from it. Dose by dose, the schedule is walked forward to the vial's discard-by date, so a titration that
grows (0.5, 1, 2 mg) uses the vial faster than a flat dose would."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.calendar.schedule import occurrences
from app.models import ActiveVial, Category, DoseLog, InventoryItem, Protocol
from app.protocols.status import Status, protocol_status

LOW_FRACTION = 0.15
LOW_DOSES = 2
HORIZON_DAYS = 400                  # never walk the schedule further than this, whatever the discard-by date says
_EPS = 1e-9


@dataclass(frozen=True)
class Projection:
    count: int                      # upcoming doses the vial can cover, in order
    through: date | None            # the date of the last one
    short_on: date | None           # the first dose (before the discard-by date) that the vial cannot cover
    left_after: float               # what remains once those doses are taken
    consumed: int                   # how many entries of the dose list were used (the next vial starts after them)


@dataclass(frozen=True)
class VialGauge:
    fraction: float
    percent: int
    left_text: str
    level: str                      # "ok", "low" or "empty"
    projection: Projection | None   # None when no active protocol uses this vial
    next_dose: str | None           # "1 mg", the next scheduled dose
    protocol_note: str | None


def simulate(remaining: float, doses: list[tuple[date, float]], discard_by: date) -> Projection:
    """Take the doses in date order from `remaining` until one does not fit or the discard-by date passes."""
    count, through, left = 0, None, remaining
    for i, (day, amount) in enumerate(doses):
        if day > discard_by:
            return Projection(count, through, None, left, i)
        if amount > left + _EPS:
            return Projection(count, through, day, left, i)
        left -= amount
        count, through = count + 1, day
    return Projection(count, through, None, max(left, 0.0), len(doses))


def level(fraction: float, doses_left: int | None) -> str:
    if fraction <= 0.001:
        return "empty"
    if fraction < LOW_FRACTION or (doses_left is not None and doses_left <= LOW_DOSES):
        return "low"
    return "ok"


def _g(value: float) -> str:
    return f"{round(value, 2):g}"


def _upcoming(session: Session, uid: int, today: date, last: date) -> tuple[list[Protocol], set[tuple[int, date]]]:
    protocols = [p for p in session.scalars(select(Protocol).where(Protocol.owner_id == uid)) if protocol_status(p, today) is Status.ACTIVE]
    logged = {(pid, d) for pid, d in session.execute(select(DoseLog.protocol_item_id, DoseLog.scheduled_date).where(
        DoseLog.owner_id == uid, DoseLog.scheduled_date >= today))}
    return protocols, logged


def _doses_for(protocols, logged, item: InventoryItem, today: date, last: date) -> list[tuple[date, float]]:
    ids = {it.id for p in protocols for it in p.items if it.inventory_item_id == item.id}
    if not ids:
        return []
    factor = item.iu_per_mg or unit_math.default_iu_per_mg(item.name)
    unit = item.vial_size_unit.value
    out = []
    for occ in occurrences(protocols, today, last):
        for due in occ.items:
            if due.protocol_item_id in ids and due.dose is not None and (due.protocol_item_id, occ.date) not in logged:
                amount = unit_math.convert(due.dose, due.unit, unit, factor)
                if amount is not None:
                    out.append((occ.date, amount))
    return out


def vial_gauges(session: Session, uid: int, vials: list[ActiveVial], items: dict[int, InventoryItem], today: date) -> dict[int, VialGauge]:
    """A gauge for each vial. The upcoming doses come from the viewer's own active protocols, so a vial shared by someone else gets the
    gauge but no projection. Several open vials of one product are used in discard-by order, each taking up where the last runs short."""
    mine = [v for v in vials if v.owner_id == uid]
    last = today + timedelta(days=HORIZON_DAYS)
    protocols, logged = _upcoming(session, uid, today, last) if mine else ([], set())
    queues: dict[int, list[tuple[date, float]]] = {}
    out: dict[int, VialGauge] = {}
    for v in sorted(vials, key=lambda v: (v.discard_by, v.id)):
        item = items.get(v.inventory_item_id)
        fraction = max(0.0, min(1.0, v.volume_remaining_ml / v.water_ml)) if v.water_ml else 0.0
        projection = next_dose = note = None
        if item is not None and item.category is Category.BAC_WATER:
            left_text = f"{_g(v.volume_remaining_ml)} mL left of {_g(v.water_ml)} mL"
        else:
            conc, unit = v.concentration_mg_ml or 0.0, v.vial_unit or "mg"
            left_text = f"{_g(v.volume_remaining_ml * conc)} {unit} left of {_g(v.water_ml * conc)} {unit}"
            if v.owner_id == uid and item is not None:
                if item.id not in queues:
                    queues[item.id] = _doses_for(protocols, logged, item, today, last)
                queue = queues[item.id]
                if queue:
                    projection = simulate(v.volume_remaining_ml * conc, queue, v.discard_by)
                    queues[item.id] = queue[projection.consumed:]
                    next_dose = f"{_g(queue[0][1])} {unit}"
                else:
                    note = "No active protocol uses this vial."
        doses_left = projection.count if projection is not None and projection.short_on is not None else None
        out[v.id] = VialGauge(fraction, round(fraction * 100), left_text, level(fraction, doses_left), projection, next_dose, note)
    return out
