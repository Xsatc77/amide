"""Spending: what each peptide cost per vial, per mg (or IU) and per dose, and what was spent each month. Costs include each order's
share of shipping and tax, the same way the order lines work out a true per-vial cost."""

from collections import defaultdict

from app.calculator import units as unit_math


def _per_unit_basis(item) -> tuple[float, str] | None:
    """(amount per vial in mg or IU, that unit), or None when the vial has no size."""
    if not item.vial_size_mg:
        return None
    unit = item.vial_size_unit.value
    if unit == "mcg":
        return item.vial_size_mg / 1000, "mg"
    return item.vial_size_mg, unit


def summarize(items, protocol_items=()) -> dict:
    """items: the user's InventoryItem rows (Medicine). protocol_items: ProtocolItem rows linked to them, for the cost per dose."""
    dose_by_item = {}
    for pi in protocol_items:
        if pi.inventory_item_id and pi.dose is not None and pi.inventory_item_id not in dose_by_item:
            dose_by_item[pi.inventory_item_id] = pi
    rows, monthly = [], defaultdict(float)
    for item in items:
        spent, vials = 0.0, 0
        for line in item.order_items:
            cost = line.total_cost
            if cost is None or cost <= 0:
                continue
            spent += cost
            vials += line.quantity
            monthly[line.order.order_date.strftime("%Y-%m")] += cost
        if spent <= 0 or vials <= 0:
            continue
        row = {"item_id": item.id, "name": item.name, "spent": spent, "vials": vials, "per_vial": spent / vials, "per_unit": None, "unit": None, "per_dose": None}
        basis = _per_unit_basis(item)
        if basis:
            amount, unit = basis
            row["per_unit"], row["unit"] = spent / (vials * amount), unit
            pi = dose_by_item.get(item.id)
            if pi is not None:
                dose = unit_math.convert(pi.dose, pi.dose_unit.value, unit, item.iu_per_mg or unit_math.default_iu_per_mg(item.name))
                if dose is not None:
                    row["per_dose"] = dose * row["per_unit"]
        rows.append(row)
    rows.sort(key=lambda r: -r["spent"])
    months = [{"month": m, "total": t} for m, t in sorted(monthly.items(), reverse=True)]
    return {"rows": rows, "months": months, "grand_total": sum(r["spent"] for r in rows)}
