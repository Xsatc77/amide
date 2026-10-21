"""Reads a stored Health Auto Export file into clean readings and events. Tolerant: bad content is flagged on the row, never raised."""

import gzip
import hashlib
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import config
from app.health import choices
from app.models import HealthEvent, HealthRaw, HealthReading, naive_utcnow

log = logging.getLogger(__name__)

CHUNK = 5000                       # readings upserted per transaction
FAILED = "Import failed"           # how a note starts when the read failed on our side (see import_failed)
MAX_ATTEMPTS = 5                   # failed reads in a row of one export before it is given up on (see _failed)
GAVE_UP = f"Gave up after {MAX_ATTEMPTS} tries; fix needed"
STALE = timedelta(hours=1)         # a "stored" export this old is no longer being read: a crash stopped it (see reprocess_flagged)

GRAIN_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
# Not established: the exact values of the app's automation-aggregation header; matched case-insensitively, anything else is "point"
_HEADER_GRAIN = {"second": "second", "seconds": "second", "minute": "minute", "minutes": "minute", "hour": "hour", "hours": "hour", "day": "day", "days": "day"}
# payload key -> event kind. Not established: the shapes of all of these but workouts
EVENT_KEYS = {"workouts": "workout", "symptoms": "symptom", "ecg": "ecg", "heart_rate_notifications": "heart_rate_notification",
              "state_of_mind": "state_of_mind", "cycle_tracking": "cycle", "medications": "medication"}
_START_KEYS = ("start", "startDate", "date", "timestamp", "time")
_END_KEYS = ("end", "endDate")
_SPECIAL = {"blood_pressure": ("systolic", {"systolic": "systolic", "diastolic": "diastolic"}), "heart_rate": ("Avg", {"Min": "min", "Max": "max"}),
            "sleep_analysis": ("totalSleep", None)}


