from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.goals import GOALS_BY_SLUG
from app.library.tag_goals import goal_colors_for_tag

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


def tag_style(tag: str) -> str:
    """Inline CSS for a tag chip: solid goal color for a single-goal tag, a
    135-degree hard-stop gradient across equal bands for a multi-goal tag,
    or "" for a tag with no goal mapping (the template falls back to the
    existing .tag-plain neutral styling for that case)."""
    slugs = goal_colors_for_tag(tag)
    if not slugs:
        return ""
    if len(slugs) == 1:
        return f"background: var(--goal-{slugs[0]}); color: var(--goal-text);"
    band = 100 / len(slugs)
    stops = []
    for i, slug in enumerate(slugs):
        start = i * band
        end = (i + 1) * band
        stops.append(f"var(--goal-{slug}) {start:.4g}% {end:.4g}%")
    return f"background: linear-gradient(135deg, {', '.join(stops)}); color: var(--goal-text);"


templates.env.filters["tag_style"] = tag_style
