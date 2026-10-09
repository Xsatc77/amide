"""One inventory row per peptide: the lines of the same peptide (any vial size) are grouped, and each line's lots are listed soonest-expiry first.

A lot is a checked-in order line. Amide counts what has been used per line (reconstituted and sold), not per lot, so the use is taken
from the lots in use-first order (nearest expiration, then the oldest arrival), the order the Peptides list already sorts by. That makes the
per-lot "on hand" an estimate, and the lots add up to the line's available count."""

from dataclasses import dataclass, field
from datetime import date

FAR = date.max


@dataclass
class Lot:
    expiration: date | None
    arrival: date | None
    received: int
    remaining: int
    lot_number: str | None = None
    order_id: int | None = None
    vendor: str | None = None


@dataclass
class StockGroup:
    key: tuple
    name: str
    owner_id: int
    items: list
    total: int
    earliest_expiration: date | None
    first_arrival: date | None
    sizes: list[str] = field(default_factory=list)
    mediums: list[str] = field(default_factory=list)
    storages: list[str] = field(default_factory=list)


def _item_key(item):
    """Use first: lines with stock before empty ones, nearest expiration, oldest arrival (undated after dated)."""
    return (item.available_count <= 0, item.next_expiration is None, item.next_expiration or FAR, item.first_arrival or FAR, item.name.casefold())


def _size(item) -> str | None:
    return f"{item.vial_size_mg:g} {item.vial_size_unit.value}" if getattr(item, "vial_size_mg", None) else None


def _unique(values) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def group_items(items) -> list[StockGroup]:
    """Groups by owner and peptide name (case-insensitive), sorted by name; the lines inside each group are in use-first order."""
    buckets: dict[tuple, list] = {}
    for item in items:
        buckets.setdefault((item.owner_id, item.name.casefold().strip()), []).append(item)
    groups = []
    for key, members in buckets.items():
        name = members[0].name                           # shown as first listed, whatever the capitalisation of the other lines
        members = sorted(members, key=_item_key)
        dated = [m.next_expiration for m in members if m.next_expiration and m.available_count > 0]
        arrivals = [m.first_arrival for m in members if m.first_arrival]
        groups.append(StockGroup(
            key=key, name=name, owner_id=members[0].owner_id, items=members, total=sum(m.available_count for m in members),
            earliest_expiration=min(dated) if dated else None, first_arrival=min(arrivals) if arrivals else None,
            sizes=_unique(_size(m) for m in members), mediums=_unique(m.medium.value if m.medium else None for m in members),
            storages=_unique(m.storage.label if m.storage else None for m in members)))
    return sorted(groups, key=lambda g: g.name.casefold())


def sort_use_first(groups: list[StockGroup]) -> list[StockGroup]:
    return sorted(groups, key=lambda g: (g.total <= 0, g.earliest_expiration is None, g.earliest_expiration or FAR,
                                         g.first_arrival or FAR, g.name.casefold()))


def lots(item) -> list[Lot]:
    """The line's checked-in lots, soonest expiry first, with the line's use taken from the first lots (see the module note)."""
    arrived = [li for li in item.order_items if li.order.arrival_date is not None and (li.received_quantity or 0) > 0]
    arrived.sort(key=lambda li: (li.expiration_date is None, li.expiration_date or FAR, li.order.arrival_date))
    used = (item.reconstituted_count or 0) + (item.sold_count or 0)
    result = []
    for li in arrived:
        taken = min(used, li.received_quantity)
        used -= taken
        result.append(Lot(expiration=li.expiration_date, arrival=li.order.arrival_date, received=li.received_quantity,
                          remaining=li.received_quantity - taken, lot_number=li.lot_number, order_id=getattr(li.order, "id", None),
                          vendor=getattr(li.order, "vendor", None)))
    return result


class _Row:
    """A one-line row for lists that are not grouped (BAC water, supplies), shaped like a StockGroup so one table macro draws both."""

    def __init__(self, item):
        self.items = [item]


def single_rows(items) -> list:
    return [_Row(i) for i in items]
