"""Items that are not mixed from powder (drops, pills, autoinjectors, liquids, salves) use up their stock a dose at a time. Each unit holds `vial_size_mg`
of its own unit (a bottle of 5000 mcg drops is one dose of 5000 mcg), so a logged dose takes dose / unit-size of a unit. Powder vials are tracked by their
open vial's volume instead."""

from app.calculator import units as unit_math
from app.models import Category, DoseUnit, InventoryItem, Medium


def tracks_units(item: InventoryItem | None) -> bool:
    return item is not None and item.category == Category.MEDICINE and item.medium != Medium.LYOPHILIZED and bool(item.vial_size_mg)


def units_for_dose(item: InventoryItem, dose_value: float | None, dose_unit: DoseUnit) -> float | None:
    """The share of one unit a dose of `dose_value` takes, or None when it cannot be worked out (no dose, or a unit that does not convert)."""
    if dose_value is None or not tracks_units(item):
        return None
    factor = item.iu_per_mg or unit_math.default_iu_per_mg(item.name)
    amount = unit_math.convert(dose_value, dose_unit.value, item.vial_size_unit.value, factor)
    return None if amount is None else amount / item.vial_size_mg


def take(item: InventoryItem, units: float) -> float:
    """Use up `units` of the item's stock, never more than is left; returns what was actually taken."""
    taken = round(max(min(units, max(item.units_left, 0.0)), 0.0), 6)
    item.dosed_units = round((item.dosed_units or 0.0) + taken, 6)
    return taken


def give_back(item: InventoryItem, units: float) -> None:
    item.dosed_units = round(max((item.dosed_units or 0.0) - units, 0.0), 6)
