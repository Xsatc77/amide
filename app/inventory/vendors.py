"""Resolve a typed vendor name to a Vendor row: reuse an existing one (case-insensitively, per owner),
or create it. Mirrors the protocol builder's "pick or create a peptide by name" pattern.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Vendor


def resolve_vendor(session: Session, owner_id: int, name: str) -> Vendor | None:
    """None when `name` is blank (meaning: no vendor). Otherwise the existing or newly created Vendor."""
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(Vendor).where(Vendor.owner_id == owner_id, Vendor.name == name))
    if existing is not None:
        return existing
    vendor = Vendor(owner_id=owner_id, name=name)
    session.add(vendor)
    session.flush()
    return vendor
