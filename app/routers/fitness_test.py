"""The standalone Fitness Test: a fixed, no-equipment bodyweight exercise set, retaken anytime,
with a per-exercise trend chart and a gentle (never blocking) 28-day retest suggestion."""

import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import FitnessTestExerciseName, FitnessTestResult
from app.templating import templates

router = APIRouter()

_LABELS = {
    FitnessTestExerciseName.MAX_PUSHUPS: "Max Push-ups",
    FitnessTestExerciseName.MAX_SITUPS: "Max Sit-ups",
    FitnessTestExerciseName.MAX_BODYWEIGHT_SQUATS: "Max Bodyweight Squats",
    FitnessTestExerciseName.PLANK_HOLD_SECONDS: "Plank Hold (seconds)",
}

_RETEST_AFTER_DAYS = 28


def _charts_and_suggestions(session: Session, uid: int) -> list[dict]:
    from app.routers.measurements import _chart  # deferred: avoid a module-load cycle

    out = []
    for exercise in FitnessTestExerciseName:
        rows = session.scalars(
            select(FitnessTestResult)
            .where(FitnessTestResult.owner_id == uid, FitnessTestResult.exercise == exercise)
            .order_by(FitnessTestResult.tested_at)
        ).all()
        points = [(r.tested_at, r.value) for r in rows]
        last_tested = rows[-1].tested_at if rows else None
        suggest_retest = last_tested is not None and (date.today() - last_tested).days >= _RETEST_AFTER_DAYS
        out.append({
            "key": exercise.value, "label": _LABELS[exercise],
            "chart": _chart(points) if len(points) >= 2 else None,
            "last_tested": last_tested, "suggest_retest": suggest_retest,
        })
    return out


@router.get("/fitness-test")
def fitness_test_page(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "fitness_test/index.html", {
        "exercises": _charts_and_suggestions(session, uid),
        "today": date.today(),
    })


@router.post("/fitness-test")
async def fitness_test_log(request: Request, session: Session = Depends(get_session),
                           uid: int = Depends(current_user_id)):
    raw = await request.form()
    try:
        tested_at = date.fromisoformat(raw["tested_at"])
    except (KeyError, ValueError):
        raise HTTPException(422, "A valid test date (YYYY-MM-DD) is required.")
    # Validate every value before adding any, so one bad field saves nothing rather than a partial test.
    results = []
    for exercise in FitnessTestExerciseName:
        value = raw.get(exercise.value)
        if not value:
            continue
        try:
            number = float(value)
        except ValueError:
            raise HTTPException(422, f"{_LABELS[exercise]} must be a number.")
        if math.isnan(number) or math.isinf(number):  # float() happily accepts "nan"/"inf"
            raise HTTPException(422, f"{_LABELS[exercise]} must be a finite number.")
        results.append(FitnessTestResult(owner_id=uid, exercise=exercise, value=number, tested_at=tested_at))
    session.add_all(results)
    session.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/fitness-test", status_code=303)
