"""Apple Health readings turned into one value per local day, and everything the Apple tab shows."""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.health import choices, parse
from app.models import HealthEvent, HealthReading, naive_utcnow

# Metrics that add up over a day (all nutrition, and activity energy, time, distance and counts); every other metric shows the day's
# last value. Any "dietary_" name adds up too (see is_cumulative). Not established: the full metric-name list.
CUMULATIVE = frozenset({
    "step_count", "walking_running_distance", "flights_climbed", "active_energy", "basal_energy_burned", "apple_exercise_time",
    "apple_stand_time", "apple_stand_hour", "apple_move_time", "cycling_distance", "swimming_distance", "wheelchair_distance",
    "distance_downhill_snow_sports", "swim_stroke_count", "push_count", "number_of_times_fallen", "time_in_daylight",
    "dietary_water", "dietary_energy", "dietary_sugar", "dietary_caffeine", "dietary_sodium", "dietary_cholesterol", "dietary_potassium",
    "dietary_calcium", "dietary_iron", "dietary_fat_saturated", "dietary_fat_total", "dietary_fiber", "dietary_protein", "dietary_carbohydrates",
    "protein", "carbohydrates", "total_fat", "fiber", "caffeine", "sodium", "sugar", "saturated_fat", "monounsaturated_fat",
    "polyunsaturated_fat", "cholesterol", "potassium", "calcium", "iron", "magnesium", "zinc", "vitamin_c", "vitamin_d",
})


def is_cumulative(metric: str) -> bool:
    return metric in CUMULATIVE or metric.startswith("dietary_")


# Finest first. "point" (a single timed sample) counts as the finest of all.
_GRAIN_RANK = {"point": 0, "second": 1, "minute": 2, "hour": 3, "day": 4}
LIST_LIMIT = 100


def daily_series(session: Session, owner_id: int, metric: str, start: date, end: date, zone: str | None) -> list[tuple[date, float]]:
    """(day, value) for each day from `start` to `end` that has data. A reading belongs to the phone's own day when it was taken (its
    stored UTC time plus its stored offset), so the viewer's zone (`zone`, kept for the callers) does not move it. A day uses only the
    finest grain stored for it (two grains are never added together); cumulative metrics sum that day's readings from one source (see
    below), the rest take the last one."""
    low, high = datetime.combine(start - timedelta(days=2), datetime.min.time()), datetime.combine(end + timedelta(days=3), datetime.min.time())
    rows = session.execute(select(HealthReading.id, HealthReading.start_at, HealthReading.offset_minutes, HealthReading.grain, HealthReading.source,
                                  HealthReading.value).where(
        HealthReading.owner_id == owner_id, HealthReading.metric == metric, HealthReading.value.is_not(None),
        HealthReading.start_at >= low, HealthReading.start_at < high).order_by(HealthReading.start_at, HealthReading.id)).all()
    by_day: dict[date, list[tuple[str, str, float]]] = defaultdict(list)
    day_rows: dict[tuple[date, str], tuple[int, float]] = {}     # (day, source) -> (id, value): one day row per local day, the newest
    for rid, at, offset, grain, source, value in rows:
        day = local_day(at, offset)
        if not start <= day <= end:
            continue
        if grain == "day":                                       # an older version could store one local day twice after a trip
            if (day, source) not in day_rows or rid > day_rows[(day, source)][0]:
                day_rows[(day, source)] = (rid, value)
            continue
        by_day[day].append((grain, source, value))
    for (day, source), (_, value) in day_rows.items():
        by_day[day].append(("day", source, value))
    cumulative = is_cumulative(metric)
    out = []
    for day in sorted(by_day):
        finest = min(_GRAIN_RANK.get(g, 5) for g, _, _ in by_day[day])
        values = [(src, v) for g, src, v in by_day[day] if _GRAIN_RANK.get(g, 5) == finest]
        if cumulative:
            # Not established: how the real export labels sources. When two sources (the phone and another app, say) both report a day,
            # adding them would count it twice, so only the source with the largest total that day is used.
            totals: dict[str, float] = defaultdict(float)
            for src, v in values:
                totals[src] += v
            out.append((day, float(max(totals.values()))))
        else:
            out.append((day, float(values[-1][1])))
    return out


def local_day(at: datetime, offset: int | None) -> date:
    """The phone's own calendar day for a reading: its stored UTC start plus the offset the phone had then."""
    return (at + timedelta(minutes=offset or 0)).date()


