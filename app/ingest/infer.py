"""Working out warehouse and date from what surrounds a price list."""

import re
from datetime import date

_US = re.compile(r"(?<![a-z])(usa|us|u\.s\.a?|united states)(?![a-z])", re.IGNORECASE)
_CHINA = re.compile(r"(?<![a-z])(china|chinese|cn)(?![a-z])", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
WINDOW_DAYS = 45


def _words_hint(text: str | None) -> str | None:
    us, china = _US.search(text or ""), _CHINA.search(text or "")
    if us and china:
        return "us" if us.start() < china.start() else "china"
    return "us" if us else "china" if china else None


def infer_warehouse(hint: str | None, caption: str | None, filename: str | None, default: str | None) -> tuple[str, bool]:
    """(warehouse, assumed): the list's own text, then words in the caption, then in the filename, then the group's default,
    then China, which is flagged as assumed."""
    if hint in ("us", "china"):
        return hint, False
    for text in (caption, filename):
        found = _words_hint(text)
        if found:
            return found, False
    if default in ("us", "china"):
        return default, False
    return "china", True


def _candidates(text: str, year: int):
    for m in re.finditer(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", text):
        yield int(m[1]), int(m[2]), int(m[3])
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b", text):
        yield int(m[3]), int(m[1]), int(m[2])
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b", text, re.IGNORECASE):
        yield year, _MONTHS[m[1].lower()], int(m[2])
    for m in re.finditer(r"(?<![\d./-])(\d{1,2})\.(\d{1,2})(?![\d./-])", text):
        yield year, int(m[1]), int(m[2])


def infer_date(texts: list[str], received: date) -> date:
    """A date written in the text, if it is within 45 days of the message; otherwise the message's own date."""
    for text in texts:
        for year, month, day in _candidates(text or "", received.year):
            try:
                found = date(year, month, day)
            except ValueError:
                continue
            if abs((found - received).days) <= WINDOW_DAYS:
                return found
    return received
