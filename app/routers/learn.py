"""The Learn page: a tutorial on gray-market peptides, with a glossary, "terms in this section" boxes and hover definitions."""

from fastapi import APIRouter, Request

from app.learn import glossary, syringe
from app.learn.sections import LAST_UPDATED, SECTIONS
from app.templating import templates

router = APIRouter()


def hover_data() -> dict:
    """What the page's script needs to underline a term and show its card: the definition, the other ways it is written, and where it lives in the glossary."""
    return {e.term: {"definition": e.definition, "aliases": [e.term, *e.aliases], "id": f"g-{glossary.slug(e.term)}"} for e in glossary.ENTRIES if not e.amide}


@router.get("/learn")
def learn(request: Request):
    return templates.TemplateResponse(request, "learn/index.html", {
        "sections": SECTIONS, "glossary_entries": glossary.SORTED, "amide_terms": glossary.AMIDE_TERMS, "by_term": glossary.BY_TERM, "slug": glossary.slug,
        "hover": hover_data(), "section_terms": {s.slug: list(s.terms) for s in SECTIONS}, "last_updated": LAST_UPDATED,
        "drawn": syringe.drawn, "units_for": syringe.units_for, "num": syringe.num,
    })
