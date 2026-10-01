"""Total course quantities for a protocol -- per item, the full planned-course dose total (and,
where computable, an estimated vial count + BAC water total), for the Protocols page's totals
popup. Deliberately ignores protocol.paused/protocol.ended_on: this is the full *planned* course,
not "how much is left" or "how much was actually used." Pure except for the one inventory lookup
dict callers pass in -- no database access of its own.
"""

import math
from dataclasses import dataclass
from datetime import timedelta

from app.calculator.reconstitution import _DOSE_UNITS_TO_MG
from app.calendar.schedule import is_due
from app.models import Frequency, PurchasingUnit
from app.protocols.status import current_step, current_week

_BAC_WATER_ML_PER_VIAL = 1.5


@dataclass
class ItemTotal:
    peptide: str
    unit: str
    as_needed: bool
    total_amount: float | None
    vials_estimate: int | None
    bac_water_ml: float | None
    note: str | None


def _days(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _effective_dose(item, protocol, day):
    # Mirrors app/calendar/schedule.py's _due_item: titration only applies when the protocol has
    # it enabled, even if the item happens to carry step rows.
    if protocol.titration_enabled and item.steps:
        step = current_step(item.steps, current_week(protocol.start_date, day))
        if step is not None:
            return step.dose
    return item.dose


def _as_needed_total(item, inventory_by_id: dict) -> ItemTotal:
    inv = inventory_by_id.get(item.inventory_item_id) if item.inventory_item_id else None
    kit = inv is not None and inv.purchasing_unit == PurchasingUnit.KIT_OF_10
    vials = 10 if kit else 1
    note = "As needed — 1 kit (10 vials)" if kit else "As needed — 1 vial"
    return ItemTotal(peptide=item.peptide.name, unit=item.dose_unit.value, as_needed=True,
                     total_amount=None, vials_estimate=vials, bac_water_ml=vials * _BAC_WATER_ML_PER_VIAL,
                     note=note)


def _scheduled_total(item, protocol, inventory_by_id: dict) -> ItemTotal:
    total = 0.0
    for day in _days(protocol.start_date, protocol.end_date):
        if is_due(item, protocol.start_date, day):
            dose = _effective_dose(item, protocol, day)
            if dose is not None:
                total += dose

    unit = item.dose_unit.value
    inv = inventory_by_id.get(item.inventory_item_id) if item.inventory_item_id else None
    dose_factor = _DOSE_UNITS_TO_MG.get(unit)

    if inv is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "No inventory item linked")
    if dose_factor is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "IU — vial count not calculable")
    vial_factor = _DOSE_UNITS_TO_MG.get(inv.vial_size_unit.value)
    if not inv.vial_size_mg or vial_factor is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "Inventory item has no vial size set")

    total_mg = total * dose_factor
    vial_mg = inv.vial_size_mg * vial_factor
    vials_estimate = math.ceil(total_mg / vial_mg - 1e-9)
    bac_water_ml = vials_estimate * _BAC_WATER_ML_PER_VIAL
    return ItemTotal(item.peptide.name, unit, False, total, vials_estimate, bac_water_ml, None)


def compute_course_totals(protocol, inventory_by_id: dict) -> list[ItemTotal] | None:
    """None when `protocol.end_date` is None -- an ongoing protocol has no course to total."""
    if protocol.end_date is None:
        return None
    return [
        _as_needed_total(item, inventory_by_id) if item.frequency is Frequency.AS_NEEDED
        else _scheduled_total(item, protocol, inventory_by_id)
        for item in protocol.items
    ]
