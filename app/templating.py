from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.goals import GOALS_BY_SLUG

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def money(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def mg(value: float | None) -> str:
    return "" if value is None else f"{value:g} mg"


def shortdate(value: date | None) -> str:
    return "" if value is None else f"{value:%b} {value.day}, {value.year}"


templates.env.filters["money"] = money
templates.env.filters["mg"] = mg
templates.env.filters["shortdate"] = shortdate
templates.env.filters["dose_num"] = lambda v: f"{v:g}"
templates.env.filters["goal_label"] = lambda slug: GOALS_BY_SLUG[slug].label if slug in GOALS_BY_SLUG else slug
