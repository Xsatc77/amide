"""Dashboard alerts: low stock, expiration (vials + sealed stock), shipments running long. Pure
functions -- callers pass already-scoped-to-one-viewer rows; nothing here queries a database."""

from datetime import date, timedelta

EXPIRING_SOON_DAYS = 7


def low_stock_alerts(items, default_threshold: int, on_order=frozenset()) -> list[dict]:
    """`items` need `.id`, `.name`, `.available_count`, `.low_stock_threshold`. None on the item's
    own threshold means "use default_threshold" -- 0 is a real, valid threshold, never coerced.
    An item whose id is in `on_order` (it has an order that has not been checked in yet) is skipped:
    it is already being restocked, and until it arrives it only looks empty."""
    out = []
    for item in items:
        if item.id in on_order:
            continue
        threshold = item.low_stock_threshold if item.low_stock_threshold is not None else default_threshold
        if item.available_count <= threshold:
            out.append({"item_id": item.id, "name": item.name,
                       "available_count": item.available_count, "threshold": threshold})
    return out


def expiration_alerts(*, vials, items, today: date) -> list[dict]:
    """`vials` need `.id`, `.item_name`, `.discard_by`, `.discarded_at` (skip if not None -- already
    discarded). `items` need `.id`, `.name`, `.available_count`, `.expiration_date` (skip if None or
    available_count <= 0 -- nothing left to expire). Same two-threshold rule for both: `< today` is
    'expired', `today <= date <= today + EXPIRING_SOON_DAYS` is 'soon'."""
    out = []
    for vial in vials:
        if vial.discarded_at is not None:
            continue
        severity = _severity(vial.discard_by, today)
        if severity:
            out.append({"kind": "vial", "id": vial.id, "name": vial.item_name, "severity": severity})
    for item in items:
        if item.expiration_date is None or item.available_count <= 0:
            continue
        severity = _severity(item.expiration_date, today)
        if severity:
            out.append({"kind": "item", "id": item.id, "name": item.name, "severity": severity})
    return out


def _severity(target_date: date, today: date) -> str | None:
    if target_date < today:
        return "expired"
    if target_date <= today + timedelta(days=EXPIRING_SOON_DAYS):
        return "soon"
    return None


def shipment_alerts(orders, *, today: date, threshold_days: int) -> list[dict]:
    """`orders` need `.id`, `.vendor`, `.order_date`, `.shipped_date`, `.arrival_date`. Only
    unarrived orders (`arrival_date is None`) are ever eligible -- an order that arrived late but
    was checked in never alerts, no matter how long it took."""
    out = []
    for order in orders:
        if order.arrival_date is not None:
            continue
        anchor = order.shipped_date or order.order_date
        if (today - anchor).days > threshold_days:
            out.append({"order_id": order.id, "vendor": order.vendor, "days": (today - anchor).days})
    return out
