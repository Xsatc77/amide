"""Food: diet type and goal, foods and the day's log."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import current_user_id
from app.db import get_session
from app.food import foods as foods_mod
from app.food import logs as logs_mod
from app.food import summary as food_summary
from app.food import recommend as recommend_mod
from app.food import usda
from app.models import FOOD_MEALS, DietPreset, Food, MacroGoal, User

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


def _meal(raw: str) -> str | None:
    return raw if raw in FOOD_MEALS else None


def _food_values(form) -> dict:
    return {k: str(form.get(k, "")).strip() for k in ("name", "serving", "serving_g", "calories", "protein_g", "carb_g", "fat_g", "fiber_g")}


@router.post("/food/log")
async def log_food(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    raw_day, raw_meal, mode = str(form.get("date", "")), str(form.get("meal", "")), str(form.get("mode", "existing"))
    day = food_summary.parse_day(raw_day)
    errors: dict[str, str] = {}
    if day is None:
        errors["date"] = "Choose a date between 2000 and a week from today."
    meal = _meal(raw_meal)
    if meal is None:
        errors["meal"] = "Choose Breakfast, Lunch, Dinner or Snacks."
    servings, problem = logs_mod.parse_servings(form.get("servings", "1"))
    if problem:
        errors["servings"] = problem
    values, food_id = None, None
    if mode == "existing":
        try:
            food = session.get(Food, int(form.get("food_id", "")))
        except ValueError:
            food = None
        if food is None or (food.owner_id is not None and food.owner_id != uid):
            raise HTTPException(404, "Food not found")
        values, food_id = {f: getattr(food, f) for f in ("name", "serving", *foods_mod.LIMITS)}, food.id
    elif mode in ("create", "quick"):
        values, food_errors = foods_mod.parse_food(_food_values(form))
        errors.update(food_errors)
    else:
        errors["mode"] = "Choose a food."
    if errors:
        return _fail(request, session, uid, day or date.today(), errors,
                     {k: str(v) for k, v in form.items() if not hasattr(v, "filename")})
    if mode == "create":
        food_id = foods_mod.own_or_create(session, uid, values).id
    logs_mod.add_entry(session, uid, day, meal, values, servings, food_id)
    return _back(day, meal)


@router.post("/food/log/{log_id}/edit")
async def edit_log(log_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    servings, problem = logs_mod.parse_servings(form.get("servings")) if form.get("servings") else (None, None)
    meal = _meal(str(form.get("meal", ""))) if form.get("meal") else None
    try:
        logs_mod.own_entry(session, uid, log_id)
    except LookupError:
        raise HTTPException(404, "Entry not found") from None
    if problem or (form.get("meal") and meal is None):
        return _fail(request, session, uid, day, {"servings": problem or "Choose a meal from the list."})
    entry = logs_mod.edit_entry(session, uid, log_id, servings=servings, meal=meal)
    return _back(entry.eaten_on, entry.meal)


@router.post("/food/log/{log_id}/delete")
async def delete_log(log_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    try:
        logs_mod.delete_entry(session, uid, log_id)
    except LookupError:
        raise HTTPException(404, "Entry not found") from None
    return _back(day)


@router.get("/food/search")
def search_foods(q: str = Query("", max_length=80), session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return JSONResponse([{"id": f.id, "name": f.name, "serving": f.serving, "starter": f.owner_id is None,
                          **{k: getattr(f, k) for k in foods_mod.LIMITS}} for f in foods_mod.search(session, uid, q)],
                        headers={"Cache-Control": "no-store"})


@router.get("/food/recommend")
def recommend_foods(nutrient: str = Query(...), date_: str | None = Query(None, alias="date"), session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    """Foods (the starter list and the person's own) that fill what is left of today's protein, carbs or fiber with the fewest carbs."""
    if nutrient not in recommend_mod.NUTRIENTS:
        raise HTTPException(422, "Choose protein, carbs or fiber")
    me = session.get(User, uid)
    day = food_summary.parse_day(date_) or date.today()
    summary = food_summary.day_summary(session, me, day)
    if summary["status"] != "ok":
        return JSONResponse({"status": summary["status"], "foods": []}, headers={"Cache-Control": "no-store"})
    fulfil = summary["fulfil"]
    remaining = fulfil[nutrient]["target"] - fulfil[nutrient]["eaten"]
    calories_left = fulfil["calories"]["target"] - fulfil["calories"]["eaten"]
    foods = session.scalars(select(Food).where(or_(Food.owner_id.is_(None), Food.owner_id == uid))).all()
    picks = recommend_mod.recommend(foods, nutrient, remaining, max(calories_left, 0))
    return JSONResponse({"status": "ok", "remaining": round(max(remaining, 0), 1), "foods": picks}, headers={"Cache-Control": "no-store"})


@router.get("/food/usda")
def search_usda(q: str = Query("", max_length=80), session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Live search in the USDA FoodData Central. Only exists when the person running Amide set a key; nothing leaves the server before this is called."""
    key = usda.key_for(session.get(User, uid))
    if not key:
        raise HTTPException(404, "USDA search is not set up")
    if not q.strip():
        return JSONResponse([], headers={"Cache-Control": "no-store"})
    try:
        found = usda.search(q, key, fetch=usda.fetch_json)
    except Exception:
        raise HTTPException(502, "The USDA food database could not be reached") from None
    return JSONResponse(found, headers={"Cache-Control": "no-store"})


def _own_or_404(session: Session, food_id: int, uid: int) -> Food:
    try:
        return foods_mod.own_food(session, food_id, uid)
    except LookupError:
        raise HTTPException(404, "Food not found") from None


@router.post("/food/foods")
async def create_food(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    values, errors = foods_mod.parse_food(_food_values(form))
    if errors:
        return _fail(request, session, uid, day, errors, _food_values(form))
    foods_mod.own_or_create(session, uid, values)
    return _back(day)


@router.post("/food/foods/{food_id}/edit")
async def edit_food(food_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = _own_or_404(session, food_id, uid)
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    values, errors = foods_mod.parse_food(_food_values(form))
    if errors:
        return _fail(request, session, uid, day, errors, _food_values(form))
    clash = foods_mod.find_own(session, uid, values["name"], values["serving"])
    if clash is not None and clash.id != food.id:
        return _fail(request, session, uid, day, {"name": "You already have a food with that name and serving."}, _food_values(form))
    for key, value in values.items():
        setattr(food, key, value)
    session.commit()
    return _back(day)


@router.post("/food/foods/{food_id}/delete")
async def delete_food(food_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = _own_or_404(session, food_id, uid)
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    session.delete(food)
    session.commit()
    return _back(day)


@router.post("/food/foods/{food_id}/copy")
def copy_food(food_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = session.get(Food, food_id)
    if food is None or food.owner_id is not None:
        raise HTTPException(404, "Food not found")
    mine = foods_mod.copy_to_mine(session, uid, food)
    return JSONResponse({"id": mine.id, "name": mine.name}, headers={"Cache-Control": "no-store"})
