"""The Links page: saved sites grouped by type, each with Edit and Delete; a short description is read from the site when none was written."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, links
from app.auth.deps import current_user_id
from app.db import SessionLocal, get_session
from app.models import SavedLink
from app.templating import templates

router = APIRouter()
MAX_LINKS = 500
CATCH_UP = 5                      # links with no description that one page view will try to read


def fill_description(link_id: int) -> None:
    """Read the site's own description into the link (runs after the response, in the background). A failed read is remembered so it is
    not tried again on every page view; editing the link resets it."""
    if not config.LINK_DESCRIPTIONS:
        return
    with SessionLocal() as session:
        link = session.get(SavedLink, link_id)
        if link is None or link.description or link.auto_checked:
            return
        url = link.url
    found = links.fetch_description(url)
    with SessionLocal() as session:
        link = session.get(SavedLink, link_id)
        if link is not None and link.url == url:
            link.auto_description, link.auto_checked = found, True
            session.commit()


def _own(session: Session, link_id: int, uid: int) -> SavedLink:
    link = session.get(SavedLink, link_id)
    if link is None or link.owner_id != uid:
        raise HTTPException(404, "Link not found")
    return link


def _render(request: Request, session: Session, uid: int, *, errors=None, form=None, edit_id=None, status_code=200):
    rows = session.scalars(select(SavedLink).where(SavedLink.owner_id == uid)).all()
    return templates.TemplateResponse(request, "links/list.html", {
        "groups": links.grouped(rows), "count": len(rows), "types": links.TYPES, "errors": errors or {}, "form": form or {},
        "edit_id": edit_id, "host_of": links.host_of, "max_description": links.MAX_DESCRIPTION}, status_code=status_code)


@router.get("/links")
def links_page(request: Request, background: BackgroundTasks, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    response = _render(request, session, uid)
    if config.LINK_DESCRIPTIONS:
        waiting = session.scalars(select(SavedLink.id).where(SavedLink.owner_id == uid, SavedLink.auto_checked.is_(False),
                                                              (SavedLink.description.is_(None)) | (SavedLink.description == "")).limit(CATCH_UP)).all()
        for link_id in waiting:
            background.add_task(fill_description, link_id)
    response.background = background
    return response


def _read(form) -> tuple[dict, dict]:
    values = {"name": " ".join(str(form.get("name", "")).split()), "url": str(form.get("url", "")).strip(),
              "link_type": str(form.get("link_type", "")).strip(), "description": " ".join(str(form.get("description", "")).split())}
    errors = {}
    if not values["name"]:
        errors["name"] = "Enter the site's name."
    elif len(values["name"]) > links.MAX_NAME:
        errors["name"] = f"Use {links.MAX_NAME} characters or fewer."
    cleaned = links.clean_url(values["url"])
    if cleaned is None:
        errors["url"] = "Enter a web address such as https://example.com."
    else:
        values["url"] = cleaned
    if values["link_type"] not in links.TYPES:
        errors["link_type"] = "Pick one of the types in the list."
    if len(values["description"]) > links.MAX_DESCRIPTION:
        errors["description"] = f"Use {links.MAX_DESCRIPTION} characters or fewer."
    return values, errors


@router.post("/links")
async def add_link(request: Request, background: BackgroundTasks, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    values, errors = _read(form)
    if not errors and session.scalar(select(SavedLink.id).where(SavedLink.owner_id == uid).offset(MAX_LINKS - 1).limit(1)):
        errors["name"] = f"Your list can hold {MAX_LINKS} links. Remove one first."
    if errors:
        return _render(request, session, uid, errors=errors, form=dict(form), status_code=422)
    link = SavedLink(owner_id=uid, name=values["name"], url=values["url"], link_type=values["link_type"], description=values["description"] or None)
    session.add(link)
    session.commit()
    if not link.description:
        background.add_task(fill_description, link.id)
    return RedirectResponse("/links", status_code=303, background=background)


@router.post("/links/{link_id}")
async def edit_link(link_id: int, request: Request, background: BackgroundTasks, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    link = _own(session, link_id, uid)
    form = await request.form()
    values, errors = _read(form)
    if errors:
        return _render(request, session, uid, errors=errors, form=dict(form), edit_id=link_id, status_code=422)
    if values["url"] != link.url:                                  # a different site: read its description afresh
        link.auto_description, link.auto_checked = None, False
    elif not values["description"] and link.auto_checked and not link.auto_description:
        link.auto_checked = False                                  # clearing a description is a chance to try reading one again
    link.name, link.url, link.link_type, link.description = values["name"], values["url"], values["link_type"], values["description"] or None
    session.commit()
    if not link.description and not link.auto_description:
        background.add_task(fill_description, link.id)
    return RedirectResponse("/links", status_code=303, background=background)


@router.post("/links/{link_id}/delete")
def delete_link(link_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    session.delete(_own(session, link_id, uid))
    session.commit()
    return RedirectResponse("/links", status_code=303)
