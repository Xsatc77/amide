"""The Progress Review page: one date range's results, adherence, intake, labs and rule-based flags, printable."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ai_summary, clock, units
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
    return _page(request, session, uid, start_day, end_day, first or today)


def _page(request: Request, session: Session, uid: int, start_day: date, end_day: date, first: date, *, ai_error: str | None = None, status_code: int = 200):
    today = clock.today()
    user = request.state.user
    result = review.build(session, user, start_day, end_day, review.Rules.for_user(user))
    quick = {"Last 30 Days": (today - timedelta(days=29), today), "Last 90 Days": (today - timedelta(days=89), today),
             "All Time": (first, today)}
    return templates.TemplateResponse(request, "progress/review.html", {
        "r": result, "u": units.for_user(user), "quick": quick,
        "ai_ready": ai_summary.configured(user), "summary": ai_summary.saved(session, uid, start_day, end_day), "ai_error": ai_error,
    }, status_code=status_code)


@router.post("/progress/summary")
def progress_summary(request: Request, start: str = Form(""), end: str = Form(""),
                     session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Ask the person's chosen AI agent for a one-page summary of this date range's Progress Review, and keep it."""
    today = clock.today()
    end_day = _day(end) or today
    start_day = _day(start) or end_day - timedelta(days=28)
    if start_day > end_day:
        start_day = end_day
    first = session.scalar(select(func.min(DoseLog.scheduled_date)).where(DoseLog.owner_id == uid))         or session.scalar(select(func.min(BodyMeasurement.measured_at)).where(BodyMeasurement.owner_id == uid)) or today
    user = request.state.user
    result = review.build(session, user, start_day, end_day, review.Rules.for_user(user))
    try:
        ai_summary.summarize(session, session.get(type(user), uid), result, units.for_user(user))
    except ai_summary.AiError as error:
        return _page(request, session, uid, start_day, end_day, first, ai_error=str(error), status_code=502)
    return RedirectResponse(f"/progress?start={start_day.isoformat()}&end={end_day.isoformat()}#summary", status_code=303)
