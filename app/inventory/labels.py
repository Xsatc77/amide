"""Vial labels: what goes on each, and the sheet and roll sizes they print on."""

from dataclasses import dataclass
from datetime import date

from app.models import Category, Order

DEFAULT_SIZE = "5160"

# key -> (menu text, css page rule)
LABEL_SIZES = {
    "5160": ("Sheet of 30 (Avery 5160, 2 5/8 x 1 in)", "@page { size: letter; margin: 0.5in 0.19in; }"),
    "2x1": ("Roll, 2 x 1 in", "@page { size: 2in 1in; margin: 0; }"),
    "4x2": ("Roll, 4 x 2 in", "@page { size: 4in 2in; margin: 0; }"),
}


def page_rule(size: str) -> str:
    return LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])[1]


@dataclass
class Label:
    name: str
    size: str
    lot: str | None = None
    received: date | None = None
    concentration: str | None = None
    recon_date: date | None = None
    discard_by: date | None = None


def _amount(item) -> str:
    return f"{item.vial_size_mg:g} {item.vial_size_unit.value}" if item.vial_size_mg else ""


def labels_for_order(order: Order, uid: int) -> list[Label]:
    """One label per vial received on a checked-in order, for the user's own peptide (Medicine) lines."""
    if order.arrival_date is None:
        return []
    out: list[Label] = []
    for line in order.items:
        item = line.inventory_item
        if item.owner_id != uid or item.category != Category.MEDICINE:
            continue
        out += [Label(name=item.name, size=_amount(item), lot=line.lot_number, received=order.arrival_date)] * (line.received_quantity or 0)
    return out


def label_for_vial(vial) -> Label:
    item = vial.inventory_item
    unit = vial.vial_unit or (item.vial_size_unit.value if item.vial_size_unit else "mg")
    return Label(name=item.name, size=_amount(item), concentration=f"{vial.concentration_mg_ml:g} {unit}/mL", recon_date=vial.date_mixed, discard_by=vial.discard_by)
