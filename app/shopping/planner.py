"""Shopping planner: the cheapest way to cover a protocol's needs from vendor price lists, never more than two orders (two shipments).

Pure functions: needs and offers in, plans out. Per need, every offered size and pack is priced (whole packs, with a 5% buffer on the
total), and a size that covers more than three times the need is skipped unless nothing else exists. Each vendor warehouse is a
source with its own shipping fee. One source carrying everything wins unless the protocol has a hard-to-find item (or nobody carries
everything), in which case two sources (two shipments, which may be one vendor's US and China warehouses) are allowed and the cheapest
total, shipping included, wins."""

import math
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

BUFFER = 1.05
MAX_OVER = 3.0
_EPS = 1e-9


@dataclass(frozen=True)
class Need:
    key: str
    name: str
    amount: float                 # in `unit` (mg, or IU for IU products)
    unit: str
    hard: bool = False            # hard to find (Testosterone, HGH): allows a second vendor
    # An as-needed item has no course total: it buys `vials` vials (1, or 10 for a kit), each between min_size and max_size.
    vials: int | None = None
    min_size: float | None = None
    max_size: float | None = None


@dataclass(frozen=True)
class Offer:
    vendor_id: int | None
    vendor_name: str
    warehouse: str                # "us" | "china"
    list_date: date
    need_key: str
    size: float                   # one vial, in the need's unit
    size_label: str               # as listed, for display ("10 mg")
    pack_size: int                # vials in the pack the price buys
    pack_price: float
    pack_type: str | None = None


@dataclass(frozen=True)
class Line:
    need: Need
    offer: Offer
    vials: int                    # vials needed to cover the need with the buffer
    packs: int                    # whole packs bought
    cost: float
    leftover_vials: int           # vials bought beyond those needed
    covered: float                # amount in everything bought


@dataclass
class SourcePlan:
    vendor_id: int | None
    vendor_name: str
    warehouse: str
    list_date: date
    shipping: float
    lines: list[Line]
    items_total: float
    total: float


@dataclass
class Plan:
    sources: list[SourcePlan]
    items_total: float
    shipping_total: float
    total: float
    missing: list[Need] = field(default_factory=list)
    reason: str = ""


@dataclass
class Result:
    plan: Plan | None
    alternatives: list[Plan]
    unshoppable: list[Need]


def _line(need: Need, offer: Offer) -> Line | None:
    if offer.size <= 0 or offer.pack_size <= 0 or offer.pack_price is None:
        return None
    if need.vials:                                              # as needed: so many vials, in the allowed size range
        if (need.min_size is not None and offer.size < need.min_size - _EPS) or (need.max_size is not None and offer.size > need.max_size + _EPS):
            return None
        vials = need.vials
    else:
        vials = max(1, math.ceil(need.amount * BUFFER / offer.size - _EPS))
    packs = math.ceil(vials / offer.pack_size)
    bought = packs * offer.pack_size
    return Line(need, offer, vials, packs, round(packs * offer.pack_price, 2), bought - vials, bought * offer.size)


def best_line(need: Need, offers: list[Offer]) -> Line | None:
    """The cheapest way to cover `need` from these offers (all for this need), or None when there are none."""
    lines = [l for l in (_line(need, o) for o in offers) if l is not None]
    if not lines:
        return None
    sensible = lines if need.vials else [l for l in lines if l.covered <= need.amount * MAX_OVER + _EPS]
    return min(sensible or lines, key=lambda l: (l.cost, l.leftover_vials, l.covered))


def _source_key(offer: Offer):
    return (offer.vendor_id if offer.vendor_id is not None else offer.vendor_name.casefold(), offer.warehouse)


def _source_plan(lines: list[Line], shipping: dict) -> SourcePlan:
    first = lines[0].offer
    items = round(sum(l.cost for l in lines), 2)
    fee = float(shipping.get(first.warehouse, 0.0))
    return SourcePlan(first.vendor_id, first.vendor_name, first.warehouse, max(l.offer.list_date for l in lines), fee, lines, items, round(items + fee, 2))


