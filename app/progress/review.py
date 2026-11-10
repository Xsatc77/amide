"""The Progress Review: what happened between two dates, worked out from the person's own entries. No judgement is made here:
every flag is a fixed rule applied to numbers, shown beside the number with the rule that raised it. `build` returns one plain
dict so the page, a printout, or anything else can read the same result."""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import units
from app.models import (BodyMeasurement, DoseLog, DoseStatus, FitnessTestResult, FoodLog, JournalEntry, LabPanel,
                        WaterLog, WorkoutLog)
from app.units import LB_PER_KG


@dataclass(frozen=True)
class Rules:
    loss_pct_per_week: float = 1.0        # a week's loss above this share of body weight is flagged
    protein_g_per_kg: float = 1.2         # average protein per kilogram of the latest weight below this is flagged
    sleep_low: int = 2                    # a sleep rating at or below this is a poor night
    sleep_low_share_pct: float = 25.0     # flagged when this share of rated days were poor nights
    bp_drop: int = 8                      # systolic this far below the person's own start is flagged
    retest_days: int = 28                 # a fitness test older than this is flagged


# stored field, label, kind
BODY_ROWS = [
    ("weight_lbs", "Weight", "weight"), ("waist_in", "Waist", "length"), ("hips_in", "Hips", "length"), ("neck_in", "Neck", "length"),
    ("scale_body_fat_pct", "Body Fat (Scale)", "%"), ("scale_fat_mass_lb", "Fat Mass (Scale)", "weight"),
    ("scale_muscle_mass_lb", "Muscle Mass (Scale)", "weight"), ("scale_visceral_fat", "Visceral Fat (Scale)", ""),
    ("systolic", "Systolic", "mmHg"), ("diastolic", "Diastolic", "mmHg"), ("heart_rate_bpm", "Pulse", "bpm"),
]


def _first_last(rows, key):
    have = [getattr(r, key) for r in rows if getattr(r, key) is not None]
    return (have[0], have[-1], len(have)) if have else (None, None, 0)


def _body(rows, u):
    out = []
    for key, label, kind in BODY_ROWS:
        first, last, n = _first_last(rows, key)
        if first is None:
            continue
        convert = {"weight": u.weight, "length": u.length}.get(kind)
        show = (lambda v: convert(v)) if convert else (lambda v: v)
        unit = {"weight": u.weight_label, "length": u.length_label}.get(kind, kind)
        out.append({"key": key, "label": label, "unit": unit, "readings": n, "first": show(first), "last": show(last),
                    "change": round(show(last) - show(first), 1), "pct": ((last - first) / first * 100) if first else None,
                    "_first": first, "_last": last})       # the stored (US) values, for the rules below
    return out


def _weeks(rows, start, end):
    """Average weight in each 7-day block from `start`, and the change from the block before as a share of that block's weight."""
    weeks, i = [], 0
    while start + timedelta(days=7 * i) <= end:
        lo, hi = start + timedelta(days=7 * i), start + timedelta(days=7 * i + 6)
        values = [r.weight_lbs for r in rows if r.weight_lbs is not None and lo <= r.measured_at <= hi]
        if values:
            weeks.append({"start": lo, "weight": mean(values)})
        i += 1
    for prev, cur in zip(weeks, weeks[1:]):
        cur["change"] = cur["weight"] - prev["weight"]
        cur["pct"] = cur["change"] / prev["weight"] * 100
    return weeks


def build(session: Session, user, start: date, end: date, rules: Rules = Rules()) -> dict:
    u = units.for_user(user)
    uid = user.id
    days = (end - start).days + 1
    rows = session.scalars(select(BodyMeasurement).where(
        BodyMeasurement.owner_id == uid, BodyMeasurement.measured_at.between(start, end))
        .order_by(BodyMeasurement.measured_at, BodyMeasurement.id)).all()
    body = _body(rows, u)
    weeks = _weeks(rows, start, end)

    workouts = session.scalar(select(func.count()).select_from(WorkoutLog).where(
        WorkoutLog.owner_id == uid, WorkoutLog.log_date.between(start, end))) or 0
    doses = session.scalars(select(DoseLog.status).where(
        DoseLog.owner_id == uid, DoseLog.scheduled_date.between(start, end))).all()
    food = session.scalars(select(FoodLog).where(FoodLog.owner_id == uid, FoodLog.eaten_on.between(start, end))).all()
    food_days = {f.eaten_on for f in food}
    water_by_day: dict[date, float] = {}
    for w in session.scalars(select(WaterLog).where(WaterLog.owner_id == uid, WaterLog.logged_at.between(start, end))):
        water_by_day[w.logged_at] = water_by_day.get(w.logged_at, 0) + w.ounces
    adherence = {"days": days, "workouts": workouts,
                 "doses_logged": sum(1 for s in doses if s in (DoseStatus.ON_TIME, DoseStatus.LATE)),
                 "doses_skipped": sum(1 for s in doses if s == DoseStatus.SKIPPED),
                 "food_days": len(food_days), "water_days": len(water_by_day)}

    intake = None
    if food_days:
        total = lambda attr: sum(getattr(f, attr) for f in food)  # noqa: E731  (a log row's numbers are already for the servings eaten)
        n = len(food_days)
        intake = {"calories": round(total("calories") / n), "protein_g": round(total("protein_g") / n), "carb_g": round(total("carb_g") / n),
                  "fat_g": round(total("fat_g") / n), "fiber_g": round(total("fiber_g") / n), "water_oz": None}
    if water_by_day:
        intake = intake or {"calories": None, "protein_g": None, "carb_g": None, "fat_g": None, "fiber_g": None}
        intake["water_oz"] = round(mean(water_by_day.values()))

    entries = session.scalars(select(JournalEntry).where(JournalEntry.owner_id == uid, JournalEntry.entry_date.between(start, end))).all()
    rated = [e for e in entries if e.sleep_quality is not None]
    effects: dict[str, int] = {}
    for e in entries:
        for se in e.side_effects:
            effects[se.side_effect.value] = effects.get(se.side_effect.value, 0) + 1
    journal = {"entries": len(entries), "rated_days": len(rated),
               "sleep_low_days": sum(1 for e in rated if e.sleep_quality <= rules.sleep_low),
               "side_effects": sorted(effects.items(), key=lambda kv: -kv[1])}

    panels = session.scalars(select(LabPanel).where(LabPanel.owner_id == uid, LabPanel.drawn_at.between(start, end))
                             .order_by(LabPanel.drawn_at)).all()
    latest: dict[str, tuple] = {}
    for p in panels:
        for r in p.results:
            latest[r.marker_other or r.marker.value] = (p.drawn_at, r)
    labs = []
    for name, (drawn, r) in latest.items():
        out = (r.range_low is not None and r.value < r.range_low) or (r.range_high is not None and r.value > r.range_high)
        labs.append({"marker": name, "value": r.value, "unit": r.unit or "", "low": r.range_low, "high": r.range_high,
                     "drawn": drawn, "out_of_range": bool(out)})
    labs.sort(key=lambda x: (not x["out_of_range"], x["marker"]))

    last_fit = session.scalar(select(func.max(FitnessTestResult.tested_at)).where(FitnessTestResult.owner_id == uid))

    return {"start": start, "end": end, "days": days, "body": body, "weeks": weeks, "adherence": adherence,
            "intake": intake, "journal": journal, "labs": labs, "last_fitness_test": last_fit,
            "flags": _flags(rules, rows, body, weeks, adherence, intake, journal, panels, last_fit, end, u),
            "unknowns": _unknowns(rows, intake)}


