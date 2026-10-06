"""Builds a clickable link for a vendor contact entry, when its method type has a known scheme.
Pure function -- no database access."""

import re

_TELEGRAM_URL = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", re.IGNORECASE)


def _whatsapp(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    return f"https://wa.me/{digits}" if digits else None  # wa.me opens the app, or WhatsApp Web on a computer


def _telegram(value: str) -> str | None:
    value = value.strip()
    if _TELEGRAM_URL.match(value):  # already a link someone pasted
        return "https://t.me/" + _TELEGRAM_URL.sub("", value)
    if re.fullmatch(r"\+?[\d\s().-]{7,}", value):  # a phone number, as Telegram contacts often are
        return "https://t.me/+" + re.sub(r"\D", "", value)
    handle = value.lstrip("@").strip()
    return f"https://t.me/{handle}" if re.fullmatch(r"\w{3,}", handle) else None


_BUILDERS = {
    "email": lambda value: f"mailto:{value}",
    "phone": lambda value: f"tel:{value}",
    "whatsapp": _whatsapp,
    "telegram": _telegram,
}
# Other ways a method type tends to be named.
_ALIASES = {"wa": "whatsapp", "whats app": "whatsapp", "whatsapp business": "whatsapp", "tg": "telegram"}


def contact_link(method_type_name: str, value: str) -> str | None:
    name = re.sub(r"\s+", " ", (method_type_name or "").strip().lower())
    builder = _BUILDERS.get(_ALIASES.get(name, name))
    return builder(value) if builder and (value or "").strip() else None
