"""Fiber that came from a pill or powder taken as a dose (psyllium husk, say), not from food."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DoseLog, DoseStatus


def by_day(session: Session, uid: int, since: date | None = None, until: date | None = None) -> dict[date, float]:
    """{day: grams of fiber from doses taken that day}; skipped doses add nothing."""
    query = select(DoseLog.scheduled_date, func.sum(DoseLog.fiber_g)).where(
        DoseLog.owner_id == uid, DoseLog.status != DoseStatus.SKIPPED, DoseLog.fiber_g.is_not(None)).group_by(DoseLog.scheduled_date)
    if since is not None:
        query = query.where(DoseLog.scheduled_date >= since)
    if until is not None:
        query = query.where(DoseLog.scheduled_date <= until)
    return {day: grams for day, grams in session.execute(query).all()}
