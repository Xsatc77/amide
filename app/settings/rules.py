"""Pure validation for account-settings fields that aren't already covered by app.auth.passwords."""

import re
import zoneinfo

TIMEZONES: list[str] = sorted(zoneinfo.available_timezones())
_TIMEZONE_SET = set(TIMEZONES)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_error(value: str) -> str | None:
    """None for a blank value (email is optional) or a plausible address; otherwise a message."""
    value = (value or "").strip()
    if not value:
        return None
    if not _EMAIL.match(value):
        return "Enter a valid email address."
    return None


def timezone_error(value: str) -> str | None:
    """None for blank ("System") or a real IANA zone name; otherwise a message."""
    value = (value or "").strip()
    if not value:
        return None
    if value not in _TIMEZONE_SET:
        return "Pick a timezone from the list."
    return None
