"""BAC water in the shopping plan. It is bought only as a brand the user ranks (their BAC Water inventory items): water from the
peptide vendors themselves is never offered, because there is no oversight of how it is made."""

import math
import re
from dataclasses import dataclass

UNRANKED = 99
BUFFER = 1.05
_BAC = re.compile(r"\bbac\b|bacteriostatic", re.IGNORECASE)
_NOT_WATER = re.compile(r"acetic|sodium chloride|saline|nacl", re.IGNORECASE)
_GENERIC_WORDS = {"bac", "bacteriostatic", "water", "sterile", "generic", "supplier", "injection", "for", "usp", "ml"}


@dataclass(frozen=True)
class BacOffer:
    vendor_id: int | None
    vendor_name: str
    warehouse: str
    product: str
    size_ml: float
    pack_size: int
    pack_price: float
    rank: int


def is_bac_water(name: str) -> bool:
    return bool(name) and "water" in name.lower() and bool(_BAC.search(name)) and not _NOT_WATER.search(name)


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z]+", name.lower()))


def rank_for(product: str, ranked_items: list[tuple[int | None, str]]) -> int:
    """The best (lowest) priority among the user's ranked BAC items whose brand words all appear in the product's name."""
    have = _words(product)
    best = UNRANKED
    for priority, item_name in ranked_items:
        brand = _words(item_name) - _GENERIC_WORDS
        if priority is not None and brand and brand <= have:
            best = min(best, priority)
    return best


def _pack_label(pack_size: int) -> str:
    return "single" if pack_size == 1 else f"pack of {pack_size}"


def plan_bac(ml: float, offers: list[BacOffer], plan_sources: list[tuple], shipping: dict) -> dict | None:
    """The BAC water to buy for `ml` mL: the best-ranked brand offered, then the lowest extra cost (water from a vendor and
    warehouse already in the plan adds no shipping; any other source is its own order with its shipping). None when no ranked brand is offered."""
    in_plan = set(plan_sources)
    best = None
    for o in offers:
        if o.rank >= UNRANKED or o.size_ml <= 0 or o.pack_size <= 0 or o.pack_price <= 0:
            continue
        units = max(1, math.ceil(ml * BUFFER / o.size_ml - 1e-9))
        packs = math.ceil(units / o.pack_size)
        cost = round(packs * o.pack_price, 2)
        ship = 0.0 if (o.vendor_id, o.warehouse) in in_plan else float(shipping["us" if o.warehouse == "us" else "china"])
        key = (o.rank, cost + ship, cost, o.vendor_name, o.product)
        if best is None or key < best[0]:
            best = (key, o, units, packs, cost, ship)
    if best is None:
        return None
    _, o, units, packs, cost, ship = best
    return {"vendor": o.vendor_name, "vendor_id": o.vendor_id, "warehouse": o.warehouse, "product": o.product, "size_label": f"{o.size_ml:g} mL",
            "pack_label": _pack_label(o.pack_size), "pack_size": o.pack_size, "units": units, "packs": packs, "ml": ml, "cost": cost, "shipping": ship,
            "extra": round(cost + ship, 2), "mode": "separate" if ship else "add", "rank": o.rank}
