"""Turning a protocol and the vendors' current price lists into a shopping plan (the database side of app.shopping.planner)."""

import math
import re
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.library.price_lists.analysis import current_lists
from app.models import Category, Frequency, InventoryItem, Peptide, PriceListItem, PurchasingUnit, User
from app.shopping.bac import BacOffer, is_bac_water, plan_bac, rank_for
from app.protocols.course_totals import compute_course_totals
from app.shopping.planner import Need, Offer, Plan, plan_protocol

DEFAULT_SHIPPING = {"china": 60.0, "us": 30.0}
MAX_FEE = 10_000.0
BAC_BOTTLE_ML = 30
BAC_DAYS_OPEN = 28            # an opened BAC water bottle is good for 28 days at room temperature
_HARD = re.compile(r"testosterone|\btrt\b|\bhgh\b|somatropin|growth hormone", re.IGNORECASE)
_SIZE_UNITS = ("mg", "mcg", "IU")
_OIL = re.compile(r"testosterone|\btrt\b", re.IGNORECASE)
CONCENTRATION_UNIT = "mg/ml"          # an oil sold by strength (250 mg/ml): the sheet does not say how much is in a vial


def _concentration_ids(session: Session, lists) -> set[int]:
    """Peptides the current lists sell by concentration (oil suspensions), which are injected as supplied: no BAC water."""
    if not lists:
        return set()
    return {pid for pid in session.scalars(select(PriceListItem.peptide_id).where(
        PriceListItem.price_list_id.in_(list(lists)), PriceListItem.vial_unit == CONCENTRATION_UNIT, PriceListItem.peptide_id.is_not(None)))}


def is_hard_to_find(name: str) -> bool:
    """Testosterone products and HGH (somatropin, 191AA): scarce, so a second vendor is allowed. The HGH fragments are not."""
    return "fragment" not in name.lower() and bool(_HARD.search(name))


def saved_shipping(user: User) -> dict:
    return {"china": user.shop_china_shipping_cents / 100 if user.shop_china_shipping_cents is not None else DEFAULT_SHIPPING["china"],
            "us": user.shop_us_shipping_cents / 100 if user.shop_us_shipping_cents is not None else DEFAULT_SHIPPING["us"]}


def parse_fee(raw) -> float | None:
    try:
        value = float(str(raw).strip().lstrip("$"))
    except ValueError:
        return None
    return round(value, 2) if 0 <= value <= MAX_FEE and math.isfinite(value) else None


def _base(amount: float, unit: str) -> tuple[float, str]:
    """An amount as mg (for mg and mcg) or IU."""
    return (amount / 1000, "mg") if unit == "mcg" else (amount, unit)


def _as_needed_range(item) -> tuple[float | None, float | None]:
    """The vial size an as-needed item buys, by its dose: a 5 mg vial for a dose up to 5 mg, a 10 mg vial up to 10 mg, and a vial that
    holds the dose beyond that (no dose set: anything from 5 to 10 mg). IU products have no such rule."""
    if item.dose_unit.value == "IU":
        return (item.dose or 0.0), None
    dose_mg = (item.dose / 1000 if item.dose_unit.value == "mcg" else item.dose) if item.dose else None
    low = 5.0 if dose_mg is None or dose_mg <= 5 else 10.0 if dose_mg <= 10 else dose_mg
    return low, (low * 2 if low > 10 else 10.0)


def _needs(protocol, totals, inventory_by_id, peptides_by_id, no_bac_ids=frozenset()) -> tuple[list[Need], list[dict], float, dict]:
    """(needs by peptide, notes for items that cannot be shopped, BAC water mL for the whole course, IU-per-mg factors)."""
    as_needed: dict[int, Need] = {}
    amounts: dict[int, float] = {}
    units: dict[int, str] = {}
    factors: dict[int, float | None] = {}
    notes: list[dict] = []
    bac_ml = 0.0
    for item, total in zip(protocol.items, totals):
        peptide = peptides_by_id.get(item.peptide_id) or item.peptide
        if item.peptide_id not in no_bac_ids and not _OIL.search(peptide.name):      # oils are not reconstituted
            bac_ml += total.bac_water_ml or 0.0
        inv = inventory_by_id.get(item.inventory_item_id) if item.inventory_item_id else None
        if item.frequency is Frequency.AS_NEEDED:                   # one vial (a kit of 10 if it is bought that way), sized by the dose
            low, high = _as_needed_range(item)
            kit = inv is not None and inv.purchasing_unit == PurchasingUnit.KIT_OF_10
            if item.peptide_id not in as_needed:
                as_needed[item.peptide_id] = Need(key=str(item.peptide_id), name=peptide.name, amount=0.0, unit="IU" if item.dose_unit.value == "IU" else "mg",
                                                  hard=is_hard_to_find(peptide.name), vials=10 if kit else 1, min_size=low, max_size=high)
                factors[item.peptide_id] = (inv.iu_per_mg if inv is not None and inv.iu_per_mg else None) or unit_math.default_iu_per_mg(peptide.name)
            continue
        if total.total_amount is not None:
            amount, unit = _base(total.total_amount, item.dose_unit.value)
        else:
            notes.append({"name": peptide.name, "reason": total.note or "No quantity to buy"})
            continue
        key = item.peptide_id
        if key in amounts and units[key] != unit:
            notes.append({"name": peptide.name, "reason": "Doses in different units could not be combined"})
            continue
        amounts[key] = amounts.get(key, 0.0) + amount
        units[key] = unit
        factors[key] = (inv.iu_per_mg if inv is not None and inv.iu_per_mg else None) or unit_math.default_iu_per_mg(peptide.name)
    needs = [Need(key=str(k), name=(peptides_by_id.get(k).name if peptides_by_id.get(k) else str(k)), amount=a, unit=units[k], hard=is_hard_to_find(peptides_by_id[k].name))
             for k, a in amounts.items() if k in peptides_by_id]
    for peptide_id, need in as_needed.items():
        if peptide_id in amounts:                                   # the course already buys this peptide: the as-needed use comes out of it
            notes.append({"name": need.name, "reason": "Also used as needed: covered by the course quantity above"})
        else:
            needs.append(need)
    return needs, notes, bac_ml, factors


