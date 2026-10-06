"""Body photos: owner-only blurred and sharp images, and the 2FA unlock."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import body_photos, config, photo_access
from app.auth import sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import BODY_PHOTO_ANGLES, BodyPhoto, LoginSession, User

router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store"}
_GALLERY = "/measurements?tab=measurements#body-photos"


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


@router.post("/measurements/photos")
async def upload(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    from app.routers import measurements    # imported here: measurements imports photo_access, not this module
    form = await request.form()
    values = {k: str(form.get(k, "")).strip() for k in ("taken_on", "angle", "note")}
    errors: dict[str, str] = {}
    taken_on = date.today()
    if values["taken_on"]:
        try:
            taken_on = date.fromisoformat(values["taken_on"])
        except ValueError:
            errors["taken_on"] = "Enter the photo date as a valid date."
        else:
            if taken_on > date.today() + timedelta(days=1):
                errors["taken_on"] = "A photo's date cannot be in the future."
    if values["angle"] and values["angle"] not in BODY_PHOTO_ANGLES:
        errors["angle"] = "Choose Front, Side, Back or Other."
    if len(values["note"]) > 200:
        errors["note"] = "The note is 200 characters at most."
    upload_file = form.get("file")
    data = b""
    if not isinstance(upload_file, UploadFile) or not upload_file.filename:
        errors["file"] = "Choose a photo to upload."
    else:
        data = await upload_file.read(config.MAX_UPLOAD_BYTES + 1)
    jpeg = None
    if not errors:
        try:
            jpeg = body_photos.process_upload(data)
        except body_photos.PhotoError as exc:
            errors["file"] = str(exc)
    if errors:
        return measurements._render(request, session, uid, tab="measurements", status_code=422,
                                    extra={"photo_error": errors, "photo_form": values, "open_photo_dialog": True})
    name = body_photos.store(jpeg)
    session.add(BodyPhoto(owner_id=uid, taken_on=taken_on, angle=values["angle"] or None,
                          note=values["note"] or None, filename=name))
    try:
        session.commit()
    except Exception:
        session.rollback()
        body_photos.delete_file(name)
        raise
    return RedirectResponse(_GALLERY, status_code=303)


@router.post("/measurements/photos/{photo_id}/delete")
def delete(photo_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo = _own_photo(session, photo_id, uid)
    name = photo.filename
    session.delete(photo)
    session.commit()
    body_photos.delete_file(name)
    return RedirectResponse(_GALLERY, status_code=303)


@router.post("/settings/photo-2fa")
async def photo_2fa(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    from app.routers.auth import check_code
    user = session.get(User, uid)
    form = await request.form()
    now = sessions.now_utc()

    def back(state: str):
        return RedirectResponse(f"/settings?photo2fa={state}#photo-2fa", status_code=303)

    if form.get("action") == "enable":
        if not user.totp_enabled:
            return back("need2fa")
        user.photo_2fa_required = True
        session.commit()
        return back("on")
    if form.get("action") != "disable":
        raise HTTPException(404, "Not found")
    if sessions.is_locked(user, now):
        return back("locked")
    if check_code(user, str(form.get("code", "")), now):
        sessions.record_failure(user, now)
        session.commit()
        return back("locked" if sessions.is_locked(user, now) else "badcode")
    sessions.clear_failures(user)
    user.photo_2fa_required = False
    photo_access.lock(session, _login_row(session, request))     # commits, ending this session's unlock too
    session.commit()
    return back("off")
