"""The Energy and Progress tabs of the Workouts page: a TDEE breakdown with a daily burn chart, and charts derived
from the logged workout history. Everything here is read-only and scoped to the signed-in owner."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.library.price_lists.chart import multi_series_chart
from app.library.price_lists.vendor_view import PALETTE
from app.measurements import tdee
from app.measurements.calculations import body_fat_pct, target_calories
from app.models import BiologicalSex, BodyMeasurement, MacroGoal, User, WeightUnit, WorkoutExerciseLog, WorkoutLog
from app.templating import templates
from app.workouts import charts, progress
from app.workouts import logging as workout_logging
from app.workouts.calories import LB_PER_KG

router = APIRouter()

WINDOWS = {"7": 7, "30": 30, "90": 90}
STACK_COLORS = {"BMR": "#0d9488", "Activity": "#2563eb", "Food digestion": "#ca8a04", "Workout": "#db2777"}
MACRO_COLORS = ("#2563eb", "#ca8a04", "#db2777")      # protein, carbs, fat


def color_map(names) -> dict[str, str]:
    """A stable color per name, in the order given, cycling the shared palette."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(names)}


def load_logged(session: Session, uid: int) -> list[progress.LoggedExercise]:
    """The owner's completed exercise logs, oldest first, from the stored snapshots (loads converted to pounds)."""
    rows = session.execute(
        select(WorkoutLog.log_date, WorkoutExerciseLog)
        .join(WorkoutExerciseLog, WorkoutExerciseLog.workout_log_id == WorkoutLog.id)
        .where(WorkoutLog.owner_id == uid, WorkoutExerciseLog.completed.is_(True))
        .order_by(WorkoutLog.log_date, WorkoutExerciseLog.id)).all()
    out = []
    for day, el in rows:
        load = None
        if el.weight_value:
            load = el.weight_value * (LB_PER_KG if el.weight_unit == WeightUnit.KG else 1)
        out.append(progress.LoggedExercise(
            day, el.db_exercise or el.name or "Exercise", el.area, el.equipment, load, el.sets, el.reps_value,
            el.volume_lb, el.net_kcal, el.gross_kcal))
    return out


def _age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _body_composition(session: Session, uid: int, user: User, weight_lb: float) -> dict | None:
    """Fat mass and waist-to-height from the owner's latest tape measurements, when they have them."""
    m = session.scalar(
        select(BodyMeasurement).where(BodyMeasurement.owner_id == uid, BodyMeasurement.neck_in.is_not(None),
                                      BodyMeasurement.waist_in.is_not(None))
        .order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc()).limit(1))
    if m is None:
        return None
    fat = body_fat_pct(user.sex, user.height_in, m.neck_in, m.waist_in, m.hips_in)
    return {"body_fat_pct": round(fat, 1) if fat is not None else None,
            "fat_mass_lb": round(weight_lb * fat / 100, 1) if fat is not None else None,
            "waist_height": round(m.waist_in / user.height_in, 2)}


def _energy_context(session: Session, uid: int, goal: str, carb: str, range_key: str) -> dict:
    user = session.get(User, uid)
    weight = workout_logging.latest_body_weight(session, uid)
    missing = [label for label, value in (("sex", user.sex), ("birth date", user.birth_date),
                                          ("height", user.height_in), ("activity level", user.activity_level))
               if value is None]
    if weight is None:
        missing.append("a weigh-in")
    if missing:
        return {"status": "missing", "missing": missing}

    today = date.today()
    male = user.sex == BiologicalSex.MALE
    report = tdee.report(male=male, age=_age(user.birth_date, today), height_in=user.height_in, weight_lb=weight,
                         activity_factor=float(user.activity_level.value), life_stage=user.life_stage)
    goal = goal if goal in tdee.MACRO_GRID else "maintain"
    carb = carb if carb in ("low", "moderate", "high") else "moderate"
    top = max(level[2] for level in report.levels)
    level_bars = [{"label": name, "factor": factor, "kcal": kcal, "pct": round(kcal / top * 100),
                   "current": abs(factor - report.activity_factor) < 1e-9} for name, factor, kcal in report.levels]

    window = WINDOWS.get(range_key, 30)
    start = today - timedelta(days=window - 1)
    per_day = progress.daily_net_kcal(progress.in_range(load_logged(session, uid), start, today))
    days = [start + timedelta(days=i) for i in range(window)]
    bmr, activity, food = report.split
    target, _ = target_calories(report.tdee, user.macro_goal or MacroGoal.MAINTAIN, user.sex)
    chart = charts.stacked_bars(
        days, {"BMR": [bmr] * window, "Activity": [activity] * window, "Food digestion": [food] * window,
               "Workout": [per_day.get(d, 0) for d in days]}, line=[target] * window, width=720, height=260)
    for bar in chart["bars"]:
        burned = per_day.get(bar["label"], 0)
        bar["tip"] = (f"{bar['label']:%a %m/%d/%Y}: TDEE {report.tdee:,} kcal; workout about {round(burned)} kcal net "
                      f"({burned / report.tdee * 100:.0f}% of TDEE, estimated)")
    return {
        "status": "ok", "r": report, "goal": goal, "carb": carb, "cell": tdee.macro_cell(goal, carb, report.tdee),
        "macro_grid": tdee.MACRO_GRID, "macro_colors": MACRO_COLORS, "level_bars": level_bars,
        "composition": _body_composition(session, uid, user, weight), "weight_lb": weight, "target": round(target),
        "chart": chart, "stack_colors": STACK_COLORS, "range": str(window), "ranges": list(WINDOWS),
        "workout_total": round(sum(per_day.values())), "workout_days": len(per_day),
        "pcos": report.life_stage is not None and report.life_stage.key == "pcos",
    }


