"""Which Apple Health metrics a person wants shown: what has arrived, what is still undecided, and the saved yes/no per metric."""

from sqlalchemy import exists, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.titles import title_case
from app.health.groups import RECOMMENDED, SECTIONS, metric_group
from app.models import HealthEvent, HealthMetricChoice, HealthReading, IngestToken, naive_utcnow

# Event kinds are offered next to the numeric metrics under the key "event:<kind>".
EVENT_KINDS: dict[str, str] = {"symptom": "Symptoms", "state_of_mind": "State of mind"}

# Friendly names for the metrics people are likely to see; a name not listed gets a readable one derived from it (see label_for).
LABELS: dict[str, str] = {
    "step_count": "Steps", "walking_running_distance": "Walking and running distance", "flights_climbed": "Flights climbed",
    "active_energy": "Active energy", "basal_energy_burned": "Resting energy", "apple_exercise_time": "Exercise minutes",
    "apple_stand_time": "Stand minutes", "apple_stand_hour": "Stand hours", "walking_speed": "Walking speed",
    "walking_step_length": "Walking step length", "walking_asymmetry_percentage": "Walking asymmetry",
    "walking_double_support_percentage": "Walking double support", "stair_speed_up": "Stair speed up", "stair_speed_down": "Stair speed down",
    "weight_&_body_mass": "Weight", "weight_body_mass": "Weight", "body_fat_percentage": "Body fat", "lean_body_mass": "Lean body mass", "body_mass_index": "BMI",
    "dietary_water": "Water", "dietary_energy": "Dietary energy", "protein": "Protein", "carbohydrates": "Carbohydrates",
    "total_fat": "Fat", "fiber": "Fiber", "dietary_sugar": "Sugar", "sleep_analysis": "Sleep analysis",
    "heart_rate": "Heart rate", "resting_heart_rate": "Resting heart rate", "walking_heart_rate_average": "Walking heart rate",
    "heart_rate_variability": "Heart rate variability", "blood_pressure": "Blood pressure", "blood_oxygen_saturation": "Blood oxygen",
    "respiratory_rate": "Breathing rate", "body_temperature": "Body temperature", "blood_glucose": "Blood glucose", "vo2_max": "VO2 max",
    "cardio_recovery": "Cardio recovery", "apple_sleeping_wrist_temperature": "Sleeping wrist temperature",
}


# How a stored unit is shown: "count" is no unit at all, and a few of Apple's spellings read badly as they are.
UNIT_LABELS: dict[str, str] = {"count": "", "fl_oz_us": "fl oz", "fl_oz_imp": "fl oz (UK)"}


def unit_label(unit: str | None) -> str:
    unit = unit or ""
    return UNIT_LABELS.get(unit, unit.replace("_", " "))


def label_for(metric: str) -> str:
    """A person-readable name: the friendly one if known, otherwise the raw name with underscores opened out."""
    if metric.startswith("event:"):
        return EVENT_KINDS.get(metric[6:], label_for(metric[6:]))
    if metric in LABELS:
        return title_case(LABELS[metric])
    return title_case(metric.replace("_", " ").replace("&", "and").strip())


def note_received(session: Session, owner_id: int, keys: set[str]) -> None:
    """Records choice keys seen at import time as pending rows (off, decided_at empty); a key that already has a row, pending or decided,
    is left alone. The caller commits. This is what keeps every page's popup check one small query instead of a scan of all readings."""
    if keys:
        session.execute(sqlite_insert(HealthMetricChoice).values(
            [{"owner_id": owner_id, "metric": k[:80], "enabled": False, "decided_at": None} for k in sorted(keys)]).on_conflict_do_nothing())


def _unit(session: Session, owner_id: int, metric: str) -> str:
    """The unit of the metric's most recent reading: one indexed lookup (owner, metric, start), never a scan."""
    if metric.startswith("event:"):
        return ""
    return session.scalar(select(HealthReading.unit).where(HealthReading.owner_id == owner_id, HealthReading.metric == metric)
                          .order_by(HealthReading.start_at.desc(), HealthReading.id.desc()).limit(1)) or ""


def _rows(session: Session, owner_id: int) -> list[HealthMetricChoice]:
    return list(session.scalars(select(HealthMetricChoice).where(HealthMetricChoice.owner_id == owner_id)))


def _seed_from_data(session: Session, owner_id: int) -> None:
    """Data stored before pending rows existed: note its keys once (one scan, only for someone with data and no choice rows at all)."""
    keys = set(session.scalars(select(HealthReading.metric).where(HealthReading.owner_id == owner_id).distinct()))
    keys |= {f"event:{k}" for k in session.scalars(select(HealthEvent.kind).where(HealthEvent.owner_id == owner_id, HealthEvent.kind.in_(EVENT_KINDS)).distinct())}
    note_received(session, owner_id, keys)
    session.commit()


