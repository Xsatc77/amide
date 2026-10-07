"""The workout log as an xlsx file: one row per logged exercise, newest workout first."""

import io

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import WorkoutLog

HEADERS = ("Date", "Plan", "Workout", "Exercise", "Done", "Sets", "Reps", "Weight", "Unit", "Minutes", "Speed mph", "Grade %", "Watts", "MET",
           "Body weight lb", "Gross kcal", "Net kcal", "Volume lb", "Note")


def workout_log_xlsx(session: Session, uid: int) -> bytes:
    logs = session.scalars(select(WorkoutLog).where(WorkoutLog.owner_id == uid).options(selectinload(WorkoutLog.exercise_logs))
                           .order_by(WorkoutLog.log_date.desc(), WorkoutLog.id.desc())).all()
    book = Workbook()
    sheet = book.active
    sheet.title = "Workout log"
    sheet.append(list(HEADERS))
    for log in logs:
        for e in log.exercise_logs:
            sheet.append([log.log_date.isoformat(), log.plan_name or "", log.day_label or "", e.name or e.db_exercise or "", "Yes" if e.completed else "No", e.sets, e.reps_value,
                          e.weight_value, e.weight_unit.value if e.weight_unit else None, e.duration_min, e.speed_mph, e.grade_pct, e.watts, e.met, e.body_weight_lb,
                          e.gross_kcal, e.net_kcal, e.volume_lb, e.kcal_note])
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = max(8, min(30, max(len(str(c.value or "")) for c in column) + 2))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
