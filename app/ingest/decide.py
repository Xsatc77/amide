"""Whether a read list goes straight into the data or waits for the administrator."""

import statistics
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.ingest.readers import priced_fraction
from app.library.price_lists.analysis import current_lists
from app.library.price_lists.reader import PriceListData

MIN_ROWS, MIN_PRICED = 5, 0.8
RATIO_LOW, RATIO_HIGH = 0.5, 2.0


@dataclass
class Verdict:
    status: str                     # "imported" (meaning: import now), "needs_review" or "duplicate"
    reason: str | None = None


def _current(session: Session, vendor_id: int, warehouse: str):
    return next((p for p in current_lists(session) if p.vendor_id == vendor_id and p.warehouse.value == warehouse), None)


def median_price_ratio(session: Session, vendor_id: int, warehouse: str, data: PriceListData) -> float | None:
    """Median of new per-vial price / current per-vial price over the products (code and size) in both lists, or None."""
    current = _current(session, vendor_id, warehouse)
    if current is None:
        return None
    old = {}
    for i in current.items:
        if i.code and i.pack_price and i.pack_size:
            old[(i.code.upper(), str(i.vial_unit).lower(), round(float(i.vial_amount), 3))] = i.pack_price / i.pack_size
    ratios = []
    for r in data.rows:
        if r.code and r.pack_price and r.spec.pack_size:
            key = (r.code.upper(), str(r.spec.unit).lower(), round(float(r.spec.amount), 3))
            if old.get(key):
                ratios.append((r.pack_price / r.spec.pack_size) / old[key])
    return statistics.median(ratios) if ratios else None


def decide(session: Session, *, vendor, enabled: bool, data: PriceListData, warehouse: str, assumed: bool, list_date: date,
           file_dupe: bool) -> Verdict:
    if file_dupe:
        return Verdict("duplicate", "the same file was already received")
    if vendor is None:
        return Verdict("needs_review", "choose the vendor for this group")
    if not enabled:
        return Verdict("needs_review", "this group is not enabled for automatic import")
    if len(data.rows) < MIN_ROWS or priced_fraction(data) < MIN_PRICED:
        return Verdict("needs_review", f"only {len(data.rows)} rows read, or too few have a price")
    if assumed:
        return Verdict("needs_review", "the warehouse was not stated, so China was assumed")
    held = _current(session, vendor.id, warehouse)
    if held is not None and list_date < held.list_date:
        return Verdict("needs_review", f"older than the current list ({held.list_date.isoformat()})")
    ratio = median_price_ratio(session, vendor.id, warehouse, data)
    if ratio is not None and not (RATIO_LOW <= ratio <= RATIO_HIGH):
        return Verdict("needs_review", f"prices differ a lot from the current list (median x{ratio:.2f}); possibly misread")
    return Verdict("imported")
