"""Quick activity: one walk, run or machine session in a single step. It builds the same form data the full log uses, so the validation and
the calorie estimate (the Compendium speed tables and METs in the exercise database) are exactly the ones every other workout gets."""

from datetime import timedelta

from fastapi import HTTPException

from app import clock

# key -> (label shown, exercise in the database, takes a speed from distance and time)
ACTIVITIES: dict[str, tuple[str, str, bool]] = {
    "walk": ("Walk", "Treadmill Walk", True),
    "jog": ("Jog", "Treadmill Jog", True),
    "run": ("Run", "Treadmill Run", True),
    "bike": ("Stationary bike", "Stationary Bike", False),
    "spin": ("Spin bike", "Spin Bike", False),
    "elliptical": ("Elliptical", "Elliptical", False),
    "rowing": ("Rowing machine", "Rowing Ergometer", False),
    "stairs": ("Stair climber", "Stair Climber", False),
    "rope": ("Jump rope", "Jump Rope", False),
}


def _number(text: str, label: str, *, positive: bool, maximum: float) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise HTTPException(422, f"{label} must be a number.") from None
    if (value <= 0 if positive else value < 0) or value > maximum:
        raise HTTPException(422, f"{label} is out of range.")
    return value


def build_form(activity: str, minutes: str, distance: str, log_date: str) -> tuple[str, dict]:
    """The activity's label and the form data parse_log_form reads. Distance is in the person's own unit (miles or km), so distance divided
    by hours is already a speed in their own unit (mph or km/h), which is what the log form takes. Only walking, jogging and running use it."""
    if activity not in ACTIVITIES:
        raise HTTPException(422, "Choose an activity from the list.")
    label, exercise, speed_based = ACTIVITIES[activity]
    mins = _number(minutes, "Minutes", positive=True, maximum=24 * 60)
    if mins is None:
        raise HTTPException(422, "Enter how many minutes.")
    dist = _number(distance, "Distance", positive=False, maximum=1000)
    when = (log_date or "").strip() or clock.today().isoformat()
    raw = {"log_date": when, "extra-0-exercise": exercise, "extra-0-minutes_value": f"{mins:g}"}
    if speed_based and dist:
        raw["extra-0-speed_value"] = f"{round(dist / (mins / 60), 2):g}"
    return label, raw


def check_date(parsed_date) -> None:
    if parsed_date > clock.today() + timedelta(days=1):
        raise HTTPException(422, "A workout's date cannot be in the future.")
