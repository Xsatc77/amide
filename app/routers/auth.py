"""Legal notice, welcome, new user, login, two-factor authentication, logout."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config, links
from app.auth import audit, devices, passkeys, passwords, registration, sessions, throttle, totp
from app.db import SessionLocal, get_session
from app.models import InventoryItem, LoginSession, Passkey, Protocol, User
from app.templating import templates

router = APIRouter()

HOME = "/dashboard"  # the front page
BANNER_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}


# ---------------------------------------------------------------- helpers

def custom_banner():
    for ext in BANNER_TYPES:
        path = config.BRANDING_DIR / f"banner{ext}"
        if path.is_file():
            return path
    return None


def banner_url() -> str:
    path = custom_banner()
    return f"/branding/banner?v={int(path.stat().st_mtime)}" if path else "/static/img/amide-banner.svg"


templates.env.globals["banner_url"] = banner_url


def _render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    context = {"trusted_days": config.TRUSTED_DEVICE_DAYS} | (context or {})
    if name in ("welcome", "login") and "registration_open" not in context:
        with SessionLocal() as db:
            context["registration_open"] = registration.is_open(db)      # hides the New User choices while sign-up is closed
    return templates.TemplateResponse(request, f"auth/{name}.html", context, status_code=status_code)


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(sessions.COOKIE, token, httponly=True, samesite="lax", secure=config.cookie_secure(request),
                        path="/")


def _current_session(db: Session, request: Request) -> LoginSession | None:
    return db.get(LoginSession, request.state.session_id) if request.state.session_id else None


def _sign_in(db: Session, request: Request, user: User, *, pending_2fa: bool) -> Response:
    """Start a fresh session for `user` (a new token on every sign-in) and send them on."""
    now = sessions.now_utc()
    old = _current_session(db, request)
    new = sessions.create(db, now)
    new.user_id = user.id
    new.notice_accepted_at = old.notice_accepted_at if old else now
    new.twofa_pending = pending_2fa
    user.notice_accepted_at = new.notice_accepted_at
    if not pending_2fa:
        user.last_login_at = now
    if old:
        db.delete(old)
    db.commit()
    response = RedirectResponse("/login/2fa" if pending_2fa else HOME, status_code=303)
    _set_cookie(response, request, new.id)
    return response


async def _form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


# ---------------------------------------------------------------- notice + welcome

@router.get("/notice")
def notice(request: Request):
    return _render(request, "notice")


@router.post("/notice")
async def accept_notice(request: Request, db: Session = Depends(get_session)):
    form = await _form(request)
    if form.get("understand") != "1":
        return _render(request, "notice", {"error": "Please tick the box to confirm you understand."}, 422)
    now = sessions.now_utc()
    row = _current_session(db, request)
    if row is None:
        # No cookie: every call makes a new row, so one address may only make so many an hour (stale ones are purged by the gate).
        if wait := throttle.throttle.check("anon_session", throttle.client_address(request)):
            return _render(request, "throttled", {"message": throttle.WAIT_MESSAGE, "wait": wait}, 429)
        row = sessions.create(db, now)
    row.notice_accepted_at = now
    db.commit()
    response = RedirectResponse(HOME if row.user_id and not row.twofa_pending else "/welcome", status_code=303)
    _set_cookie(response, request, row.id)
    return response


@router.get("/welcome")
def welcome(request: Request):
    if request.state.user is not None:
        return RedirectResponse(HOME, status_code=303)
    return _render(request, "welcome")


@router.get("/branding/banner")
def branding_banner():
    path = custom_banner()
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type=BANNER_TYPES[path.suffix.lower()])


# ---------------------------------------------------------------- new user

def _checks_context(form: dict | None = None, errors: list[str] | None = None) -> dict:
    return {"f": form or {}, "errors": errors or [], "rules": passwords.password_checks(""),
            "min_length": config.PASSWORD_MIN_LENGTH}


@router.get("/register")
def register_form(request: Request, db: Session = Depends(get_session)):
    if not registration.is_open(db):
        return _render(request, "registration_closed", status_code=403)
    return _render(request, "register", _checks_context())


@router.post("/register")
async def register(request: Request, db: Session = Depends(get_session)):
    if not registration.is_open(db):
        return _render(request, "registration_closed", status_code=403)
    form = await _form(request)
    username = form.get("username", "").strip()
    password, confirm = form.get("password", ""), form.get("confirm", "")
    errors = []
    if err := passwords.username_error(username):
        errors.append(err)
    elif db.scalar(select(User).where(User.username_key == passwords.username_key(username))):
        errors.append("That username is already taken.")
    errors += passwords.password_errors(password, confirm)
    if errors:
        return _render(request, "register", _checks_context({"username": username, "twofa": form.get("twofa")},
                                                            errors), 422)

    first = db.scalar(select(User.id).limit(1)) is None
    user = User(username=username, username_key=passwords.username_key(username),
                password_hash=await passwords.hash_password_async(password), is_admin=first)
    db.add(user)
    db.flush()
    audit.record(db, "registered", request, user, "first account (admin)" if first else "")
    links.add_starter_links(db, user.id)
    if first:
        # The first account takes over everything created before accounts existed.
        for model in (InventoryItem, Protocol):
            db.execute(update(model).where(model.owner_id.is_(None)).values(owner_id=user.id))
    response = _sign_in(db, request, user, pending_2fa=False)
    if form.get("twofa") == "1":
        response.headers["location"] = "/account/2fa"
    return response


# ---------------------------------------------------------------- login

GENERIC_LOGIN_ERROR = "Username or password is incorrect."


def check_code(user: User, code: str, now) -> str | None:
    """None if the code is good (and records it as used), otherwise the message to show."""
    step = totp.matching_step(user.totp_secret, code, now)
    if step is None:
        return "That code didn't match. Enter the 6-digit code your app shows now."
    if user.totp_last_step is not None and step <= user.totp_last_step:
        return "That code was already used. Wait for the next one in your app."
    user.totp_last_step = step
    return None


def _locked_message(user: User, now) -> str:
    minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
    return f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


@router.get("/login")
def login_form(request: Request):
    return _render(request, "login", {"f": {}})


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_session)):
    form = await _form(request)
    username = form.get("username", "").strip()
    now = sessions.now_utc()
    key = passwords.username_key(username)
    client = throttle.client_address(request)
    user = db.scalar(select(User).where(User.username_key == key))

    def fail(message: str, status_code: int = 422, headers: dict | None = None):
        response = _render(request, "login", {"f": {"username": username}, "error": message}, status_code)
        response.headers.update(headers or {})
        return response

    if user is not None and sessions.is_locked(user, now) and not throttle.is_known(client, key, now):
        return fail(_locked_message(user, now))
    if wait := throttle.backoff_wait(client, key, now):
        return fail(f"Too many wrong tries for that name from this address. Wait {wait} seconds and try again.", 429,
                    {"Retry-After": str(wait)})
    if user is None:
        await passwords.verify_dummy_async(form.get("password", ""))  # same work either way: do not leak names
        throttle.note_failure(client, key, now)
        audit.record(db, "login_failed", request, username=username, detail="unknown name")
        db.commit()
        return fail(GENERIC_LOGIN_ERROR)
    was_locked = sessions.is_locked(user, now)
    if not await passwords.verify_password_async(user.password_hash, form.get("password", "")):
        throttle.note_failure(client, key, now)
        if not was_locked:
            sessions.record_failure(user, now)
        audit.record(db, "login_failed", request, user)
        if sessions.is_locked(user, now) and not was_locked:
            audit.record(db, "lockout", request, user, f"locked for {config.LOCKOUT_MINUTES} minutes")
        db.commit()
        return fail(_locked_message(user, now) if sessions.is_locked(user, now) else GENERIC_LOGIN_ERROR)
    sessions.clear_failures(user)
    throttle.note_success(client, key, now)
    remembered = user.totp_enabled and devices.is_trusted(db, user.id, request.cookies.get(devices.COOKIE), now)
    pending = user.totp_enabled and not remembered
    audit.record(db, "login_ok", request, user, "password ok, 2FA code still needed" if pending else "")
    return _sign_in(db, request, user, pending_2fa=pending)


@router.get("/login/2fa")
def twofa_form(request: Request, db: Session = Depends(get_session)):
    row = _current_session(db, request)
    if row is None or not row.twofa_pending:
        return RedirectResponse("/welcome" if request.state.user is None else HOME, status_code=303)
    return _render(request, "twofa_login")


@router.post("/login/2fa")
async def twofa_login(request: Request, db: Session = Depends(get_session)):
    row = _current_session(db, request)
    if row is None or not row.twofa_pending or row.user is None:
        return RedirectResponse("/welcome", status_code=303)
    user, now = row.user, sessions.now_utc()
    if sessions.is_locked(user, now):
        return _render(request, "twofa_login", {"error": _locked_message(user, now)}, 422)
    form = await _form(request)
    if problem := check_code(user, form.get("code", ""), now):
        sessions.record_failure(user, now)
        audit.record(db, "twofa_failed", request, user)
        if sessions.is_locked(user, now):
            audit.record(db, "lockout", request, user, f"locked for {config.LOCKOUT_MINUTES} minutes")
        db.commit()
        message = _locked_message(user, now) if sessions.is_locked(user, now) else problem
        return _render(request, "twofa_login", {"error": message}, 422)
    sessions.clear_failures(user)
    row.twofa_pending = False
    user.last_login_at = now
    token = devices.remember(db, user.id, now) if form.get("remember") == "1" else None
    audit.record(db, "login_ok", request, user, "2FA code accepted")
    db.commit()
    response = RedirectResponse(HOME, status_code=303)
    if token:
        response.set_cookie(devices.COOKIE, token, max_age=devices.MAX_AGE, httponly=True, samesite="lax",
                            secure=config.cookie_secure(request), path="/")
    return response


# ---------------------------------------------------------------- 2FA setup / disable

def _twofa_page(request: Request, user: User, error: str | None = None, status_code: int = 200):
    context = {"enabled": user.totp_enabled, "error": error}
    if not user.totp_enabled:
        uri = totp.provisioning_uri(user.totp_secret, user.username)
        context |= {"secret": user.totp_secret, "qr": totp.qr_svg(uri),
                    "secret_groups": " ".join(user.totp_secret[i:i + 4] for i in range(0, len(user.totp_secret), 4))}
    return _render(request, "twofa_setup", context, status_code)


def _user_with_secret(db: Session, request: Request) -> User:
    """The signed-in user; while 2FA is off, a pending secret is created (kept until confirmed with a code)."""
    user = db.get(User, request.state.user.id)
    if not user.totp_enabled and not user.totp_secret:
        user.totp_secret = totp.new_secret()
        db.commit()
    return user


@router.get("/account/2fa")
def twofa_setup(request: Request, db: Session = Depends(get_session)):
    return _twofa_page(request, _user_with_secret(db, request))


@router.post("/account/2fa")
async def twofa_change(request: Request, db: Session = Depends(get_session)):
    user = _user_with_secret(db, request)
    form = await _form(request)
    if form.get("action") == "disable" and user.photo_2fa_required:
        return _twofa_page(request, user, "Turn off Body Recomp Photo 2FA in Settings before turning off two-factor authentication.", 422)
    if problem := check_code(user, form.get("code", ""), sessions.now_utc()):
        return _twofa_page(request, user, problem, 422)
    if form.get("action") == "disable":
        user.totp_enabled, user.totp_secret, user.totp_last_step = False, None, None
        devices.forget_all(db, user.id)
        audit.record(db, "twofa_disabled", request, user)
    else:
        user.totp_enabled = True
        audit.record(db, "twofa_enabled", request, user)
    db.commit()
    return RedirectResponse(HOME if user.totp_enabled else "/account/2fa", status_code=303)


@router.post("/account/2fa/forget-devices")
def forget_devices(request: Request, db: Session = Depends(get_session)):
    devices.forget_all(db, request.state.user.id)
    audit.record(db, "devices_forgotten", request, db.get(User, request.state.user.id))
    db.commit()
    response = RedirectResponse("/account/2fa", status_code=303)
    response.delete_cookie(devices.COOKIE, path="/")
    return response


# ---------------------------------------------------------------- passkeys

def _json_sign_in(db: Session, request: Request, user: User) -> Response:
    """Sign `user` in without a 2FA step (a passkey is both factors) and answer the browser's fetch with where to go."""
    redirect = _sign_in(db, request, user, pending_2fa=False)
    answer = JSONResponse({"next": HOME})
    answer.raw_headers += [h for h in redirect.raw_headers if h[0] == b"set-cookie"]
    return answer


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _bad_origin() -> JSONResponse:
    return JSONResponse({"error": "Passkeys are not available at this address."}, status_code=400)


