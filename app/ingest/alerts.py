"""Dashboard alerts the ingest raises: a new price list arrived, and a group stopped being watchable."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import DashboardDismissal, IngestItem, IngestSource, PriceList, User, Vendor, naive_utcnow


def _vendor_name(session: Session, vendor_id: int | None, fallback: str) -> str:
    vendor = session.get(Vendor, vendor_id) if vendor_id else None
    return vendor.name if vendor is not None else fallback


def new_list_alerts(session: Session, user_id: int, now: datetime) -> list[dict]:
    since = now - timedelta(days=config.INGEST_NEW_LIST_DAYS)
    dismissed = set(session.scalars(select(DashboardDismissal.alert_key).where(DashboardDismissal.user_id == user_id)))
    seen, out = set(), []
    rows = session.scalars(select(IngestItem).where(IngestItem.status == "imported", IngestItem.price_list_id.is_not(None),
                                                    IngestItem.decided_at >= since).order_by(IngestItem.decided_at.desc(), IngestItem.id.desc()))
    for item in rows:
        key = f"newlist:{item.price_list_id}"
        if key in dismissed or key in seen or session.get(PriceList, item.price_list_id) is None:
            continue
        seen.add(key)
        name = _vendor_name(session, item.vendor_id, "A vendor")
        out.append({"key": key, "vendor": name, "vendor_id": item.vendor_id, "text": f"{name} released new price list."})
    return out


def group_gone_alerts(session: Session, user: User) -> list[dict]:
    """Shown to the administrator only, for each group that is gone and not yet acknowledged since it went away."""
    if not user.is_admin:
        return []
    out = []
    for s in session.scalars(select(IngestSource).where(IngestSource.state == "gone").order_by(IngestSource.id)):
        if s.alert_acknowledged_at is not None and s.state_changed_at is not None and s.alert_acknowledged_at >= s.state_changed_at:
            continue
        name = _vendor_name(session, s.vendor_id, s.title)
        out.append({"key": f"gone:{s.id}", "source_id": s.id, "text": f"{name} Telegram group is no longer active."})
    return out


def dismiss(session: Session, user: User, key: str) -> None:
    kind, _, ident = key.partition(":")
    if not ident.isdigit():
        raise ValueError("unknown alert")
    if kind == "newlist":
        if session.scalar(select(DashboardDismissal.id).where(DashboardDismissal.user_id == user.id, DashboardDismissal.alert_key == key)) is None:
            session.add(DashboardDismissal(user_id=user.id, alert_key=key))
            session.commit()
    elif kind == "gone" and user.is_admin:
        source = session.get(IngestSource, int(ident))
        if source is not None:
            source.alert_acknowledged_at = naive_utcnow()
            session.commit()
    else:
        raise ValueError("unknown alert")
