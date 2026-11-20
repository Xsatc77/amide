"""One safe way to read a whole number out of a form field or query string."""
import re

_DIGITS = re.compile(r"[0-9]{1,18}")


def parse_int(value) -> int | None:
    """The number in `value` when it is 1 to 18 plain ASCII digits, else None (never raises; str.isdigit() accepts characters int() rejects)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    return int(value) if isinstance(value, str) and _DIGITS.fullmatch(value) else None
