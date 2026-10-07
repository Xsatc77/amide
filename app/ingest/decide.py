"""Whether a read list goes straight into the data or waits for the administrator."""

import statistics
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.readers import has_price, priced_fraction
from app.library.price_lists.analysis import current_lists
from app.library.price_lists.importer import synthetic_filename
from app.library.price_lists.reader import PriceListData
from app.models import PriceList

MIN_ROWS, MIN_PRICED = 5, 0.8
RATIO_LOW, RATIO_HIGH = 0.5, 2.0


@dataclass
class Verdict:
    status: str                     # "imported" (meaning: import now), "needs_review" or "duplicate"
    reason: str | None = None


def _current(session: Session, vendor_id: int, warehouse: str):
    return next((p for p in current_lists(session) if p.vendor_id == vendor_id and p.warehouse.value == warehouse), None)


MIN_OVERLAP = 0.3


def median_price_ratio(session: Session, vendor_id: int, warehouse: str, data: PriceListData) -> float | None:
    """Median of new per-vial price / current per-vial price over the products (code and size) in both lists, or None when
    fewer than 30% of the new rows (and fewer than 3) can be matched to the current list."""
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
    if len(ratios) < 3 or len(ratios) < MIN_OVERLAP * len(data.rows):
        return None
    return statistics.median(ratios)


def same_content(session: Session, vendor_id: int, warehouse: str, data: PriceListData) -> bool:
    """Whether the new list has exactly the rows (codes and prices) of the vendor's current list for that warehouse."""
    current = _current(session, vendor_id, warehouse)
    if current is None:
        return False
    old = sorted((i.code or "", round(i.pack_price, 2)) for i in current.items if i.pack_price is not None)
    new = sorted((r.code or "", round(r.pack_price, 2)) for r in data.rows if has_price(r))
    return bool(new) and old == new


def decide(session: Session, *, vendor, enabled: bool, data: PriceListData, warehouse: str, assumed: bool, list_date: date,
           file_dupe: bool) -> Verdict:
    if file_dupe:
        return Verdict("duplicate", "the same file was already received")
    if vendor is None:
        return Verdict("needs_review", "choose the vendor for this group")
    if same_content(session, vendor.id, warehouse, data):
        return Verdict("duplicate", "the same prices as the current list")
    if not enabled:
        return Verdict("needs_review", "this group is not enabled for automatic import")
    if len(data.rows) < MIN_ROWS or priced_fraction(data) < MIN_PRICED:
        return Verdict("needs_review", f"only {len(data.rows)} rows read, or too few have a price")
    if assumed:
        return Verdict("needs_review", "the warehouse was not stated, so China was assumed")
    held = _current(session, vendor.id, warehouse)
    if held is not None and list_date < held.list_date:
        return Verdict("needs_review", f"older than the current list ({held.list_date.isoformat()})")
    if session.scalar(select(PriceList.id).where(PriceList.source_filename == synthetic_filename(vendor.name, warehouse, list_date))) is not None:
        return Verdict("needs_review", "a different list for this vendor, warehouse and date is already stored")
    ratio = median_price_ratio(session, vendor.id, warehouse, data)
    if held is not None and ratio is None:
        return Verdict("needs_review", "too few products match the current list to compare prices")
    if ratio is not None and not (RATIO_LOW <= ratio <= RATIO_HIGH):
        return Verdict("needs_review", f"prices differ a lot from the current list (median x{ratio:.2f}); possibly misread")
    return Verdict("imported")
