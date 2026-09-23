"""Calendar: the signed-in user's protocols projected onto month / week / day views."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.calendar.layout import month_rows, month_weeks
from app.calendar.schedule import DueItem, Occurrence, as_needed, occurrences, week_number, week_start
from app.db import get_session
from app.models import Protocol, ProtocolItem, TimeOfDay
from app.routers.protocols import get_today
from app.templating import templates

router = APIRouter()

VIEWS = ("month", "week", "day")
SLOTS = [(TimeOfDay.AM, "am"), (TimeOfDay.PM, "pm"), (TimeOfDay.BEDTIME, "bedtime"), (TimeOfDay.ANY, "any")]
PALETTE_SIZE = 8


def _url(view: str, day: date) -> str:
    return f"/calendar?view={view}&date={day.isoformat()}"


def _add_months(day: date, n: int) -> date:
    month = day.month - 1 + n
    year, month = day.year + month // 12, month % 12 + 1
    for d in (day.day, 30, 29, 28):  # keep the day, or the month's last day
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def _dose_text(item: DueItem) -> str:
    return f"{item.dose:g} {item.unit}" if item.dose is not None else "Dose not set"


def _item_json(item: DueItem) -> dict:
    return {"peptide": item.peptide, "dose": _dose_text(item), "step": item.step,
            "time": item.time_of_day.label, "route": item.route, "inventory": item.inventory}


def _details(occs: list[Occurrence], colors: dict[int, int]) -> dict:
    return {
        f"{o.protocol_id}|{o.date.isoformat()}": {
            "name": o.protocol_name, "date": f"{o.date:%A, %B} {o.date.day}, {o.date.year}",
            "color": colors.get(o.protocol_id, 0), "edit_url": f"/protocols/{o.protocol_id}/edit",
            "items": [_item_json(i) for i in o.items],
        }
        for o in occs
    }


def _by_slot(occ: Occurrence, slot: TimeOfDay) -> list[DueItem]:
    return [i for i in occ.items if i.time_of_day is slot]


@router.get("/calendar")
def calendar_page(request: Request, view: str = "month", date_param: str | None = Query(None, alias="date"),
                  session: Session = Depends(get_session), today: date = Depends(get_today),
                  uid: int = Depends(current_user_id)):
    view = view if view in VIEWS else "month"
    try:
        anchor = date.fromisoformat(date_param) if date_param else today
    except ValueError:
        anchor = today

    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).order_by(Protocol.id).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    colors = {p.id: i % PALETTE_SIZE for i, p in enumerate(protocols)}

    ctx: dict = {"view": view, "anchor": anchor, "today": today, "colors": colors, "slots": SLOTS, "url": _url}
    if view == "month":
        weeks = month_weeks(anchor)
        first, last = weeks[0][0], weeks[-1][-1]
        prev, nxt = _add_months(anchor, -1), _add_months(anchor, 1)
        title = f"{anchor:%B %Y}"
    elif view == "week":
        first = week_start(anchor)
        last = first + timedelta(days=6)
        prev, nxt = anchor - timedelta(days=7), anchor + timedelta(days=7)
        span = f"{first:%b} {first.day}–{last.day}" if first.month == last.month \
            else f"{first:%b} {first.day} – {last:%b} {last.day}"
        title = f"Week {week_number(first)} · {span}, {last.year}"
    else:
        first = last = anchor
        prev, nxt = anchor - timedelta(days=1), anchor + timedelta(days=1)
        title = f"{anchor:%A, %B} {anchor.day}, {anchor.year}"

    occs = occurrences(protocols, first, last)
    ctx |= {"title": title, "prev_url": _url(view, prev), "next_url": _url(view, nxt),
            "today_url": _url(view, today), "data": {"occurrences": _details(occs, colors)}}

    if view == "month":
        ctx["rows"] = month_rows(weeks, occs, colors)
    elif view == "week":
        days = [first + timedelta(days=i) for i in range(7)]
        cells = {key: [[(o, _by_slot(o, slot)) for o in occs if o.date == d and _by_slot(o, slot)] for d in days]
                 for slot, key in SLOTS}
        ctx |= {"days": days, "cells": cells}
    else:
        ctx["sections"] = [(slot, key, [(o, _by_slot(o, slot)) for o in occs if _by_slot(o, slot)])
                           for slot, key in SLOTS]
        ctx["as_needed"] = as_needed(protocols, anchor)
        ctx["dose_text"] = _dose_text
        ctx["nothing"] = not occs and not ctx["as_needed"]
    return templates.TemplateResponse(request, "calendar/calendar.html", ctx)
