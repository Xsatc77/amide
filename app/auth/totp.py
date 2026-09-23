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


def matching_step(secret: str, code: str, now: datetime | None = None) -> int | None:
    """The 30-second step the code belongs to, if it's the current one or one step either side
    (phone clocks drift a little); None if it doesn't match."""
    code = (code or "").replace(" ", "")
    if not secret or not (code.isdigit() and len(code) == 6):
        return None
    t = pyotp.TOTP(secret)
    when = (now or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=timezone.utc)
    for offset in (0, -1, 1):
        step = t.timecode(when) + offset
        if pyotp.utils.strings_equal(code, t.generate_otp(step)):
            return step
    return None


def verify(secret: str, code: str, now: datetime | None = None) -> bool:
    return matching_step(secret, code, now) is not None


def qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False,
                                    dark="#16181d", light="#ffffff")
    return buf.getvalue().decode()
