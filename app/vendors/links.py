"""Builds a clickable link for a vendor contact entry, when its method type has a known scheme.
Pure function -- no database access."""

import re

_BUILDERS = {
    "email": lambda value: f"mailto:{value}",
    "phone": lambda value: f"tel:{value}",
    "whatsapp": lambda value: f"https://wa.me/{re.sub(r'\D', '', value)}",
    "telegram": lambda value: f"https://t.me/{value.lstrip('@')}",
}


def contact_link(method_type_name: str, value: str) -> str | None:
    builder = _BUILDERS.get((method_type_name or "").strip().lower())
    return builder(value) if builder else None
