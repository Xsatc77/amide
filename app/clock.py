"""'Today' for the signed-in person. The server (a container, usually) runs in UTC, so its date turns over hours before a person's own in
the Americas: at 8 PM Monday in Chicago the server already says Tuesday. The request gate sets the person's time zone once per request
and everything that asks "what day is it" calls today() here instead of date.today(). With no zone known (background work, someone who
has not chosen one) it is the server's date, as before."""

from contextvars import ContextVar
from datetime import date, datetime
from zoneinfo import ZoneInfo

_zone: ContextVar[ZoneInfo | None] = ContextVar("amide_zone", default=None)


def set_zone(name: str | None) -> None:
    try:
        _zone.set(ZoneInfo(name) if name else None)
    except Exception:                       # an unknown or malformed zone name: fall back to the server's date
        _zone.set(None)


def zone_name() -> str | None:
    """The zone the request gate set for this request (the person's chosen one, else the device's for "automatic"), or None."""
    zone = _zone.get()
    return zone.key if zone else None


def local_date(moment: datetime, zone_name: str | None) -> date:
    """The calendar day a stored moment (naive means UTC) falls on in a person's own time zone."""
    from datetime import timezone
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(ZoneInfo(zone_name)).date() if zone_name else moment.astimezone(timezone.utc).date()
    except Exception:
        return moment.astimezone(timezone.utc).date()


def today() -> date:
    zone = _zone.get()
    return datetime.now(zone).date() if zone else date.today()