def _plan(sources: list[SourcePlan], needs: list[Need], reason: str = "") -> Plan:
    got = {l.need.key for s in sources for l in s.lines}
    items = round(sum(s.items_total for s in sources), 2)
    fees = round(sum(s.shipping for s in sources), 2)
    return Plan(sources, items, fees, round(items + fees, 2), [n for n in needs if n.key not in got], reason)


def plan_protocol(needs: list[Need], offers: list[Offer], shipping: dict) -> Result:
    needs = [n for n in needs if n.amount > 0 or n.vials]
    if not needs:
        return Result(None, [], [])
    by_need: dict[str, list[Offer]] = {}
    for o in offers:
        by_need.setdefault(o.need_key, []).append(o)
    unshoppable = [n for n in needs if not by_need.get(n.key)]
    shoppable = [n for n in needs if by_need.get(n.key)]
    if not shoppable:
        return Result(None, [], unshoppable)

    # each source's best line per need
    per_source: dict[tuple, dict[str, Line]] = {}
    for n in shoppable:
        groups: dict[tuple, list[Offer]] = {}
        for o in by_need[n.key]:
            groups.setdefault(_source_key(o), []).append(o)
        for key, group in groups.items():
            line = best_line(n, group)
            if line is not None:
                per_source.setdefault(key, {})[n.key] = line

    def single(key) -> Plan:
        return _plan([_source_plan(list(per_source[key].values()), shipping)], needs)

    def pair(a, b) -> Plan | None:
        chosen: dict[str, Line] = {}
        for n in shoppable:
            options = [per_source[k][n.key] for k in (a, b) if n.key in per_source[k]]
            if options:
                chosen[n.key] = min(options, key=lambda l: (l.cost, _source_key(l.offer)))
        sides = [[l for l in chosen.values() if _source_key(l.offer) == k] for k in (a, b)]
        if not all(sides):
            return None
        return _plan([_source_plan(side, shipping) for side in sides], needs)

    covered_keys = {k for lines in per_source.values() for k in lines}
    unshoppable += [n for n in shoppable if n.key not in covered_keys]            # offers exist, but none usable (size out of range)
    shoppable = [n for n in shoppable if n.key in covered_keys]
    if not shoppable:
        return Result(None, [], unshoppable)
    keys = sorted(per_source, key=str)
    singles = [single(k) for k in keys]
    pairs = [p for a, b in combinations(keys, 2) if (p := pair(a, b)) is not None]      # two shipments; a vendor's US and China warehouses may pair
    full_singles = [p for p in singles if all(n.key in {l.need.key for l in p.sources[0].lines} for n in shoppable)]
    full_pairs = [p for p in pairs if all(n.key in {l.need.key for s in p.sources for l in s.lines} for n in shoppable)]
    hard = [n for n in shoppable if n.hard]

    def total(p: Plan):
        return (p.total, len(p.sources), [s.vendor_name for s in p.sources])

    if full_singles and not hard:
        chosen = min(full_singles, key=total)
        chosen.reason = "One vendor carries everything" if len(full_singles) == 1 else "One vendor carries everything, and this one is the cheapest"
        pool = full_singles + full_pairs
    elif full_singles or full_pairs:
        pool = full_singles + full_pairs
        chosen = min(pool, key=total)
        if hard:
            names = ", ".join(n.name for n in hard)
            chosen.reason = (f"{names} is hard to find, so two orders were allowed; this is the cheapest total"
                             if len(chosen.sources) == 2 else f"{names} is hard to find, so two orders were allowed, but one vendor was cheapest")
        else:
            chosen.reason = "No single vendor carries everything; this two-order split is the cheapest"
    else:
        pool = singles + pairs
        covers = lambda p: len({l.need.key for s in p.sources for l in s.lines})
        chosen = min(pool, key=lambda p: (-covers(p), total(p)))
        chosen.reason = f"No one or two orders carry everything: {len(chosen.missing)} item(s) not carried by these orders"
        pool = [p for p in pool if covers(p) == covers(chosen)]
    alternatives = sorted((p for p in pool if p is not chosen), key=total)[:2]
    return Result(chosen, alternatives, unshoppable)
