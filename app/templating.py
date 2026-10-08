from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.goals import GOALS_BY_SLUG
from app.library.tag_goals import goal_colors_for_tag

def _units_context(request):
    """`u` in every template: the signed-in person's display units (US unless they chose metric in Settings)."""
    from app import units
    return {"u": units.for_user(getattr(request.state, "user", None))}


templates = Jinja2Templates(directory=Path(__file__).parent / "templates", context_processors=[_units_context])
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


@pass_context
def peptide_cautions(ctx, name, aliases=None):
    """Cautions between this peptide and the signed-in person's medicine list (Settings), for the red-and-black tape.
    The list is read once per request."""
    request = ctx.get("request")
    user = getattr(getattr(request, "state", None), "user", None)
    if user is None:
        return []
    meds = getattr(request.state, "medicine_names", None)
    if meds is None:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import UserMedicine
        with SessionLocal() as s:
            meds = list(s.scalars(select(UserMedicine.name).where(UserMedicine.owner_id == user.id)))
        request.state.medicine_names = meds
    if not meds:
        return []
    from app.library.interactions import cautions_for
    return cautions_for(name or "", aliases, meds)


templates.env.globals["peptide_cautions"] = peptide_cautions


def money(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def mg(value: float | None) -> str:
    return "" if value is None else f"{value:g} mg"


def shortdate(value: date | None) -> str:
    return "" if value is None else f"{value:%m/%d/%Y}"   # standard US format, everywhere


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
