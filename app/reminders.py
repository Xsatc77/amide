"""Dose reminders as ntfy push messages (https://ntfy.sh, or a server you run). Opt-in per user, and the only call Amide makes to the outside
on its own: it posts a short message to the topic the user chose when a scheduled dose's time of day has come and the dose is not logged."""

import asyncio
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import config
from app.calendar.ical import SLOT_TIMES
from app.calendar.schedule import occurrences
from app.models import DoseLog, DoseReminder, Protocol, ProtocolItem, User

GRACE = timedelta(hours=3)          # a reminder is only sent within this long after its time, so starting Amide late never floods the phone
INTERVAL = 60


def send_ntfy(server: str, topic: str, title: str, message: str) -> None:
    request = urllib.request.Request(f"{server.rstrip('/')}/{topic}", data=message.encode("utf-8"), method="POST",
                                     headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "pill"})
    with urllib.request.urlopen(request, timeout=10):
        pass


def _zone(user: User):
    try:
        return ZoneInfo(user.timezone) if user.timezone else timezone.utc
    except Exception:
        return timezone.utc


def run_once(session_factory, now_utc: datetime, send=None) -> int:
    """Send every reminder that is due now; returns how many were sent. A failed send is simply tried again next round."""
    send = send or send_ntfy
    sent = 0
    with session_factory() as session:
        for user in session.scalars(select(User).where(User.ntfy_enabled.is_(True), User.ntfy_topic.is_not(None))):
            local = now_utc.astimezone(_zone(user))
            today = local.date()
            protocols = session.scalars(select(Protocol).where(Protocol.owner_id == user.id).options(
                selectinload(Protocol.items).selectinload(ProtocolItem.peptide), selectinload(Protocol.items).selectinload(ProtocolItem.steps),
                selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs))).all()
            logged = set(session.scalars(select(DoseLog.protocol_item_id).where(DoseLog.owner_id == user.id, DoseLog.scheduled_date == today)))
            reminded = set(session.scalars(select(DoseReminder.protocol_item_id).where(DoseReminder.user_id == user.id, DoseReminder.for_date == today)))
            for occ in occurrences(protocols, today, today):
                for item in occ.items:
                    if item.protocol_item_id in logged or item.protocol_item_id in reminded or item.time_of_day not in SLOT_TIMES:
                        continue
                    hour, minute = SLOT_TIMES[item.time_of_day]
                    due_at = datetime(today.year, today.month, today.day, hour, minute, tzinfo=local.tzinfo)
                    if not due_at <= local < due_at + GRACE:
                        continue
                    dose = f"{item.dose:g} {item.unit}" if item.dose is not None else "dose not set"
                    try:
                        send(config.NTFY_SERVER, user.ntfy_topic, f"Time for {item.peptide}", f"{dose} ({item.time_of_day.label}) - {occ.protocol_name}")
                    except Exception:
                        continue
                    session.add(DoseReminder(user_id=user.id, protocol_item_id=item.protocol_item_id, for_date=today))
                    session.commit()
                    sent += 1
    return sent


async def _loop():
    while True:
        try:
            from app.db import SessionLocal
            await asyncio.to_thread(run_once, SessionLocal, datetime.now(timezone.utc))
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)


def start():
    return asyncio.create_task(_loop()) if config.REMINDERS_ENABLED else None


async def stop(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
