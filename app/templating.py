from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.goals import GOALS_BY_SLUG

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
STATIC_DIR = Path(__file__).parent / "static"


def static_url(path: str) -> str:
    """URL of a static file with its modification time attached, so browsers fetch a changed file
    right away instead of reusing an old cached copy."""
    try:
        version = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = static_url


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
