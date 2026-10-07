"""The Price list inbox (administrator only): tokens for the watcher, the groups it sees, and what it received."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import current_user_id
from app.db import get_session
from app.ingest import process, skip, tokens
from app.models import INGEST_STATUSES, IngestItem, IngestSource, IngestToken, IngestTopic, User, Vendor
from app.templating import templates

router = APIRouter()
BACK = "/settings/ingest"


def _admin(session: Session, uid: int) -> User:
    me = session.get(User, uid)
    if me is None or not me.is_admin:
        raise HTTPException(status_code=404)
    return me


def _done() -> RedirectResponse:
    return RedirectResponse(BACK, status_code=303)


def _page(request: Request, session: Session, me: User, *, status: str = "", q: str = "", new_secret: str | None = None, new_label: str = "", code: int = 200):
    items_q = select(IngestItem).where(IngestItem.status.not_in(("ignored", "duplicate"))).order_by(IngestItem.id.desc()).limit(100)
    if status in INGEST_STATUSES:
        items_q = select(IngestItem).where(IngestItem.status == status).order_by(IngestItem.id.desc()).limit(100)
    # one row per list: the newest item of each group stands for it
    seen, items = set(), []
    for item in session.scalars(items_q):
        if item.group_key not in seen:
            seen.add(item.group_key)
            items.append(item)
    waiting = len({i.group_key for i in session.scalars(select(IngestItem).where(IngestItem.status.in_(("needs_review", "failed"))))})
    sources = {s.id: s for s in session.scalars(select(IngestSource))}
    shown = [s for s in sources.values() if q.strip().lower() in s.title.lower()] if q.strip() else list(sources.values())
    topics_by_source: dict[int, list] = {}
    for t in session.scalars(select(IngestTopic).order_by(IngestTopic.title)):
        topics_by_source.setdefault(t.source_id, []).append(t)
    return templates.TemplateResponse(request, "settings/ingest.html", {
        "me": me, "tokens": list(session.scalars(select(IngestToken).order_by(IngestToken.id.desc()))),
        "sources": shown, "source_by_id": sources, "topics_by_source": topics_by_source, "q": q, "items": items, "statuses": INGEST_STATUSES, "status": status,
        "waiting": waiting, "vendors": list(session.scalars(select(Vendor).order_by(Vendor.name))), "new_secret": new_secret, "new_label": new_label,
        "today": date.today().isoformat(),
    }, status_code=code)


@router.get("/settings/ingest")
def inbox(request: Request, status: str = "", q: str = "", session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return _page(request, session, _admin(session, uid), status=status, q=q[:100])


@router.post("/settings/ingest/tokens")
async def create_token(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    me = _admin(session, uid)
    form = await request.form()
    token, secret = tokens.create_token(session, me.id, str(form.get("label") or ""))
    return _page(request, session, me, new_secret=secret, new_label=token.label)


@router.post("/settings/ingest/tokens/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    tokens.revoke_token(session, token_id)
    return _done()


@router.post("/settings/ingest/sources/{source_id}")
async def update_source(source_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    source = session.get(IngestSource, source_id)
    if source is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    if form.get("new_vendor"):
        vendor = session.scalar(select(Vendor).where(Vendor.name == source.title)) or Vendor(name=source.title[:200], created_by_id=uid)
        session.add(vendor)
        session.commit()
        source.vendor_id = vendor.id
    else:
        raw = str(form.get("vendor_id") or "").strip()
        if raw and (not raw.isdigit() or session.get(Vendor, int(raw)) is None):
            raise HTTPException(status_code=422, detail="Unknown vendor.")
        source.vendor_id = int(raw) if raw else None
    warehouse = str(form.get("default_warehouse") or "").strip()
    if warehouse not in ("", "us", "china"):
        raise HTTPException(status_code=422, detail="Warehouse must be us or china.")
    source.default_warehouse = warehouse or None
    source.enabled = bool(form.get("enabled"))
    try:
        skips = skip.parse_skip_words(str(form.get("skip_words") or ""))
        follows = skip.parse_skip_words(str(form.get("follow_words")) if "follow_words" in form else source.follow_words)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    source.skip_words = ", ".join(skips) or None
    source.follow_words = ", ".join(follows) or None
    source.topics_only = bool(form.get("topics_only"))
    ticked = {int(v) for v in form.getlist("topics") if str(v).isdigit()}
    for topic in session.scalars(select(IngestTopic).where(IngestTopic.source_id == source.id)):
        topic.enabled = topic.id in ticked           # only this group's own topics can be ticked; the switch decides whether ticks are used
    session.commit()
    return _done()


def _int(raw) -> int:
    raw = str(raw or "").strip()
    if not raw.isdigit():
        raise HTTPException(status_code=422, detail="Choose a vendor.")
    return int(raw)


@router.post("/settings/ingest/items/{item_id}/approve")
async def approve(item_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    form = await request.form()
    try:
        when = date.fromisoformat(str(form.get("list_date") or ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="Enter the list date.") from None
    try:
        await run_in_threadpool(process.approve_group, session, item_id, vendor_id=_int(form.get("vendor_id")),
                                warehouse=str(form.get("warehouse") or ""), list_date=when, user_id=uid)
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _done()


@router.post("/settings/ingest/items/{item_id}/reject")
def reject(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    try:
        process.reject_group(session, item_id, uid)
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _done()


@router.post("/settings/ingest/items/{item_id}/undo")
def undo(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    try:
        process.undo_group(session, item_id, uid)
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _done()


@router.get("/settings/ingest/items/{item_id}/original")
def original(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    item = session.get(IngestItem, item_id)
    path = config.INGEST_DIR / item.stored_file if item is not None and item.stored_file else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/octet-stream", filename=f"item-{item.id}", headers={"Cache-Control": "no-store"})
