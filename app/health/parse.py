"""Reads a stored Health Auto Export file into clean readings and events. Tolerant: bad content is flagged on the row, never raised."""

import gzip
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.health import choices
from app.models import HealthEvent, HealthRaw, HealthReading

log = logging.getLogger(__name__)

CHUNK = 5000                       # readings upserted per transaction
FAILED = "Import failed"           # how a note starts when the read failed on our side (see import_failed)

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


def _grain(raw: HealthRaw) -> str:
    headers = raw.headers if isinstance(raw.headers, dict) else {}
    return _HEADER_GRAIN.get(str(headers.get("automation-aggregation", "")).strip().lower(), "point")


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


def _import_readings(session: Session, raw: HealthRaw, metrics: list, grain: str, skipped: list[str]) -> int:
    """Upserts each metric's rows CHUNK at a time, committing after every chunk, so a big export never holds the database's write
    lock for long. A failure part way leaves the earlier chunks in place, which is safe: every row upserts on its key, so a re-read
    of the same export (a resend, or the daily reprocess pass) simply finishes the job."""
    written = 0
    seconds = GRAIN_SECONDS.get(grain, 0)
    for metric in metrics:
        if not isinstance(metric, dict) or not isinstance(metric.get("name"), str) or not isinstance(metric.get("data"), list):
            skipped.append("metric entry")
            continue
        name, unit = metric["name"][:80], (metric["units"][:30] if isinstance(metric.get("units"), str) else None)
        rows = []                                                 # (key, start, offset, value, extra) for the rows that parse
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
            rows.append(((start, grain, source[:120]), start, offset, value, extra))
        if not rows:
            continue
        choices.note_received(session, raw.owner_id, {name})            # a new metric becomes a pending choice for the popup
        rows.sort(key=lambda r: r[1])
        for n in range(0, len(rows), CHUNK):
            chunk = rows[n:n + CHUNK]
            # Not established: how Batch Requests splits a payload. Overlapping or split batches are safe because every row upserts on its key.
            # Only the stored rows inside this chunk's time span are loaded, never the person's whole history.
            low, high = chunk[0][1], chunk[-1][1]
            existing = {(r.start_at, r.grain, r.source): r for r in session.scalars(select(HealthReading).where(
                HealthReading.owner_id == raw.owner_id, HealthReading.metric == name, HealthReading.grain == grain,
                HealthReading.start_at >= low, HealthReading.start_at <= high))}
            for key, start, offset, value, extra in chunk:
                reading = existing.get(key)
                if reading is None:
                    reading = existing[key] = HealthReading(owner_id=raw.owner_id, metric=name, start_at=start, grain=grain, source=key[2])
                    session.add(reading)
                reading.end_at, reading.offset_minutes, reading.value, reading.unit, reading.extra = start + timedelta(seconds=seconds), offset, value, unit, extra
                written += 1
            session.commit()
    return written


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
    written. Never raises: bad content is flagged with a note, and an unexpected error is flagged with a note starting FAILED."""
    if not raw.file_name:                                            # no copy was kept (not JSON, or removed for the quota): nothing to read
        return raw.readings_count or 0
    try:
        try:
            payload = _load(raw)
        except OSError as exc:                                       # the stored copy cannot be opened: a fault here, not in the export
            return _finish(session, raw, 0, "flagged", f"{FAILED}: could not open the stored file ({type(exc).__name__})")
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
        return _finish(session, raw, 0, "flagged", f"{FAILED}: {type(exc).__name__}")


def _finish(session: Session, raw: HealthRaw, count: int, status: str, note: str | None) -> int:
    raw = session.merge(raw) if raw not in session else raw
    raw.readings_count, raw.status, raw.note = count, status, note
    session.commit()
    return count


def reprocess_flagged(session: Session, limit: int = 20) -> int:
    """Re-reads up to `limit` exports whose last read failed on our side (not those that merely had items skipped) and whose stored copy
    is still on disk; returns how many were re-read. Run by the daily loop, so a passing fault does not leave an export half read."""
    rows = session.scalars(select(HealthRaw).where(HealthRaw.status == "flagged", HealthRaw.file_name != "", HealthRaw.note.startswith(FAILED))
                           .order_by(HealthRaw.id)).all()
    done = 0
    for raw in rows:
        if done >= limit:
            break
        if (config.HEALTH_RAW_DIR / raw.file_name).exists():      # one whose copy is lost is passed over, so it cannot use up the day's turns
            import_raw(session, raw)
            done += 1
    return done


def run_reprocess(session_factory) -> int:
    with session_factory() as session:
        return reprocess_flagged(session)
