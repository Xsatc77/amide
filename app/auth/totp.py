"""Two-factor authentication with an authenticator app (TOTP: 6 digits, 30-second steps)."""

import io
from datetime import datetime, timezone

import pyotp
import segno

ISSUER = "Amide"


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify(secret: str, code: str, now: datetime | None = None) -> bool:
    """Accepts the current code and one step either side (phone clocks drift a little)."""
    code = (code or "").replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False
    when = (now or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=timezone.utc)
    return pyotp.TOTP(secret).verify(code, for_time=when, valid_window=1)


def qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False,
                                    dark="#16181d", light="#ffffff")
    return buf.getvalue().decode()