def parse_date(text: str) -> tuple[datetime, int]:
    """(naive UTC time, UTC offset in minutes) from "yyyy-MM-dd HH:mm:ss Z"; a bare "yyyy-MM-dd" is local midnight with offset 0."""
    if not isinstance(text, str):
        raise ValueError("date is not text")
    text = text.strip()
    parts = text.split()
    if len(parts) == 3:
        local = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
        sign, digits = parts[2][:1], parts[2][1:]
        if sign not in "+-" or len(digits) != 4 or not digits.isdigit():
            raise ValueError("bad offset")
        offset = (int(digits[:2]) * 60 + int(digits[2:])) * (1 if sign == "+" else -1)
        return local - timedelta(minutes=offset), offset
    if len(parts) == 1 and len(text) == 10:
        return datetime.strptime(text, "%Y-%m-%d"), 0
    parsed = datetime.fromisoformat(text)                     # Not established: whether any other date form turns up; ISO is a safe extra
    if parsed.tzinfo is None:
        return parsed, 0
    return parsed.astimezone(timezone.utc).replace(tzinfo=None), int(parsed.utcoffset().total_seconds() // 60)


def _qty(value) -> float:
    """A finite number from a number or a numeric string; anything else (None, "null", booleans) raises ValueError."""
    if isinstance(value, bool) or value is None:
        raise ValueError("no number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


def _grain(raw: HealthRaw) -> str | None:
    """The grain the automation-aggregation header names; None when there is no such header (see _row_grain)."""
    headers = raw.headers if isinstance(raw.headers, dict) else {}
    value = str(headers.get("automation-aggregation") or "").strip().lower()
    return _HEADER_GRAIN.get(value, "point") if value else None


def _row_grain(header_grain: str | None, text) -> str:
    """Without the header, a date-only value ("yyyy-MM-dd") is a whole day; anything else is a single sample."""
    if header_grain is not None:
        return header_grain
    return "day" if isinstance(text, str) and len(text.strip()) == 10 else "point"


def local_day(item) -> date | None:
    """The phone's own calendar day for an event, from the offset carried by its first readable start date (a bare "yyyy-MM-dd" is
    that day itself). None when that date carries no offset, so the caller falls back to a zone of its own."""
    if not isinstance(item, dict):
        return None
    for key in _START_KEYS:
        text = item.get(key)
        if not isinstance(text, str):
            continue
        try:
            moment, offset = parse_date(text)
        except ValueError:
            continue
        text = text.strip()
        if len(text.split()) == 3 or len(text) == 10 or datetime.fromisoformat(text).tzinfo is not None:
            return (moment + timedelta(minutes=offset)).date()
        return None
    return None


def _day_key(start: datetime, offset: int | None, source: str) -> tuple:
    """A day row's identity is its local date, not its UTC start: the same day re-sent from another zone starts at another UTC time."""
    return (start + timedelta(minutes=offset or 0)).date(), source


def _reading_fields(name: str, item: dict) -> tuple[float, dict | None]:
    """(value, extra) for one data row, by the metric's documented shape."""
    special = _SPECIAL.get(name)
    if special:
        main, extras = special
        value = _qty(item.get(main))
        if name == "sleep_analysis":
            skip = {"date", "source", main}
            extra = {k: v for k, v in item.items() if k not in skip} or None      # Not established: the stage field names; keep them all as sent
        else:
            extra = {out: item.get(key) for key, out in extras.items()}
        return value, extra
    value = _qty(item["qty"] if "qty" in item else item.get("value"))               # handwashing and toothbrushing send "value"
    extra = {k: v for k, v in item.items() if k not in ("qty", "value", "date", "source")} or None      # e.g. blood glucose mealTime
    return value, extra


def _event_id(item: dict) -> str:
    if item.get("id") not in (None, "") and len(str(item["id"])) <= 120:         # a too-long id is hashed rather than cut, so two long ids cannot collide
        return str(item["id"])
    return hashlib.sha1(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()


def _first_date(item: dict, keys, fallback):
    for key in keys:
        if isinstance(item.get(key), str):
            try:
                return parse_date(item[key])[0]
            except ValueError:
                continue
    return fallback


def _load(raw: HealthRaw):
    path = config.HEALTH_RAW_DIR / raw.file_name
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    return json.loads(text)


def _import_readings(session: Session, raw: HealthRaw, metrics: list, header_grain: str | None, skipped: list[str]) -> int:
    """Upserts each metric's rows CHUNK at a time, committing after every chunk, so a big export never holds the database's write
    lock for long. A failure part way leaves the earlier chunks in place, which is safe: every row upserts on its key, so a re-read
    of the same export (a resend, or the daily reprocess pass) simply finishes the job."""
    written = 0
    for metric in metrics:
        if not isinstance(metric, dict) or not isinstance(metric.get("name"), str) or not isinstance(metric.get("data"), list):
            skipped.append("metric entry")
            continue
        name, unit = metric["name"][:80], (metric["units"][:30] if isinstance(metric.get("units"), str) else None)
        by_grain: dict[str, list] = {}                            # grain -> (start, offset, source, value, extra) for the rows that parse
        for item in metric["data"]:
            try:
                if not isinstance(item, dict):
                    raise ValueError("row is not an object")
                value, extra = _reading_fields(metric["name"], item)
                start, offset = parse_date(item.get("date"))
            except (ValueError, TypeError, KeyError):
                skipped.append(name)
                continue
            source = item.get("source") if isinstance(item.get("source"), str) else ""
            by_grain.setdefault(_row_grain(header_grain, item.get("date")), []).append((start, offset, source[:120], value, extra))
        if not by_grain:
            continue
        choices.note_received(session, raw.owner_id, {name})            # a new metric becomes a pending choice for the popup
        for grain, rows in by_grain.items():
            written += _upsert(session, raw.owner_id, name, unit, grain, rows, skipped)
    return written


def _upsert(session: Session, owner_id: int, name: str, unit: str | None, grain: str, rows: list, skipped: list[str]) -> int:
    seconds, daily = GRAIN_SECONDS.get(grain, 0), grain == "day"
    rows.sort(key=lambda r: r[0])
    written = 0
    for n in range(0, len(rows), CHUNK):
        chunk = rows[n:n + CHUNK]
        # Not established: how Batch Requests splits a payload. Overlapping or split batches are safe because every row upserts on its key.
        # Only the stored rows inside this chunk's time span are loaded, never the person's whole history; a day row is matched by its
        # local date (see _day_key), and the same local day can start up to a day apart in UTC, hence the wider span for those.
        low, high = chunk[0][0], chunk[-1][0]
        if daily:
            low, high = low - timedelta(days=2), high + timedelta(days=2)
        stored = session.scalars(select(HealthReading).where(
            HealthReading.owner_id == owner_id, HealthReading.metric == name, HealthReading.grain == grain,
            HealthReading.start_at >= low, HealthReading.start_at <= high).order_by(HealthReading.id)).all()
        at_start = {(r.start_at, r.source): r for r in stored}            # what the unique key already holds
        same_day: dict[tuple, list[HealthReading]] = {}
        for r in stored:
            same_day.setdefault(_day_key(r.start_at, r.offset_minutes, r.source), []).append(r)
        for start, offset, source, value, extra in chunk:
            if daily:
                reading = _day_row(session, same_day, at_start, start, offset, source)
                if reading is False:                                     # see _day_row: the start belongs to another local day
                    skipped.append("day-key collision skipped")
                    continue
            else:
                reading = at_start.get((start, source))
            if reading is None:
                reading = HealthReading(owner_id=owner_id, metric=name, grain=grain, source=source)
                session.add(reading)
                if daily:
                    same_day[_day_key(start, offset, source)] = [reading]
            at_start.pop((reading.start_at, source), None)
            at_start[(start, source)] = reading
            # a day re-sent from another zone moves to its new UTC start and offset rather than being stored a second time
            reading.start_at, reading.end_at, reading.offset_minutes = start, start + timedelta(seconds=seconds), offset
            reading.value, reading.unit, reading.extra = value, unit, extra
            written += 1
        session.commit()
    return written


def _day_row(session: Session, same_day: dict, at_start: dict, start: datetime, offset: int, source: str):
    """The stored day row an incoming day reading updates: None for a new one, False when it must be skipped. Old twins of the same local
    day (an older version stored a trip's day twice) are merged: the one already at the incoming start is kept, else the newest, and the
    rest are deleted. Two different local days can share one UTC start when the offsets differ by exactly 24 hours (-1000 and +1400, say);
    the row already there is kept as it is and the incoming one skipped, so the unique key never fails the whole chunk."""
    twins = same_day.get(_day_key(start, offset, source), [])
    if twins:
        keep = next((r for r in twins if r.start_at == start), twins[-1])
        for other in twins:
            if other is not keep:
                at_start.pop((other.start_at, source), None)
                session.delete(other)
        if len(twins) > 1:
            session.flush()                                              # the deletes first, or the move below could meet the twin's key
        same_day[_day_key(start, offset, source)] = [keep]
    else:
        keep = None
    holder = at_start.get((start, source))
    if holder is not None and holder is not keep:
        return False
    return keep


def _import_events(session: Session, raw: HealthRaw, payload: dict, skipped: list[str]) -> None:
    for key, kind in EVENT_KEYS.items():
        items = payload.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            skipped.append(key)
            continue
        ids = [_event_id(i) for i in items if isinstance(i, dict)]
        existing = {}                                                     # only the events named in this batch, looked up in chunks
        for n in range(0, len(ids), 500):
            existing.update({e.external_id: e for e in session.scalars(select(HealthEvent).where(
                HealthEvent.owner_id == raw.owner_id, HealthEvent.kind == kind, HealthEvent.external_id.in_(ids[n:n + 500])))})
        for item in items:
            if not isinstance(item, dict):
                skipped.append(key)
                continue
            external_id = _event_id(item)
            start = _first_date(item, _START_KEYS, raw.received_at)           # Not established: where non-workout items keep their time
            end = _first_date(item, _END_KEYS, None)
            event = existing.get(external_id)
            if event is None:
                event = existing[external_id] = HealthEvent(owner_id=raw.owner_id, kind=kind, external_id=external_id)
                session.add(event)
            name = item.get("name")
            event.start_at, event.end_at, event.name, event.payload = start, end, name[:120] if isinstance(name, str) else None, item
        if kind in choices.EVENT_KINDS and any(isinstance(i, dict) for i in items):
            choices.note_received(session, raw.owner_id, {f"event:{kind}"})
        session.commit()                                                  # one kind at a time, like the metrics


def import_failed(raw: HealthRaw) -> bool:
    """True when the last read of this export failed on our side (an error, not bad content), so trying again may well work."""
    return raw.status == "flagged" and (raw.note or "").startswith(FAILED)


def import_raw(session: Session, raw: HealthRaw) -> int:
    """Reads the stored file into readings and events, committing a chunk at a time (see _import_readings); returns the readings
    written. Never raises: bad content is flagged with a note, and an unexpected error is flagged with a note starting FAILED
    (GAVE_UP once it has failed MAX_ATTEMPTS times, see _failed)."""
    if not raw.file_name:                                            # no copy was kept (not JSON, or removed for the quota): nothing to read
        return raw.readings_count or 0
    if raw not in session:
        raw = session.merge(raw)
    if (raw.note or "").startswith(GAVE_UP):                         # a resend of an export given up on gets a fresh MAX_ATTEMPTS tries
        raw.attempts = 0
    raw.attempts = (raw.attempts or 0) + 1                           # counted before the read, so a crash part way counts too
    before = raw.status                                              # see _failed: only a row finished during this read is a twin's work
    session.commit()
    try:
        try:
            payload = _load(raw)
        except OSError as exc:                                       # the stored copy cannot be opened: a fault here, not in the export
            return _failed(session, raw, f"could not open the stored file ({type(exc).__name__})", before)
        except (EOFError, ValueError) as exc:                        # bad gzip, bad UTF-8, bad JSON
            return _finish(session, raw, 0, "flagged", f"Could not read the file: {type(exc).__name__}")
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]                                    # Not established: whether the app wraps its arrays in "data"
        if not isinstance(payload, dict) or not any(k in payload for k in ("metrics", *EVENT_KEYS)):
            return _finish(session, raw, 0, "flagged", "Nothing recognised in this file")
        skipped: list[str] = []
        metrics = payload.get("metrics", [])
        if not isinstance(metrics, list):
            skipped.append("metrics")
            metrics = []
        skipped += [f"unknown: {k}" for k, v in payload.items() if k != "metrics" and k not in EVENT_KEYS and isinstance(v, (list, dict))]   # kept raw, flagged for a look
        written = _import_readings(session, raw, metrics, _grain(raw), skipped)
        _import_events(session, raw, payload, skipped)
        note = f"{len(skipped)} item(s) skipped: {', '.join(sorted(set(skipped)))}"[:300] if skipped else None
        return _finish(session, raw, written, "flagged" if skipped else "parsed", note)
    except Exception as exc:                                             # anything unforeseen: undo the unfinished chunk, keep it flagged for a retry
        log.exception("health import failed for raw %s", raw.id)
        session.rollback()
        return _failed(session, raw, type(exc).__name__, before)


def _failed(session: Session, raw: HealthRaw, detail: str, before: str | None) -> int:
    """Records a failure on our side. A concurrent twin import of the same export may have finished meanwhile (the row turned "parsed"
    after this read began, `before`): its "parsed" row is never written over. After MAX_ATTEMPTS failed reads in a row (attempts counts them: _finish resets it on any other outcome) the note says it
    was given up on (no longer FAILED), so the phone gets a 200 and moves on and the daily pass stops; a resend of the same export starts
    a fresh count (see import_raw)."""
    if before != "parsed" and session.scalar(select(HealthRaw.status).where(HealthRaw.id == raw.id)) == "parsed":
        session.refresh(raw)
        return raw.readings_count or 0
    if (raw.attempts or 0) >= MAX_ATTEMPTS:
        return _finish(session, raw, 0, "flagged", f"{GAVE_UP} ({detail})")
    return _finish(session, raw, 0, "flagged", f"{FAILED}: {detail}")


def _finish(session: Session, raw: HealthRaw, count: int, status: str, note: str | None) -> int:
    raw = session.merge(raw) if raw not in session else raw
    raw.readings_count, raw.status, raw.note = count, status, note
    if not (note or "").startswith((FAILED, GAVE_UP)):                # read through (parsed, or flagged for its content): no failures in a row
        raw.attempts = 0
    session.commit()
    return count


def reprocess_flagged(session: Session, limit: int = 20) -> int:
    """Re-reads up to `limit` exports whose stored copy is still on disk and whose last read failed on our side (not those that merely had
    items skipped), or that a crash left "stored" for over STALE; returns how many were re-read. Run by the daily loop, so a passing fault
    does not leave an export half read."""
    stale = naive_utcnow() - STALE
    rows = session.scalars(select(HealthRaw).where(HealthRaw.file_name != "", or_(
        and_(HealthRaw.status == "flagged", HealthRaw.note.startswith(FAILED)),
        and_(HealthRaw.status == "stored", HealthRaw.received_at < stale))).order_by(HealthRaw.id)).all()
    done = 0
    for raw in rows:
        if done >= limit:
            break
        if not (config.HEALTH_RAW_DIR / raw.file_name).exists():      # one whose copy is lost is passed over, so it cannot use up the day's turns
            continue
        if raw.status == "stored" and (raw.attempts or 0) >= MAX_ATTEMPTS:      # its reads keep stopping the process: not tried again by itself
            _finish(session, raw, raw.readings_count or 0, "flagged", f"{GAVE_UP} (the read never finished)")
            continue
        import_raw(session, raw)
        done += 1
    return done


def run_reprocess(session_factory) -> int:
    with session_factory() as session:
        return reprocess_flagged(session)
