"""Resolve a typed vendor name to a Vendor row: reuse the existing one (case-insensitively,
shared across every user, like the peptide library), or create it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Vendor


def resolve_vendor(session: Session, created_by_id: int, name: str) -> Vendor | None:
    """None when `name` is blank (meaning: no vendor). Otherwise the existing or newly created Vendor."""
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(Vendor).where(Vendor.name == name))
    if existing is not None:
        return existing
    vendor = Vendor(created_by_id=created_by_id, name=name)
    session.add(vendor)
    session.flush()
    return vendor
