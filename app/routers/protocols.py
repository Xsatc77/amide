from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.goals import GOALS
from app.models import (
    WEEKDAY_NAMES, Frequency, Peptide, Protocol, ProtocolItem, TimeOfDay, TitrationStep,
)
from app.protocols.status import Status, current_step, current_week, day_number, protocol_status
from app.templating import templates

router = APIRouter()


def get_today() -> date:
    """Today's date; a dependency so tests can pin it."""
    return date.today()


def _protocol_query():
    return select(Protocol).options(
        selectinload(Protocol.goals),
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
        selectinload(Protocol.items).selectinload(ProtocolItem.steps),
    )


def _get_protocol(session: Session, protocol_id: int) -> Protocol:
    p = session.scalar(_protocol_query().where(Protocol.id == protocol_id))
    if p is None:
        raise HTTPException(404, "Protocol not found")
    return p


# ---------------------------------------------------------------- display helpers

def _amount(value: float, unit) -> str:
    return f"{value:g} {unit.value}"


def describe_item(item: ProtocolItem) -> str:
    """e.g. "250 mcg · Daily · AM · SubQ"."""
    parts = [_amount(item.dose, item.dose_unit) if item.dose is not None else "Dose not set"]
    if item.frequency is Frequency.EVERY_N_DAYS and item.every_n_days:
        parts.append(f"Every {item.every_n_days} days")
    elif item.frequency is Frequency.WEEKDAYS and item.weekdays:
        parts.append(", ".join(WEEKDAY_NAMES[d] for d in item.weekdays))
    else:
        parts.append(item.frequency.label)
    if item.time_of_day is not TimeOfDay.ANY:
        parts.append(item.time_of_day.label)
    parts.append(item.route.label)
    return " · ".join(parts)


def _step_text(item: ProtocolItem, week: int | None) -> str | None:
    step = current_step(item.steps, week)
    if step is None:
        return None
    number = item.steps.index(step) + 1
    return f"Week {week} · step {number}: {_amount(step.dose, item.dose_unit)}"


def _view(p: Protocol, today: date) -> dict:
    week = current_week(p.start_date, today)
    return {
        "p": p,
        "status": protocol_status(p, today),
        "day": day_number(p.start_date, today),
        "items": [
            {
                "name": it.peptide.name,
                "dose_set": it.dose is not None,
                "desc": describe_item(it),
                "step": _step_text(it, week) if p.titration_enabled else None,
            }
            for it in p.items
        ],
    }


# ---------------------------------------------------------------- pages

@router.get("/protocols")
def list_protocols(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today)):
    protocols = session.scalars(_protocol_query().order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    views = [_view(p, today) for p in protocols]
    active = sorted((v for v in views if v["status"] is Status.ACTIVE), key=lambda v: v["p"].start_date)
    saved = [v for v in views if v["status"] is not Status.ACTIVE]
    return templates.TemplateResponse(request, "protocols/list.html",
                                      {"active_views": active, "saved_views": saved, "goals": GOALS, "statuses": list(Status)})


# ---------------------------------------------------------------- actions

def _back(protocol_id: int, next_page: str) -> RedirectResponse:
    target = f"/protocols/{protocol_id}/edit" if next_page == "edit" else "/protocols"
    return RedirectResponse(target, status_code=303)


@router.post("/protocols/{protocol_id}/pause")
def pause_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session)):
    _get_protocol(session, protocol_id).paused = True
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/resume")
def resume_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session)):
    _get_protocol(session, protocol_id).paused = False
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/end")
def end_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session),
                 today: date = Depends(get_today)):
    _get_protocol(session, protocol_id).ended_on = today
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/delete")
def delete_protocol(protocol_id: int, session: Session = Depends(get_session)):
    session.delete(_get_protocol(session, protocol_id))
    session.commit()
    return RedirectResponse("/protocols", status_code=303)


# ---------------------------------------------------------------- JSON API

def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _step_json(s: TitrationStep) -> dict:
    return {"start_week": s.start_week, "end_week": s.end_week, "dose": s.dose}


def _protocol_json(p: Protocol, today: date) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "status": protocol_status(p, today).value,
        "goals": p.goal_slugs,
        "start_date": _iso(p.start_date),
        "end_date": _iso(p.end_date),
        "ended_on": _iso(p.ended_on),
        "paused": p.paused,
        "titration_enabled": p.titration_enabled,
        "notes": p.notes,
        "items": [
            {
                "id": it.id,
                "peptide_id": it.peptide_id,
                "peptide": it.peptide.name,
                "dose": it.dose,
                "dose_unit": it.dose_unit.value,
                "frequency": it.frequency.value,
                "every_n_days": it.every_n_days,
                "weekdays": it.weekdays,
                "time_of_day": it.time_of_day.value,
                "route": it.route.value,
                "inventory_item_id": it.inventory_item_id,
                "notes": it.notes,
                "steps": [_step_json(s) for s in it.steps] if p.titration_enabled else [],
            }
            for it in p.items
        ],
    }


@router.get("/api/protocols")
def api_list_protocols(session: Session = Depends(get_session), today: date = Depends(get_today)):
    protocols = session.scalars(_protocol_query().order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    return [_protocol_json(p, today) for p in protocols]


@router.get("/api/protocols/{protocol_id}")
def api_get_protocol(protocol_id: int, session: Session = Depends(get_session), today: date = Depends(get_today)):
    return _protocol_json(_get_protocol(session, protocol_id), today)


@router.get("/api/peptides")
def api_list_peptides(session: Session = Depends(get_session)):
    peptides = session.scalars(select(Peptide).order_by(Peptide.name)).all()
    return [
        {
            "id": pp.id, "name": pp.name, "aliases": pp.aliases, "card_number": pp.card_number,
            "dose_low": pp.dose_low, "dose_mid": pp.dose_mid, "dose_high": pp.dose_high,
            "dose_unit": pp.dose_unit.value if pp.dose_unit else None,
            "typical_frequency": pp.typical_frequency, "source": pp.source.value,
        }
        for pp in peptides
    ]