def _flags(rules, rows, body, weeks, adherence, intake, journal, panels, last_fit, end, u):
    flags = []
    by = {b["key"]: b for b in body}
    steps = [w for w in weeks if "pct" in w]
    over = [w for w in steps if -w["pct"] > rules.loss_pct_per_week]
    if over:
        worst = max(over, key=lambda w: -w["pct"])
        flags.append({"key": "fast_loss",
                      "text": f"Weight fell more than {rules.loss_pct_per_week:.1f}% in {len(over)} of {len(steps)} weeks; the fastest was {-worst['pct']:.1f}% (week of {worst['start']:%b} {worst['start'].day}).",
                      "rule": f"A week's loss above {rules.loss_pct_per_week:.1f}% of body weight."})
    if intake and intake["protein_g"] and "weight_lbs" in by:
        per_kg = intake["protein_g"] / (by["weight_lbs"]["_last"] / LB_PER_KG)
        if per_kg < rules.protein_g_per_kg:
            flags.append({"key": "low_protein", "text": f"Protein averaged {intake['protein_g']} g a day, {per_kg:.2f} g per kg of your latest weight.",
                          "rule": f"Average protein under {rules.protein_g_per_kg:.1f} g per kg."})
    fat, muscle = by.get("scale_fat_mass_lb"), by.get("scale_muscle_mass_lb")
    if fat and muscle and fat["_last"] < fat["_first"] and muscle["_last"] < muscle["_first"]:
        flags.append({"key": "muscle_loss",
                      "text": f"Scale muscle mass fell {u.weight(muscle['_first'] - muscle['_last'])} {u.weight_label} while fat mass fell {u.weight(fat['_first'] - fat['_last'])} {u.weight_label}.",
                      "rule": "Scale muscle mass down over the period while fat mass is also down."})
    if journal["rated_days"] and journal["sleep_low_days"] / journal["rated_days"] * 100 >= rules.sleep_low_share_pct:
        flags.append({"key": "poor_sleep", "text": f"Sleep was rated {rules.sleep_low} or lower on {journal['sleep_low_days']} of {journal['rated_days']} rated days.",
                      "rule": f"At least {rules.sleep_low_share_pct:.0f}% of rated days at {rules.sleep_low} or lower."})
    readings = [r.systolic for r in rows if r.systolic is not None]
    if len(readings) >= 4:
        base, now = mean(readings[:2]), mean(readings[-2:])
        if base - now >= rules.bp_drop:
            flags.append({"key": "bp_drop", "text": f"Systolic blood pressure averaged {now:.0f} lately against {base:.0f} at the start of the period.",
                          "rule": f"Latest two readings at least {rules.bp_drop} points below your first two (your own start, not a fixed number)."})
    if last_fit is None:
        flags.append({"key": "no_fitness_test", "text": "No fitness test has been logged.", "rule": "A fitness test baseline is missing."})
    elif (end - last_fit).days > rules.retest_days:
        flags.append({"key": "fitness_retest", "text": f"The last fitness test was {last_fit:%b} {last_fit.day}, {(end - last_fit).days} days before the end of this period.",
                      "rule": f"A fitness test older than {rules.retest_days} days."})
    if not panels:
        flags.append({"key": "no_labs", "text": "No labs were drawn in this period.", "rule": "No lab panel in the period."})
    return flags


def _unknowns(rows, intake):
    out = ["The strength and purity of what was injected cannot be known from logged doses."]
    if not any(r.scale_fat_mass_lb is not None for r in rows):
        out.append("No scale body-composition readings in this period, so fat and muscle change is not established.")
    if not any(r.systolic is not None for r in rows):
        out.append("No blood pressure readings in this period.")
    if not intake:
        out.append("No food or water was logged in this period.")
    return out
