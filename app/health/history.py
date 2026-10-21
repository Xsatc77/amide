"""The one read path for the Apple Health data (a later assistant step reads through it) and the daily clean-up of the stored raw files."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import config
from app.health.series import local_day
from app.models import HealthRaw, HealthReading

log = logging.getLogger(__name__)

INTERVAL = 86400
STRAY_MIN_AGE = 3600          # seconds; a younger file without a row may be a post still being written


def _iso(moment: datetime) -> str:
    """Stored times are naive UTC; hand them out as explicit UTC ISO strings."""
    return moment.replace(tzinfo=timezone.utc).isoformat()


def history(session, owner_id: int, start: datetime, end: datetime, metrics: list[str] | None = None) -> list[dict]:
    """The owner's readings that start in [start, end) as plain dicts, oldest first. Naive datetimes are read as UTC. Each row also
    carries the phone's offset and its local date (the day it counts on); day rows give one row per local day (the newest), even if an
    older version stored a day twice after a trip."""
    def naive(moment):
        return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment
    query = select(HealthReading).where(HealthReading.owner_id == owner_id, HealthReading.start_at >= naive(start), HealthReading.start_at < naive(end))
    if metrics is not None:
        query = query.where(HealthReading.metric.in_(metrics))
    rows = session.scalars(query.order_by(HealthReading.start_at, HealthReading.id)).all()
    newest = {}                                                     # (metric, source, local date) -> id of the day row that stands
    for r in rows:
        if r.grain == "day":
            k = (r.metric, r.source, local_day(r.start_at, r.offset_minutes))
            newest[k] = max(newest.get(k, 0), r.id)
    return [{"metric": r.metric, "start": _iso(r.start_at), "end": _iso(r.end_at), "value": r.value, "unit": r.unit, "source": r.source,
             "extra": r.extra, "offset_minutes": r.offset_minutes, "local_date": local_day(r.start_at, r.offset_minutes).isoformat()}
            for r in rows if r.grain != "day" or newest[(r.metric, r.source, local_day(r.start_at, r.offset_minutes))] == r.id]


def _remove_strays(session) -> None:
    """Deletes files in HEALTH_RAW_DIR that no row owns (left by a deleted account, a restored installation, or a crash mid-post).
    A file younger than STRAY_MIN_AGE is never touched: a post may be writing it right now."""
    directory = config.HEALTH_RAW_DIR
    if not directory.is_dir():
        return
    owned = set(session.scalars(select(HealthRaw.file_name).where(HealthRaw.file_name != "")))
    cutoff = time.time() - STRAY_MIN_AGE
    for path in directory.iterdir():
        try:
            if path.is_file() and path.name not in owned and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            log.warning("could not remove stray health file %s", path.name)


def purge_raw(session, today: datetime, keep_days: int = config.HEALTH_RAW_KEEP_DAYS) -> int:
    """Deletes the stored raw files (and their rows) received more than keep_days before today, and any stray file no row owns;
    readings and events stay. Returns how many rows went."""
    if today.tzinfo:
        today = today.astimezone(timezone.utc).replace(tzinfo=None)
    old = list(session.scalars(select(HealthRaw).where(HealthRaw.received_at < today - timedelta(days=keep_days))))
    for raw in old:
        if raw.file_name:                                                          # a row that kept no file has nothing to delete
            (config.HEALTH_RAW_DIR / raw.file_name).unlink(missing_ok=True)      # a file already gone is fine
        session.delete(raw)
    session.commit()
    _remove_strays(session)
    return len(old)


def run_purge(session_factory, now: datetime) -> int:
    with session_factory() as session:
        return purge_raw(session, now)


def daily_once(session_factory) -> None:
    """One day's work: the purge, then a re-read of exports that failed on our side. A failure is logged and the next day tries again."""
    from app.health import parse
    try:
        run_purge(session_factory, datetime.now(timezone.utc))
    except Exception:
        log.exception("health raw purge failed")
    try:
        parse.run_reprocess(session_factory)
    except Exception:
        log.exception("health reprocess pass failed")


async def _loop():
    from app.db import SessionLocal
    while True:
        await asyncio.to_thread(daily_once, SessionLocal)
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
