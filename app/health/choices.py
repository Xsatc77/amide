"""Which Apple Health metrics a person wants shown: what has arrived, what is still undecided, and the saved yes/no per metric."""

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.health.groups import RECOMMENDED, SECTIONS, metric_group
from app.models import HealthEvent, HealthMetricChoice, HealthReading, naive_utcnow

# Event kinds are offered next to the numeric metrics under the key "event:<kind>".
EVENT_KINDS: dict[str, str] = {"symptom": "Symptoms", "state_of_mind": "State of mind"}

# Friendly names for the metrics people are likely to see; a name not listed gets a readable one derived from it (see label_for).
LABELS: dict[str, str] = {
    "step_count": "Steps", "walking_running_distance": "Walking and running distance", "flights_climbed": "Flights climbed",
    "active_energy": "Active energy", "basal_energy_burned": "Resting energy", "apple_exercise_time": "Exercise minutes",
    "apple_stand_time": "Stand minutes", "apple_stand_hour": "Stand hours", "walking_speed": "Walking speed",
    "walking_step_length": "Walking step length", "walking_asymmetry_percentage": "Walking asymmetry",
    "walking_double_support_percentage": "Walking double support", "stair_speed_up": "Stair speed up", "stair_speed_down": "Stair speed down",
    "weight_&_body_mass": "Weight", "body_fat_percentage": "Body fat", "lean_body_mass": "Lean body mass", "body_mass_index": "BMI",
    "dietary_water": "Water", "dietary_energy": "Dietary energy", "protein": "Protein", "carbohydrates": "Carbohydrates",
    "total_fat": "Fat", "fiber": "Fiber", "dietary_sugar": "Sugar", "sleep_analysis": "Sleep analysis",
    "heart_rate": "Heart rate", "resting_heart_rate": "Resting heart rate", "walking_heart_rate_average": "Walking heart rate",
    "heart_rate_variability": "Heart rate variability", "blood_pressure": "Blood pressure", "blood_oxygen_saturation": "Blood oxygen",
    "respiratory_rate": "Breathing rate", "body_temperature": "Body temperature", "blood_glucose": "Blood glucose", "vo2_max": "VO2 max",
    "cardio_recovery": "Cardio recovery", "apple_sleeping_wrist_temperature": "Sleeping wrist temperature",
}


def label_for(metric: str) -> str:
    """A person-readable name: the friendly one if known, otherwise the raw name with underscores opened out."""
    if metric.startswith("event:"):
        return EVENT_KINDS.get(metric[6:], label_for(metric[6:]))
    if metric in LABELS:
        return LABELS[metric]
    words = metric.replace("_", " ").replace("&", "and").strip()
    return words[:1].upper() + words[1:]


def received_metrics(session: Session, owner_id: int) -> list[dict]:
    """Every metric this person has actually received (numeric ones, plus symptom and state-of-mind events), in section order.
    Each is {"metric", "label", "unit", "section", "recommended"}; the unit comes from the most recent reading."""
    newest = (select(HealthReading.metric, func.max(HealthReading.start_at).label("at"))
              .where(HealthReading.owner_id == owner_id).group_by(HealthReading.metric).subquery())
    units: dict[str, str] = {}
    for metric, unit in session.execute(
            select(HealthReading.metric, HealthReading.unit).join(newest, (HealthReading.metric == newest.c.metric) & (HealthReading.start_at == newest.c.at))
            .where(HealthReading.owner_id == owner_id).order_by(HealthReading.id)):
        units[metric] = unit or ""                      # two readings at the same instant: the later row wins
    rows = [{"metric": m, "label": label_for(m), "unit": u, "section": metric_group(m), "recommended": m in RECOMMENDED} for m, u in units.items()]
    for kind in session.scalars(select(HealthEvent.kind).where(HealthEvent.owner_id == owner_id, HealthEvent.kind.in_(EVENT_KINDS)).distinct()):
        key = f"event:{kind}"
        rows.append({"metric": key, "label": label_for(key), "unit": "", "section": metric_group(key), "recommended": key in RECOMMENDED})
    rows.sort(key=lambda r: (SECTIONS.index(r["section"]) if r["section"] in SECTIONS else len(SECTIONS), r["label"].lower()))
    return rows


def _decided(session: Session, owner_id: int) -> dict[str, bool]:
    return {c.metric: c.enabled for c in session.scalars(select(HealthMetricChoice).where(HealthMetricChoice.owner_id == owner_id))}


def has_data(session: Session, owner_id: int) -> bool:
    """One cheap query: has this person received any Apple reading or event at all? (what every page asks first)"""
    return bool(session.scalar(select(exists().where(HealthReading.owner_id == owner_id) | exists().where(HealthEvent.owner_id == owner_id))))


def received_keys(session: Session, owner_id: int) -> set[str]:
    """The choice keys of everything received (metric names and "event:<kind>"), without units or labels."""
    keys = set(session.scalars(select(HealthReading.metric).where(HealthReading.owner_id == owner_id).distinct()))
    keys |= {f"event:{k}" for k in session.scalars(select(HealthEvent.kind).where(HealthEvent.owner_id == owner_id, HealthEvent.kind.in_(EVENT_KINDS)).distinct())}
    return keys


def undecided(session: Session, owner_id: int) -> list[str]:
    """Received metrics this person has not yet said yes or no to."""
    return sorted(received_keys(session, owner_id) - _decided(session, owner_id).keys())


def needs_popup(session: Session, owner_id: int) -> bool:
    return bool(undecided(session, owner_id))


def save_choices(session: Session, owner_id: int, enabled: set[str], offered: set[str]) -> None:
    """Writes a yes or no for every metric that was on the form; only those in `enabled` are yes. Names this person never received are ignored."""
    offered = offered & received_keys(session, owner_id)
    existing = {c.metric: c for c in session.scalars(select(HealthMetricChoice).where(HealthMetricChoice.owner_id == owner_id))}
    now = naive_utcnow()
    for metric in offered:
        row = existing.get(metric)
        if row is None:
            session.add(HealthMetricChoice(owner_id=owner_id, metric=metric, enabled=metric in enabled, decided_at=now))
        else:
            row.enabled, row.decided_at = metric in enabled, now
    session.commit()


def is_enabled(session: Session, owner_id: int, metric: str) -> bool:
    """True only for a metric the person switched on; one with no saved choice is not shown yet."""
    row = session.scalar(select(HealthMetricChoice).where(HealthMetricChoice.owner_id == owner_id, HealthMetricChoice.metric == metric))
    return bool(row and row.enabled)


def dialog_context(session: Session, owner_id: int, full: bool = False) -> dict | None:
    """What the popup needs, or None when this person has received no Apple data (then nothing is drawn).
    mode "first": nothing decided yet, the whole list. mode "new": some earlier choices exist, so only the undecided ones are listed (the
    earlier ones are kept). mode "all": the manual full list (`full`), with the undecided ones tagged "New"."""
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
