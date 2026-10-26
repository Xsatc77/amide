"""Passkeys (WebAuthn): sign in with Face ID, a fingerprint or a device PIN instead of a password and a code.

The phone or computer keeps the private key and unlocks it with the person's face, finger or PIN; this server stores only the public
key and checks a signature. A passkey proves two things at once (the device, and the person who can unlock it), so a passkey sign-in
needs no 2FA code. The face itself never leaves the device."""

import base64
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                      UserVerificationRequirement)

from app import config
from app.models import LoginSession, Passkey, User

RP_NAME = "Amide"
NAME_MAX = 60


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def origin(request: Request) -> str:
    """The address the browser is on (a passkey is tied to it), unless AMIDE_PUBLIC_URL fixes it."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host.split(',')[0].strip()}"


def rp_id(request: Request) -> str:
    return urlsplit(origin(request)).hostname or "localhost"


def supported(request: Request) -> bool:
    """Browsers only offer passkeys on https (or on localhost)."""
    parts = urlsplit(origin(request))
    return parts.scheme == "https" or parts.hostname in ("localhost", "127.0.0.1")


def _keep_challenge(db: Session, session_id: str, challenge: bytes) -> None:
    db.get(LoginSession, session_id).webauthn_challenge = b64(challenge)
    db.commit()


def _take_challenge(db: Session, session_id: str) -> bytes | None:
    """The challenge in flight for this browser, used once."""
    row = db.get(LoginSession, session_id)
    if row is None or not row.webauthn_challenge:
        return None
    challenge, row.webauthn_challenge = unb64(row.webauthn_challenge), None
    db.commit()
    return challenge


def registration_options(db: Session, request: Request, user: User) -> str:
    """JSON for navigator.credentials.create(), asking for a passkey the device can find again by itself (no username typed)."""
    existing = db.scalars(select(Passkey).where(Passkey.user_id == user.id)).all()
    options = generate_registration_options(
        rp_id=rp_id(request), rp_name=RP_NAME, user_name=user.username, user_id=str(user.id).encode(),
        user_display_name=user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64(p.credential_id)) for p in existing])
    _keep_challenge(db, request.state.session_id, options.challenge)
    return options_to_json(options)


def register(db: Session, request: Request, user: User, credential: dict, name: str, now: datetime) -> Passkey | None:
    """Check the browser's answer to registration_options and store the new passkey; None when it does not check out."""
    challenge = _take_challenge(db, request.state.session_id)
    if challenge is None:
        return None
    try:
        verified = verify_registration_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(request),
                                                expected_origin=origin(request), require_user_verification=True)
    except Exception:  # noqa: BLE001 -- any failure to verify is simply "not accepted"
        return None
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == b64(verified.credential_id))):
        return None                                           # a passkey is stored once, for one account
    key = Passkey(user_id=user.id, credential_id=b64(verified.credential_id), public_key=verified.credential_public_key,
                  sign_count=verified.sign_count, name=(name.strip() or "Passkey")[:NAME_MAX], created_at=now)
    db.add(key)
    db.commit()
    return key


def login_options(db: Session, request: Request) -> str:
    """JSON for navigator.credentials.get(): any passkey this site holds on the device, so no username is needed."""
    options = generate_authentication_options(rp_id=rp_id(request), user_verification=UserVerificationRequirement.REQUIRED)
    _keep_challenge(db, request.state.session_id, options.challenge)
    return options_to_json(options)


def login(db: Session, request: Request, credential: dict, now: datetime) -> User | None:
    """The user whose passkey signed the challenge, or None."""
    challenge = _take_challenge(db, request.state.session_id)
    if challenge is None:
        return None
    key = db.scalar(select(Passkey).where(Passkey.credential_id == str(credential.get("id", ""))))
    if key is None:
        return None
    try:
        verified = verify_authentication_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(request),
                                                  expected_origin=origin(request), credential_public_key=key.public_key,
                                                  credential_current_sign_count=key.sign_count, require_user_verification=True)
    except Exception:  # noqa: BLE001
        return None
    key.sign_count = verified.new_sign_count
    key.last_used_at = now
    db.commit()
    return db.get(User, key.user_id)
