"""Switching a protocol line from one form (medium) of a medicine to another, say sublingual drops to an injectable vial.

Two inventory items are the same medicine when their names match (the grouping the Inventory page already uses). The switch is offered only
when that medicine is stocked in two or more mediums, and only to a medium other than the one the line uses now."""

from dataclasses import dataclass

from app.inventory.groups import group_items
from app.models import Category, Medium, Route
from app.protocols.status import Status, protocol_status

# What a medium is normally taken by; the person can change it on the form (an injectable might be IM rather than SubQ).
DEFAULT_ROUTE = {
    Medium.LYOPHILIZED: Route.SUBQ, Medium.LIQUID: Route.SUBQ, Medium.AUTOINJECTOR: Route.SUBQ, Medium.DUAL_CHAMBER: Route.SUBQ,
    Medium.PILL: Route.ORAL, Medium.DROPS: Route.ORAL, Medium.INHALER: Route.OTHER, Medium.SALVE: Route.TOPICAL,
}


@dataclass(frozen=True)
class Option:
    item_id: int
    medium: str
    label: str
    route: str


def _key(name: str) -> str:
    return name.casefold().strip()


def _label(item) -> str:
    size = f" {item.vial_size_mg:g} {item.vial_size_unit.value}" if item.vial_size_mg else ""
    return f"{item.medium.value}: {item.name}{size} ({item.available_count} left)"


def options(line, inventory) -> list[Option]:
    """The forms `line` could switch to: none unless its medicine is stocked in two or more mediums. `inventory` is the person's items."""
    current = next((i for i in inventory if i.id == line.inventory_item_id), None)
    name = current.name if current is not None else line.peptide.name
    medicines = [i for i in inventory if i.category == Category.MEDICINE and i.medium is not None and _key(i.name) == _key(name)]
    if len({i.medium for i in medicines}) < 2:
        return []
    now = current.medium if current is not None else None
    chosen = {}
    for item in group_items(medicines)[0].items:               # use-first order, so the form offered is the one with stock to spare
        if item.medium != now and item.medium not in chosen:
            chosen[item.medium] = item
    return [Option(i.id, i.medium.value, _label(i), DEFAULT_ROUTE.get(i.medium, Route.OTHER).value) for i in chosen.values()]


def current_lines(protocols, today) -> list:
    """The lines of every protocol that has not ended."""
    return [it for p in protocols if protocol_status(p, today) is not Status.ENDED for it in p.items]


def by_item(protocols, inventory, today) -> dict[int, list[dict]]:
    """{inventory item id: the current lines using it that can be switched}, each with its protocol and the forms to switch to."""
    found: dict[int, list[dict]] = {}
    for p in protocols:
        if protocol_status(p, today) is Status.ENDED:
            continue
        for line in p.items:
            if line.inventory_item_id is None:
                continue
            opts = options(line, inventory)
            if opts:
                found.setdefault(line.inventory_item_id, []).append({"protocol": p, "line": line, "options": opts})
    return found