def event_day(event: HealthEvent, zone: str | None) -> date:
    """The phone's own day for an event, read from the offset in the date string kept in its payload; only a date without one falls
    back to the viewer's zone. Events store UTC alone, so the payload is where the phone's offset survives."""
    return parse.local_day(event.payload) or clock.local_date(event.start_at, zone)


def _round(value: float) -> float:
    return round(value, 1) if abs(value) < 1000 else round(value)


def _workout(event: HealthEvent, zone: str | None) -> dict:
    payload = event.payload if isinstance(event.payload, dict) else {}

    def quantity(key):
        item = payload.get(key)
        if isinstance(item, dict) and isinstance(item.get("qty"), (int, float)) and not isinstance(item.get("qty"), bool) and math.isfinite(item["qty"]):
            return item["qty"], item.get("units") or ""
        return None, ""

    seconds = payload.get("duration")             # Not established: the unit of the app's workout duration; seconds is its documented one
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        seconds = (event.end_at - event.start_at).total_seconds() if event.end_at else None
    distance, distance_unit = quantity("distance")
    energy, energy_unit = quantity("activeEnergyBurned")
    return {"name": event.name or "Workout", "start": event.start_at, "day": event_day(event, zone),
            "minutes": round(seconds / 60) if seconds else None,
            "distance": _round(distance) if distance is not None else None, "distance_unit": distance_unit,
            "energy": _round(energy) if energy is not None else None, "energy_unit": energy_unit}


def _dated(session: Session, owner_id: int, kind: str, since: date | None, zone: str | None) -> list[dict]:
    query = select(HealthEvent).where(HealthEvent.owner_id == owner_id, HealthEvent.kind == kind)
    if since is not None:
        query = query.where(HealthEvent.start_at >= datetime.combine(since - timedelta(days=1), datetime.min.time()))
    events = session.scalars(query.order_by(HealthEvent.start_at.desc(), HealthEvent.id.desc()).limit(LIST_LIMIT * 2)).all()
    rows = [{"name": e.name or choices.label_for(f"event:{kind}"), "start": e.start_at, "day": event_day(e, zone)} for e in events]
    return [r for r in rows if since is None or r["day"] >= since][:LIST_LIMIT]


def apple_tab_data(session: Session, owner_id: int, range_param: str | None, zone: str | None) -> dict:
    """The Apple tab: a chart for every metric the person switched on, today's and the week's figures, and the workout, symptom and
    state-of-mind lists, all inside the chosen range. Never touches the person's own measurements."""
    from app.routers import measurements as body          # the page's own range choices and chart geometry
    today = clock.local_date(naive_utcnow(), zone)
    days = body.RANGE_DAYS.get(range_param if range_param in body.RANGES else body.DEFAULT_RANGE)
    since = today - timedelta(days=days) if days is not None else None
    fetch_from = min(since, today - timedelta(days=6)) if since else date(1970, 1, 1)
    on = choices.enabled_metrics(session, owner_id)
    metrics = []
    for item in choices.received_metrics(session, owner_id):
        key = item["metric"]
        if key.startswith("event:") or key not in on:
            continue
        daily = daily_series(session, owner_id, key, fetch_from, today, zone)
        shown = [(d, v) for d, v in daily if since is None or d >= since]
        week = [v for d, v in daily if d > today - timedelta(days=7)]
        cumulative = is_cumulative(key)
        today_value = next((v for d, v in daily if d == today), None)
        metrics.append({
            "metric": key, "label": item["label"], "unit": item["unit"], "cumulative": cumulative, "series": shown,
            "chart": body._chart(shown), "today": _round(today_value) if today_value is not None else None,
            "week": _round(sum(week) if cumulative else sum(week) / len(week)) if week else None,
            "week_kind": "total" if cumulative else "average"})
    workouts = [_workout(e, zone) for e in session.scalars(
        select(HealthEvent).where(HealthEvent.owner_id == owner_id, HealthEvent.kind == "workout").order_by(HealthEvent.start_at.desc(), HealthEvent.id.desc()).limit(LIST_LIMIT * 2))]
    workouts = [w for w in workouts if since is None or w["day"] >= since][:LIST_LIMIT]
    return {"metrics": metrics, "workouts": workouts, "today": today,
            "symptoms": _dated(session, owner_id, "symptom", since, zone) if "event:symptom" in on else [],
            "state_of_mind": _dated(session, owner_id, "state_of_mind", since, zone) if "event:state_of_mind" in on else []}