@router.post("/login/passkey/options")
def passkey_login_options(request: Request, db: Session = Depends(get_session)):
    if not passkeys.origin_ok(request):
        return _bad_origin()
    return Response(passkeys.login_options(db, request), media_type="application/json")


@router.post("/login/passkey")
async def passkey_login(request: Request, db: Session = Depends(get_session)):
    now = sessions.now_utc()
    user = passkeys.login(db, request, (await _json_body(request)).get("credential") or {}, now)
    if user is None:
        audit.record(db, "passkey_login_failed", request)
        db.commit()
        return JSONResponse({"error": "That passkey was not accepted. Use your password instead."}, status_code=401)
    audit.record(db, "login_ok", request, user, "passkey")
    return _json_sign_in(db, request, user)


@router.get("/account/passkeys")
def passkeys_page(request: Request, db: Session = Depends(get_session)):
    mine = db.scalars(select(Passkey).where(Passkey.user_id == request.state.user.id).order_by(Passkey.created_at)).all()
    return _render(request, "passkeys", {"passkeys": mine, "supported": passkeys.supported(request),
                                         "totp_enabled": bool(db.get(User, request.state.user.id).totp_enabled)})


@router.post("/account/passkeys/options")
def passkey_register_options(request: Request, db: Session = Depends(get_session)):
    if not passkeys.origin_ok(request):
        return _bad_origin()
    user = db.get(User, request.state.user.id)
    return Response(passkeys.registration_options(db, request, user), media_type="application/json")


