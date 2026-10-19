"""Reminders to take body photos. Nothing is stored for them: they are worked out from the photos and protocols, so a new upload replaces the old reminder
by itself. Two kinds: two weeks after the last photo was uploaded, and on every protocol's start date and end date."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.models import BodyPhoto, Protocol, User

INTERVAL_DAYS = 14
MILESTONE_GRACE_DAYS = 3        # a start or end reminder keeps asking this many days, then drops (a photo taken that day, or the day before, satisfies it)
UPLOAD_URL = "/measurements?tab=measurements&add_photo=1#body-photos"


@dataclass(frozen=True)
class Reminder:
    key: str
    kind: str          # "interval" | "start" | "end"
    due: date
    text: str


def _zone(session: Session, uid: int) -> str | None:
    user = session.get(User, uid)
    return user.timezone if user is not None else None


def last_upload(session: Session, uid: int) -> date | None:
    """The day the newest body photo was uploaded, on the person's own calendar."""
    moment = session.scalar(select(BodyPhoto.created_at).where(BodyPhoto.owner_id == uid).order_by(BodyPhoto.created_at.desc()).limit(1))
    return None if moment is None else clock.local_date(moment, _zone(session, uid))


def next_interval_due(session: Session, uid: int) -> date | None:
    """Two weeks after the last upload; None until a photo has been uploaded."""
    uploaded = last_upload(session, uid)
    return None if uploaded is None else uploaded + timedelta(days=INTERVAL_DAYS)


def _milestones(session: Session, uid: int, first: date, last: date) -> list[tuple[str, date, Protocol]]:
    found = []
    for p in session.scalars(select(Protocol).where(Protocol.owner_id == uid, Protocol.paused.is_(False))):          # a paused protocol is off the calendar
        for kind, day in (("start", p.start_date), ("end", p.end_date)):
            if day is not None and first <= day <= last:
                found.append((kind, day, p))
    return found


def _text(kind: str, protocol_name: str | None = None) -> str:
    if kind == "interval":
        return "It has been two weeks since your last body photo. Time to take another."
    verb = "starts" if kind == "start" else "ends"
    return f"{protocol_name} {verb}. Take your body photos."


def active(session: Session, uid: int, today: date) -> list[Reminder]:
    """The reminders to show on `today`."""
    out = []
    due = next_interval_due(session, uid)
    if due is not None and today >= due:
        out.append(Reminder("interval", "interval", due, _text("interval")))
    for kind, day, p in _milestones(session, uid, today - timedelta(days=MILESTONE_GRACE_DAYS), today):
        taken = session.scalar(select(BodyPhoto.id).where(BodyPhoto.owner_id == uid, BodyPhoto.taken_on >= day - timedelta(days=1)).limit(1))
        if taken is None:
            out.append(Reminder(f"{kind}:{p.id}", kind, day, _text(kind, p.name)))
    return out


def calendar_dates(session: Session, uid: int, first: date, last: date) -> dict[date, list[str]]:
    """Days in [first, last] to mark on the calendar, each with what to say: protocol starts and ends, and the day two weeks after the last upload."""
    marks: dict[date, list[str]] = {}
    for kind, day, p in _milestones(session, uid, first, last):
        marks.setdefault(day, []).append(_text(kind, p.name))
    due = next_interval_due(session, uid)
    if due is not None and first <= due <= last:
        marks.setdefault(due, []).append(_text("interval"))
    return marks
