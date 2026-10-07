"""Order tracking, shipping-tracker style, built from the dates the owner already enters: ordered, shipped, delivered, checked in.

No carrier data is fetched. A tracking number only becomes a link: the owner's own tracking site (with `{number}` filled in
when the address has it), or the carrier's page when the number's format is recognized, or a universal tracker as a fallback."""

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

STEPS = (("ordered", "Ordered"), ("shipped", "Shipped"), ("delivered", "Delivered"), ("checked_in", "Checked in"))
STATUS_BY_STEP = ("waiting", "in_transit", "delivered", "checked_in")
STATUS_LABELS = {"waiting": "Waiting to ship", "in_transit": "In transit", "delivered": "Delivered, needs check-in", "checked_in": "Checked in"}
DEFAULT_DELAY_DAYS = 21

_CARRIERS = (
    ("UPS", re.compile(r"1Z[0-9A-Z]{16}"), "https://www.ups.com/track?tracknum={n}"),
    ("USPS", re.compile(r"(?:9[2-5]\d{18,20}|\d{22}|[A-Z]{2}\d{9}US)"), "https://tools.usps.com/go/TrackConfirmAction?tLabels={n}"),
    ("FedEx", re.compile(r"(?:\d{12}|\d{15})"), "https://www.fedex.com/fedextrack/?trknbr={n}"),
    ("DHL", re.compile(r"\d{10}"), "https://www.dhl.com/global-en/home/tracking/tracking-express.html?submit=1&tracking-id={n}"),
    ("International post", re.compile(r"[A-Z]{2}\d{9}[A-Z]{2}"), "https://t.17track.net/en#nums={n}"),
)
UNIVERSAL = ("17TRACK", "https://t.17track.net/en#nums={n}")


@dataclass
class TrackLink:
    url: str
    label: str
    copy_first: bool = False         # the site has no place for the number: copy it for the owner to paste


def detect_carrier(number: str | None) -> tuple[str, str] | None:
    """(carrier name, link template with {n}) for a recognized tracking number format, else None."""
    cleaned = re.sub(r"[\s-]", "", (number or "")).upper()
    for name, pattern, template in _CARRIERS:
        if pattern.fullmatch(cleaned):
            return name, template
    return None


def tracking_link(site: str | None, number: str | None) -> TrackLink | None:
    cleaned = re.sub(r"[\s-]", "", (number or "")).upper()
    site = (site or "").strip()
    if site and "{number}" in site:
        return TrackLink(site.replace("{number}", quote(cleaned or "", safe="")), "Track package")
    if site:
        return TrackLink(site, "Open tracking site", copy_first=bool(cleaned))
    if cleaned:
        name, template = detect_carrier(cleaned) or UNIVERSAL
        return TrackLink(template.replace("{n}", quote(cleaned, safe="")), f"Track on {name}")
    return None


def build_timeline(order, today: date, delay_days: int | None = None) -> dict:
    """The four steps with their dates and state, the status, the days it has been waiting, and whether it is late.

    `order.arrival_date` is the check-in date (filling it in is the check-in); `order.delivered_date` is when the package
    arrived at the door. An order checked in without a delivered date counts as delivered on its check-in date."""
    delay = delay_days if delay_days else DEFAULT_DELAY_DAYS
    checked_in = order.arrival_date
    delivered = getattr(order, "delivered_date", None) or checked_in
    dates = [order.order_date, order.shipped_date, delivered, checked_in]
    reached = max((i for i, d in enumerate(dates) if d is not None), default=0)
    steps = []
    for i, (key, label) in enumerate(STEPS):
        state = "done" if i < reached or (i == reached and reached == 3) else "current" if i == reached else "todo"
        steps.append({"key": key, "label": label, "date": dates[i], "state": state})
    anchor = {0: order.order_date, 1: order.shipped_date, 2: delivered, 3: None}[reached] or order.order_date
    days = (today - anchor).days if reached < 3 else 0
    late = None
    if reached < 2:
        waited = (today - (order.shipped_date or order.order_date)).days
        late = "late" if waited > delay else "warn" if waited > delay * 3 / 4 else None
    status = STATUS_BY_STEP[reached]
    return {"steps": steps, "status": status, "status_label": STATUS_LABELS[status], "days": max(days, 0), "late": late}