@router.post("/account/passkeys")
async def passkey_register(request: Request, db: Session = Depends(get_session)):
    body = await _json_body(request)
    user = db.get(User, request.state.user.id)
    now = sessions.now_utc()
    # Adding a passkey is as sensitive as a password change: prove it is the person (current password, or a fresh code when 2FA is on).
    if sessions.is_locked(user, now):
        return JSONResponse({"error": _locked_message(user, now)}, status_code=429)
    if user.totp_enabled:
        problem = check_code(user, str(body.get("code") or ""), now)
    elif await passwords.verify_password_async(user.password_hash, str(body.get("password") or "")):
        problem = None
    else:
        problem = "Enter your current password to add a passkey."
    if problem:
        sessions.record_failure(user, now)
        audit.record(db, "passkey_reauth_failed", request, user)
        db.commit()
        return JSONResponse({"error": problem}, status_code=403)
    key = passkeys.register(db, request, user, body.get("credential") or {}, str(body.get("name") or ""), sessions.now_utc())
    if key is None:
        db.commit()      # keeps a used 2FA code marked as used
        return JSONResponse({"error": "That passkey could not be added. Try again."}, status_code=400)
    audit.record(db, "passkey_added", request, user, key.name)
    db.commit()
    return JSONResponse({"next": "/account/passkeys"})


@router.post("/account/passkeys/{passkey_id}/delete")
def passkey_delete(passkey_id: int, request: Request, db: Session = Depends(get_session)):
    key = db.get(Passkey, passkey_id)
    if key is not None and key.user_id == request.state.user.id:
        audit.record(db, "passkey_removed", request, db.get(User, key.user_id), key.name)
        db.delete(key)
        db.commit()
    return RedirectResponse("/account/passkeys", status_code=303)


# ---------------------------------------------------------------- logout + keep-alive

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_session)):
    row = _current_session(db, request)
    if row is not None:
        if row.user_id is not None:
            audit.record(db, "logout", request, db.get(User, row.user_id))
        row.user_id, row.twofa_pending = None, False
        db.commit()
    return RedirectResponse("/welcome", status_code=303)


@router.post("/session/ping")
def ping():
    """Open tabs call this every minute; the gate has already refreshed the session."""
    return Response(status_code=204)
