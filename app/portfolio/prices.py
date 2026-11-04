"""The Protocol Portfolio's last page: the three best ways to buy each peptide for the whole course, and the BAC water the course needs.

Reuses the Shopping plan's own maths (course totals, the 5% buffer, whole packs, the newest price list per vendor) so the two never disagree.
Shipping is shown apart from the item cost: an order's shipping is shared by everything bought from that source, so it is not split per peptide."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventoryItem, Peptide
from app.protocols.course_totals import compute_course_totals
from app.shopping.build import _concentration_ids, _needs, _offers, shop_for_protocol
from app.shopping.planner import top_lines
from app.library.price_lists.analysis import current_lists

PER_PEPTIDE = 3


def _pack(size: int, kind: str | None) -> str:
    if kind:
        return kind if size == 1 else f"{kind} of {size}"
    return "single" if size == 1 else f"pack of {size}"


def _option(line) -> dict:
    o = line.offer
    return {"vendor": o.vendor_name, "warehouse": o.warehouse, "list_date": o.list_date, "size_label": o.size_label, "packs": line.packs,
            "pack": _pack(o.pack_size, o.pack_type), "per_vial": round(o.pack_price / o.pack_size, 2), "vials": line.vials, "cost": line.cost,
            "leftover_vials": line.leftover_vials, "premixed": o.premixed}


def portfolio_prices(session: Session, protocol, uid: int, shipping: dict) -> dict:
    """{"status": "ok" | "no_end_date", "shipping", "peptides": [{"name", "need", "options": [...]}], "unpriced": [...], "bac": {...} | None}."""
    if protocol.end_date is None:
        return {"status": "no_end_date", "shipping": shipping, "peptides": [], "unpriced": [], "bac": None}
    inventory_by_id = {i.id: i for i in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid))}
    peptides_by_id = {p.id: p for p in session.scalars(select(Peptide).where(Peptide.id.in_({it.peptide_id for it in protocol.items})))}
    totals = compute_course_totals(protocol, inventory_by_id, peptides_by_id) or []
    no_bac = _concentration_ids(session, {p.id for p in current_lists(session)})
    needs, notes, _, factors = _needs(protocol, totals, inventory_by_id, peptides_by_id, no_bac)
    offers, needs_volume, needs_strength = _offers(session, needs, factors)
    asking = {a["name"] for a in needs_volume + needs_strength}
    rows = []
    for need in needs:
        if need.name in asking:
            continue
        options = [_option(l) for l in top_lines(need, [o for o in offers if o.need_key == need.key], PER_PEPTIDE)]
        rows.append({"name": need.name, "need": f"{need.amount:g} {need.unit}" if not need.vials else f"{need.vials} vial{'s' if need.vials != 1 else ''}, as needed",
                     "options": options})
    unpriced = [{"name": a["name"], "reason": "Listed by strength or volume only; give its vial size in the Shopping plan to price it"} for a in needs_volume + needs_strength]
    unpriced += [{"name": n["name"], "reason": n["reason"]} for n in notes]
    shop = shop_for_protocol(session, protocol, uid, shipping)
    return {"status": "ok", "shipping": shipping, "peptides": rows, "unpriced": unpriced, "bac": shop.get("bac")}
