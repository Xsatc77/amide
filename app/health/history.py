"""The one read path for the Apple Health data (a later assistant step reads through it) and the daily clean-up of the stored raw files."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import config
from app.models import HealthRaw, HealthReading

INTERVAL = 86400


def _iso(moment: datetime) -> str:
    """Stored times are naive UTC; hand them out as explicit UTC ISO strings."""
    return moment.replace(tzinfo=timezone.utc).isoformat()


def history(session, owner_id: int, start: datetime, end: datetime, metrics: list[str] | None = None) -> list[dict]:
    """The owner's readings that start in [start, end) as plain dicts, oldest first. Naive datetimes are read as UTC."""
    def naive(moment):
        return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment
    query = select(HealthReading).where(HealthReading.owner_id == owner_id, HealthReading.start_at >= naive(start), HealthReading.start_at < naive(end))
    if metrics is not None:
        query = query.where(HealthReading.metric.in_(metrics))
    rows = session.scalars(query.order_by(HealthReading.start_at, HealthReading.id))
    return [{"metric": r.metric, "start": _iso(r.start_at), "end": _iso(r.end_at), "value": r.value, "unit": r.unit, "source": r.source, "extra": r.extra} for r in rows]


def purge_raw(session, today: datetime, keep_days: int = config.HEALTH_RAW_KEEP_DAYS) -> int:
    """Deletes the stored raw files (and their rows) received more than keep_days before today; readings and events stay. Returns how many."""
    if today.tzinfo:
        today = today.astimezone(timezone.utc).replace(tzinfo=None)
    old = list(session.scalars(select(HealthRaw).where(HealthRaw.received_at < today - timedelta(days=keep_days))))
    for raw in old:
        (config.HEALTH_RAW_DIR / raw.file_name).unlink(missing_ok=True)          # a file already gone is fine
        session.delete(raw)
    session.commit()
    return len(old)


def run_purge(session_factory, now: datetime) -> int:
    with session_factory() as session:
        return purge_raw(session, now)


async def _loop():
    from app.db import SessionLocal
    while True:
        try:
            await asyncio.to_thread(run_purge, SessionLocal, datetime.now(timezone.utc))
        except Exception:
            pass                                    # the next day tries again
        await asyncio.sleep(INTERVAL)


def start():
    return asyncio.create_task(_loop())


async def stop(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
