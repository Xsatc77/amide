"""Body photos: owner-only blurred and sharp images, and the 2FA unlock."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app import body_photos, photo_access
from app.auth import sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import BodyPhoto, LoginSession, User

router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store"}


def _own_photo(session: Session, photo_id: int, uid: int) -> BodyPhoto:
    photo = session.get(BodyPhoto, photo_id)
    if photo is None or photo.owner_id != uid:
        raise HTTPException(404, "Not found")        # never 403: that would confirm someone else's photo exists
    return photo


def _login_row(session: Session, request: Request) -> LoginSession | None:
    return session.get(LoginSession, request.state.session_id) if request.state.session_id else None


def _file(photo: BodyPhoto):
    try:
        path = body_photos.photo_path(photo.filename)
    except ValueError:
        raise HTTPException(404, "Not found") from None
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return path


@router.get("/measurements/photos/{photo_id}/preview")
def preview(photo_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    path = _file(_own_photo(session, photo_id, uid))
    return Response(body_photos.blurred_jpeg(path), media_type="image/jpeg", headers=_NO_STORE)


@router.get("/measurements/photos/{photo_id}/full")
def full(photo_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo = _own_photo(session, photo_id, uid)
    path = _file(photo)
    if not photo_access.can_view_full(session.get(User, uid), _login_row(session, request), sessions.now_utc()):
        return JSONResponse({"locked": True}, status_code=403, headers=_NO_STORE)
    return Response(path.read_bytes(), media_type="image/jpeg", headers=_NO_STORE)


@router.post("/measurements/photos/unlock")
async def unlock(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    code = str((await request.form()).get("code", ""))
    row = _login_row(session, request)
    now = sessions.now_utc()
    problem = photo_access.unlock(session, session.get(User, uid), row, code, now)
    if problem:
        return JSONResponse({"error": problem}, status_code=422, headers=_NO_STORE)
    return JSONResponse({"ok": True, "seconds": photo_access.seconds_left(row, now)}, headers=_NO_STORE)


@router.post("/measurements/photos/lock")
def lock(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo_access.lock(session, _login_row(session, request))
    return JSONResponse({"ok": True}, headers=_NO_STORE)
