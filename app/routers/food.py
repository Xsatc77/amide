"""Food: diet type and goal, foods and the day's log."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.food import summary as food_summary
from app.models import DietPreset, MacroGoal, User

router = APIRouter()


def _back(day: date, meal: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"/measurements?tab=food&date={day.isoformat()}" + (f"#meal-{meal}" if meal else ""), status_code=303)


def _fail(request: Request, session: Session, uid: int, day: date, errors: dict, form: dict | None = None):
    from app.routers import measurements    # imported here: measurements renders the Food tab and must not import this module
    return measurements._render(request, session, uid, tab="food", status_code=422,
                                extra={"food_error": errors, "food_form": form or {}, "food_date": day})


@router.post("/food/settings")
async def food_settings(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    values = {k: str(form.get(k, "")).strip() for k in ("diet_preset", "macro_goal", "custom_protein_pct", "custom_carb_pct",
                                                        "custom_fat_pct", "date")}
    day = food_summary.parse_day(values["date"]) or date.today()
    errors: dict[str, str] = {}
    try:
        preset = DietPreset(values["diet_preset"])
    except ValueError:
        preset = None
        errors["diet_preset"] = "Choose a diet type from the list."
    try:
        goal = MacroGoal(values["macro_goal"])
    except ValueError:
        goal = None
        errors["macro_goal"] = "Choose a goal from the list."
    custom = None
    if preset == DietPreset.CUSTOM:
        try:
            custom = tuple(int(values[k]) for k in ("custom_protein_pct", "custom_carb_pct", "custom_fat_pct"))
        except ValueError:
            errors["custom"] = "Enter whole-number percentages for protein, carbs and fat."
        else:
            if any(not 0 <= v <= 100 for v in custom) or sum(custom) != 100:
                errors["custom"] = "The three percentages must total 100."
    if errors:
        return _fail(request, session, uid, day, errors, values)
    user = session.get(User, uid)
    user.diet_preset, user.macro_goal = preset, goal
    if custom:
        user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct = custom
    session.commit()
    return _back(day)