@router.get("/workouts/energy")
def energy(request: Request, goal: str = "maintain", carb: str = "moderate",
           range_key: str = Query("30", alias="range"), session: Session = Depends(get_session),
           uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "workouts/energy.html",
                                      _energy_context(session, uid, goal, carb, range_key))


PROGRESS_RANGES = ("30", "90", "365", "all")


def _line(points: list[tuple[date, float]], label: str, color: str, tips: list[str]) -> dict | None:
    """One-series line chart from the price chart's geometry, with a color and a tip per point."""
    chart = multi_series_chart({label: points})
    if chart is None:
        return None
    series = chart["series"][label]
    series["color"] = color
    for dot, tip in zip(series["points"], tips):
        dot["tip"] = tip
    return chart


def _progress_context(session: Session, uid: int, exercise: str, range_key: str) -> dict:
    today = date.today()
    range_key = range_key if range_key in PROGRESS_RANGES else "90"
    everything = load_logged(session, uid)
    names = progress.exercises_by_recency(everything)
    if not names:
        return {"empty": True, "range": range_key, "ranges": PROGRESS_RANGES}
    chosen = exercise if exercise in names else names[0]
    rows = progress.in_range(everything, progress.range_start(range_key, today), today)

    history = progress.exercise_history(rows, chosen)
    load_points = [(p.log_date, p.top_load_lb) for p in history if p.top_load_lb]
    load_tips = [f"{p.log_date:%m/%d/%Y}: top load {p.top_load_lb:,.0f} lb; about {p.net_kcal:,.0f} kcal net (estimated)"
                 for p in history if p.top_load_lb]
    volume_points = [(p.log_date, p.volume_lb) for p in history if p.volume_lb]
    volume_tips = [f"{p.log_date:%m/%d/%Y}: {p.volume_lb:,.0f} lb total volume; about {p.net_kcal:,.0f} kcal net (estimated)"
                   for p in history if p.volume_lb]

    weeks, by_area = progress.weekly_volume_by_area(rows)
    area_colors = color_map(by_area)
    weekly = charts.stacked_bars(weeks, by_area, width=720, height=260) if weeks else None
    if weekly:
        for bar in weekly["bars"]:
            index = weeks.index(bar["label"])
            parts = ", ".join(f"{area} {values[index]:,.0f} lb" for area, values in by_area.items() if values[index])
            bar["tip"] = f"Week of {bar['label']:%m/%d/%Y}: {parts or 'no volume'}"

    equipment = progress.burn_by_equipment(rows)
    equipment_colors = color_map([name for name, _ in equipment])
    top = progress.top_exercises_by_kcal(rows)
    return {
        "empty": False, "names": names, "chosen": chosen, "range": range_key, "ranges": PROGRESS_RANGES,
        "load_chart": _line(load_points, "Top load (lb)", PALETTE[0], load_tips),
        "volume_chart": _line(volume_points, "Volume (lb)", PALETTE[2], volume_tips),
        "records": progress.personal_records(everything), "weekly": weekly, "area_colors": area_colors,
        "ring": charts.ring(equipment), "equipment_colors": equipment_colors,
        "top": [(name, kcal, round(kcal / top[0][1] * 100)) for name, kcal in top],
    }


@router.get("/workouts/progress")
def progress_tab(request: Request, exercise: str = "", range_key: str = Query("90", alias="range"),
                 session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "workouts/progress.html",
                                      _progress_context(session, uid, exercise, range_key))
