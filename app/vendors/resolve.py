"""Resolve a typed method-type name to a row: reuse the existing one (case-insensitively --
`ContactMethodType.name`/`PaymentMethodType.name` use the same NOCASE column collation as
`Vendor.name`, so a plain `==` comparison is already case-insensitive), or create it. Mirrors
`resolve_vendor` in `app/inventory/vendors.py` exactly.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContactMethodType, PaymentMethodType


def resolve_contact_method_type(session: Session, name: str) -> ContactMethodType | None:
    """None when `name` is blank (meaning: no contact method type). Otherwise the existing or
    newly created ContactMethodType."""
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(ContactMethodType).where(ContactMethodType.name == name))
    if existing is not None:
        return existing
    method_type = ContactMethodType(name=name)
    session.add(method_type)
    session.flush()
    return method_type


def resolve_payment_method_type(session: Session, name: str) -> PaymentMethodType | None:
    """None when `name` is blank (meaning: no payment method type). Otherwise the existing or
    newly created PaymentMethodType."""
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(PaymentMethodType).where(PaymentMethodType.name == name))
    if existing is not None:
        return existing
    method_type = PaymentMethodType(name=name)
    session.add(method_type)
    session.flush()
    return method_type
