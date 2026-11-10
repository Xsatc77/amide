"""The Progress Review page: one date range's results, adherence, intake, labs and rule-based flags, printable."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock, units
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import BodyMeasurement, DoseLog
from app.progress import review
from app.templating import templates

router = APIRouter()


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


@router.get("/progress")
def progress_page(request: Request, start: str | None = None, end: str | None = None,
                  session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    today = clock.today()
    first = session.scalar(select(func.min(DoseLog.scheduled_date)).where(DoseLog.owner_id == uid)) \
        or session.scalar(select(func.min(BodyMeasurement.measured_at)).where(BodyMeasurement.owner_id == uid))
    end_day = _day(end) or today
    start_day = _day(start) or first or end_day - timedelta(days=28)
    if start_day > end_day:
        start_day = end_day
    result = review.build(session, request.state.user, start_day, end_day, review.Rules.for_user(request.state.user))
    quick = {"Last 30 Days": (today - timedelta(days=29), today), "Last 90 Days": (today - timedelta(days=89), today),
             "All Time": (first or today, today)}
    return templates.TemplateResponse(request, "progress/review.html", {
        "r": result, "u": units.for_user(request.state.user), "quick": quick,
    })