def _offers(session: Session, needs: list[Need], factors: dict[int, float | None], sizes: dict | None = None) -> tuple[list[Offer], list[dict]]:
    """(the offers, and the concentration-only products that cannot be priced until the person says how big a vial is).
    `sizes` maps a peptide id to (mL per vial, vials per box) for those products."""
    sizes = sizes or {}
    ids = [int(n.key) for n in needs]
    lists = {p.id: p for p in current_lists(session)}
    if not ids or not lists:
        return [], []
    by_key = {n.key: n for n in needs}
    out: list[Offer] = []
    ask: dict[int, dict] = {}
    for row in session.scalars(select(PriceListItem).where(
            PriceListItem.peptide_id.in_(ids), PriceListItem.price_list_id.in_(list(lists)), PriceListItem.vial_unit == CONCENTRATION_UNIT,
            PriceListItem.pack_price > 0, PriceListItem.pack_size.is_not(None), PriceListItem.pack_size > 0)):
        need = by_key[str(row.peptide_id)]
        plist = lists[row.price_list_id]
        if row.peptide_id in sizes and need.unit == "mg":
            ml, per_box = sizes[row.peptide_id]
            out.append(Offer(vendor_id=plist.vendor_id, vendor_name=plist.vendor_name, warehouse=plist.warehouse.value, list_date=plist.list_date,
                             need_key=need.key, size=row.vial_amount * ml, size_label=f"{row.vial_amount:g} mg/mL × {ml:g} mL",
                             pack_size=row.pack_size * per_box, pack_price=row.pack_price, pack_type=row.pack_type))
        elif row.peptide_id not in sizes:
            ask.setdefault(row.peptide_id, {"peptide_id": row.peptide_id, "name": need.name, "strength": f"{row.vial_amount:g} mg/mL",
                                            "pack_type": row.pack_type or "pack", "pack_size": row.pack_size, "pack_price": row.pack_price})
    for row in session.scalars(select(PriceListItem).where(
            PriceListItem.peptide_id.in_(ids), PriceListItem.price_list_id.in_(list(lists)), PriceListItem.vial_unit.in_(_SIZE_UNITS),
            PriceListItem.pack_price > 0, PriceListItem.pack_size.is_not(None), PriceListItem.pack_size > 0)):
        need = by_key[str(row.peptide_id)]
        size_mg_or_iu, base_unit = _base(row.vial_amount, row.vial_unit)
        if base_unit != need.unit:                                   # IU against mass: bridge with the product's IU per mg
            factor = factors.get(row.peptide_id)
            if not factor:
                continue
            size_mg_or_iu = size_mg_or_iu * factor if need.unit == "IU" else size_mg_or_iu / factor
        plist = lists[row.price_list_id]
        out.append(Offer(vendor_id=plist.vendor_id, vendor_name=plist.vendor_name, warehouse=plist.warehouse.value, list_date=plist.list_date,
                         need_key=need.key, size=size_mg_or_iu, size_label=f"{row.vial_amount:g} {row.vial_unit}", pack_size=row.pack_size,
                         pack_price=row.pack_price, pack_type=row.pack_type))
    return out, list(ask.values())