def received_metrics(session: Session, owner_id: int) -> list[dict]:
    """Every metric this person has received (numeric ones, plus symptom and state-of-mind events; the parser notes each one), in section
    order. Each is {"metric", "label", "unit", "section", "recommended"}; the unit comes from the most recent reading."""
    rows = [{"metric": c.metric, "label": label_for(c.metric), "unit": unit_label(_unit(session, owner_id, c.metric)), "section": metric_group(c.metric),
             "recommended": c.metric in RECOMMENDED} for c in _rows(session, owner_id)]
    rows.sort(key=lambda r: (SECTIONS.index(r["section"]) if r["section"] in SECTIONS else len(SECTIONS), r["label"].lower()))
    return rows


def _decided(session: Session, owner_id: int) -> dict[str, bool]:
    return {c.metric: c.enabled for c in _rows(session, owner_id) if c.decided_at is not None}


def has_data(session: Session, owner_id: int) -> bool:
    """One cheap query: has this person received any Apple reading or event at all?"""
    return bool(session.scalar(select(exists().where(HealthReading.owner_id == owner_id) | exists().where(HealthEvent.owner_id == owner_id))))


def received_keys(session: Session, owner_id: int) -> set[str]:
    """The choice keys of everything received (metric names and "event:<kind>"), pending or decided."""
    return {c.metric for c in _rows(session, owner_id)}


def undecided(session: Session, owner_id: int) -> list[str]:
    """Received metrics this person has not yet said yes or no to."""
    return sorted(c.metric for c in _rows(session, owner_id) if c.decided_at is None)


def needs_popup(session: Session, owner_id: int) -> bool:
    """One small query (what every page asks): is any received metric still undecided?"""
    return bool(session.scalar(select(exists().where(HealthMetricChoice.owner_id == owner_id, HealthMetricChoice.decided_at.is_(None)))))


def save_choices(session: Session, owner_id: int, enabled: set[str], offered: set[str]) -> None:
    """Writes a yes or no for every metric that was on the form; only those in `enabled` are yes. Names this person never received are ignored."""
    now = naive_utcnow()
    for row in _rows(session, owner_id):
        if row.metric in offered:
            row.enabled, row.decided_at = row.metric in enabled, now
    session.commit()


def is_enabled(session: Session, owner_id: int, metric: str) -> bool:
    """True only for a metric the person switched on; one with no saved choice is not shown yet."""
    row = session.scalar(select(HealthMetricChoice).where(HealthMetricChoice.owner_id == owner_id, HealthMetricChoice.metric == metric))
    return bool(row and row.enabled)


def enabled_metrics(session: Session, owner_id: int) -> set[str]:
    """The keys of every metric the person switched on, in one query (what a page asks instead of is_enabled once per metric)."""
    return set(session.scalars(select(HealthMetricChoice.metric).where(HealthMetricChoice.owner_id == owner_id, HealthMetricChoice.enabled.is_(True))))


def has_health_token(session: Session, owner_id: int) -> bool:
    """True when the person has a live (not revoked) Apple Health token."""
    return bool(session.scalar(select(exists().where(IngestToken.owner_id == owner_id, IngestToken.scope == "health", IngestToken.revoked_at.is_(None)))))


def dialog_context(session: Session, owner_id: int, full: bool = False) -> dict | None:
    """What the popup needs, or None when this person has received no Apple data (then nothing is drawn).
    mode "first": nothing decided yet, the whole list. mode "new": some earlier choices exist, so only the undecided ones are listed (the
    earlier ones are kept). mode "all": the manual full list (`full`), with the undecided ones tagged "New"."""
    metrics = received_metrics(session, owner_id)
    if not metrics and has_data(session, owner_id):
        _seed_from_data(session, owner_id)
        metrics = received_metrics(session, owner_id)
    if not metrics:
        return None
    decided = _decided(session, owner_id)
    for m in metrics:
        m["on"] = decided[m["metric"]] if m["metric"] in decided else m["recommended"]       # the saved answer, else the suggestion
        m["new"] = m["metric"] not in decided
    if all(m["new"] for m in metrics):
        mode = "first"
    elif full:
        mode = "all"
    else:
        mode = "new"
        metrics = [m for m in metrics if m["new"]]
    sections = [{"name": s, "metrics": [m for m in metrics if m["section"] == s]} for s in SECTIONS]
    sections = [s for s in sections if s["metrics"]]
    for s in sections:
        s["all_on"] = all(m["on"] for m in s["metrics"])
    opens = mode == "first" or (mode == "new" and bool(metrics))
    return {"sections": sections, "total": len(metrics), "on": sum(m["on"] for m in metrics), "open": opens, "mode": mode}
