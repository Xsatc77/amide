"""The Dashboard's Apple Health card: whether it is set up, when data last arrived, and today's steps and walking distance."""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.health import choices
from app.health.series import daily_series
from app.models import HealthRaw, HealthReading, naive_utcnow

# The two figures worth a glance on the Dashboard (shown when the person switched the metric on).
GLANCE = (("step_count", "Steps today"), ("walking_running_distance", "Walking and running today"))
CHART_METRIC = "walking_running_distance"
CHART_DAYS = 14


def card(session: Session, owner_id: int, today: date, now: datetime | None = None) -> dict:
    """{"state": "setup" | "waiting" | "live", "last_received": datetime | None, "figures": [{"label", "value", "unit"}]}.
    "setup": no token and no data yet; "waiting": a token exists but nothing has arrived; "live": data has arrived."""
    if not choices.has_data(session, owner_id):
        return {"state": "waiting" if choices.has_health_token(session, owner_id) else "setup", "last_received": None, "figures": []}
    last = session.scalar(select(func.max(HealthRaw.received_at)).where(HealthRaw.owner_id == owner_id))
    on = choices.enabled_metrics(session, owner_id)
    figures = []
    for metric, label in GLANCE:
        if metric not in on:
            continue
        value = next((v for d, v in daily_series(session, owner_id, metric, today, today, None) if d == today), None)
        if value is None:
            continue
        unit = session.scalar(select(HealthReading.unit).where(HealthReading.owner_id == owner_id, HealthReading.metric == metric)
                              .order_by(HealthReading.start_at.desc(), HealthReading.id.desc()).limit(1)) or ""
        figures.append({"label": label, "value": f"{round(value):,}" if value >= 100 else f"{value:.1f}".rstrip("0").rstrip("."),
                        "unit": choices.unit_label(unit)})
    from app.routers import measurements              # deferred: the router modules import from app.health
    from app.routers.apple_health import _ago
    chart, unit = None, ""
    if CHART_METRIC in on:                              # the walking and running distance of the last two weeks, drawn like the Weight card
        series = daily_series(session, owner_id, CHART_METRIC, today - timedelta(days=CHART_DAYS - 1), today, None)
        chart = measurements._chart(series) if series else None
        unit = choices.unit_label(session.scalar(select(HealthReading.unit).where(HealthReading.owner_id == owner_id, HealthReading.metric == CHART_METRIC)
                                                 .order_by(HealthReading.start_at.desc(), HealthReading.id.desc()).limit(1)) or "")
    return {"state": "live", "last_received": last, "figures": figures, "ago": _ago(last, now or naive_utcnow()) if last else None,
            "chart": chart, "chart_unit": unit}