def _plan_json(plan: Plan, chosen_total: float | None = None) -> dict:
    out = {"total": plan.total, "items_total": plan.items_total, "shipping_total": plan.shipping_total, "reason": plan.reason,
           "missing": [n.name for n in plan.missing],
           "sources": [{"vendor": s.vendor_name, "vendor_id": s.vendor_id, "warehouse": s.warehouse, "list_date": s.list_date.isoformat(), "shipping": s.shipping,
                        "items_total": s.items_total, "total": s.total,
                        "lines": [{"peptide": l.need.name, "size_label": l.offer.size_label, "pack_type": l.offer.pack_type, "pack_size": l.offer.pack_size,
                                   "packs": l.packs, "vials_needed": l.vials, "per_vial": round(l.offer.pack_price / l.offer.pack_size, 2), "cost": l.cost,
                                   "leftover_vials": l.leftover_vials} for l in s.lines]} for s in plan.sources]}
    if chosen_total is not None:
        out["vs_chosen"] = round(plan.total - chosen_total, 2)
    return out


def _without(plan_json: dict, asking: set) -> dict:
    """The plan with products still waiting for a vial size taken off its "not covered" list (the dialog asks about them instead)."""
    plan_json["missing"] = [m for m in plan_json["missing"] if m not in asking]
    return plan_json


def _bac_buy(session: Session, uid: int, bac_ml: float, plan: Plan | None, shipping: dict, min_units: int = 1) -> dict | None:
    """The BAC water to buy: only a brand the user ranks in their BAC Water inventory (never the peptide vendors' own water)."""
    ranked = [(i.bac_priority, i.name) for i in session.scalars(select(InventoryItem).where(
        InventoryItem.owner_id == uid, InventoryItem.category == Category.BAC_WATER))]
    lists = {p.id: p for p in current_lists(session)}
    if not ranked or not lists:
        return None
    offers = []
    for row in session.scalars(select(PriceListItem).where(PriceListItem.price_list_id.in_(list(lists)), PriceListItem.vial_unit == "ml",
                                                           PriceListItem.pack_price > 0, PriceListItem.pack_size > 0)):
        if is_bac_water(row.product_name or ""):
            plist = lists[row.price_list_id]
            offers.append(BacOffer(vendor_id=plist.vendor_id, vendor_name=plist.vendor_name, warehouse=plist.warehouse.value, product=row.product_name,
                                   size_ml=row.vial_amount, pack_size=row.pack_size, pack_price=row.pack_price, rank=rank_for(row.product_name, ranked)))
    return plan_bac(bac_ml, offers, [(s.vendor_id, s.warehouse) for s in plan.sources] if plan else [], shipping, min_units)


def shop_for_protocol(session: Session, protocol, uid: int, shipping: dict, sizes: dict | None = None) -> dict:
    """The JSON the Shopping plan dialog shows. `sizes` is {peptide id: (mL per vial, vials per box)} for oil products the lists
    sell by strength only; without it those are returned under `needs_volume` for the dialog to ask about."""
    if protocol.end_date is None:
        return {"status": "no_end_date"}
    inventory_by_id = {i.id: i for i in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid))}
    peptides_by_id = {p.id: p for p in session.scalars(select(Peptide).where(Peptide.id.in_({it.peptide_id for it in protocol.items})))}
    totals = compute_course_totals(protocol, inventory_by_id, peptides_by_id) or []
    no_bac = _concentration_ids(session, {p.id for p in current_lists(session)})
    needs, notes, bac_ml, factors = _needs(protocol, totals, inventory_by_id, peptides_by_id, no_bac)
    bac_min_units = max(1, math.ceil(((protocol.end_date - protocol.start_date).days + 1) / BAC_DAYS_OPEN - 1e-9))
    base = {"status": "ok", "protocol": {"id": protocol.id, "name": protocol.name}, "shipping": shipping,
            "bac": {"ml": round(bac_ml, 1), "bottles": max(math.ceil(bac_ml / BAC_BOTTLE_ML - 1e-9), bac_min_units)} if bac_ml > 0 else None}
    if not needs and not notes:
        return base | {"status": "nothing_to_buy", "plan": None, "alternatives": [], "unshoppable": []}
    offers, needs_volume = _offers(session, needs, factors, sizes)
    result = plan_protocol(needs, offers, shipping)
    asking = {a["name"] for a in needs_volume}
    unshoppable = notes + [{"name": n.name, "reason": "No current price list carries it in a usable size"} for n in result.unshoppable
                           if n.name not in asking]
    base["needs_volume"] = needs_volume
    base["sizes"] = {str(k): {"ml": v[0], "per_box": v[1]} for k, v in (sizes or {}).items()}
    if base["bac"]:
        base["bac"]["buy"] = _bac_buy(session, uid, bac_ml, result.plan, shipping, bac_min_units)
    extra = base["bac"]["buy"]["extra"] if base["bac"] and base["bac"]["buy"] else 0.0
    return base | {"grand_total": round((result.plan.total if result.plan else 0.0) + extra, 2) if (result.plan or extra) else None,
                   "plan": _without(_plan_json(result.plan), asking) if result.plan else None,
                   "alternatives": [_without(_plan_json(p, result.plan.total), asking) for p in result.alternatives] if result.plan else [],
                   "unshoppable": unshoppable}
