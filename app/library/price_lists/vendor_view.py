"""The vendor card's price-history chart: products, legends and drawn geometry, ready for the template."""

from sqlalchemy.orm import Session

from app.library.price_lists.analysis import PricePoint, vendor_price_history
from app.library.price_lists.chart import multi_series_chart

# Mid-saturation colors that read on both the light and the dark theme.
PALETTE = ["#0d9488", "#d97706", "#2563eb", "#db2777", "#7c3aed", "#059669", "#dc2626", "#ca8a04"]
_UNIT_ORDER = {"mg": 0, "mcg": 1, "IU": 2, "ml": 3, "mg/ml": 4}
_WAREHOUSE_LABEL = {"china": "China", "us": "USA"}


def _size(amount: float, unit: str) -> str:
    return f"{amount:g}{unit}"


def _tip(point: PricePoint) -> str:
    if point.pack_type == "kit":
        pack = f"kit of {point.pack_size}"
    elif point.pack_type == "box":
        pack = f"box of {point.pack_size}"
    else:
        pack = "pack"
    return f"{point.list_date:%m/%d/%Y} · ${point.per_vial:,.2f} per vial · ${point.pack_price:,.2f} {pack}"


def build_price_history(session: Session, vendor_id: int) -> dict | None:
    """One entry per product (its library name, or its listed name), each with a line per vial size. A size keeps
    one color on every product; a vendor with both warehouses gets a line per warehouse, the USA one dashed."""
    history = vendor_price_history(session, vendor_id)
    if not history:
        return None
    sizes = sorted({(a, u) for p in history for (a, u, _) in p.series}, key=lambda s: (_UNIT_ORDER.get(s[1], 9), s[0]))
    color = {size: PALETTE[i % len(PALETTE)] for i, size in enumerate(sizes)}
    two_warehouses = len({w for p in history for (_, _, w) in p.series}) > 1
    products = []
    for product in history:
        ordered = sorted(product.series.items(), key=lambda kv: (_UNIT_ORDER.get(kv[0][1], 9), kv[0][0], kv[0][2]))
        labelled = []
        for (amount, unit, warehouse), points in ordered:
            points = [p for p in points if p.per_vial is not None]  # a pack with no stated size has no per-vial price
            if not points:
                continue
            label = _size(amount, unit) + (f" · {_WAREHOUSE_LABEL.get(warehouse, warehouse)}" if two_warehouses else "")
            labelled.append((label, color[(amount, unit)], two_warehouses and warehouse == "us", points))
        if not labelled:
            continue
        chart = multi_series_chart({label: [(p.list_date, p.per_vial) for p in points]
                                    for label, _, _, points in labelled})
        for label, hue, dashed, points in labelled:
            drawn = chart["series"][label]
            drawn.update(color=hue, dashed=dashed)
            for dot, point in zip(drawn["points"], points):
                dot["tip"] = _tip(point)
        products.append({
            "key": product.key, "label": product.label, "peptide_id": product.peptide_id, "chart": chart,
            "legend": [{"label": label, "color": hue, "dashed": dashed} for label, hue, dashed, _ in labelled],
        })
    return {"products": products}
