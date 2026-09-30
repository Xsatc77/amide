from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.calendar.schedule import missed_items, occurrences
from app.db import get_session
from app.goals import GOALS, GOALS_BY_SLUG
from app.models import (
    WEEKDAY_LETTERS, WEEKDAY_NAMES, DoseLog, DoseUnit, Frequency, GoalPeptide, InventoryItem, Peptide, PeptideSource,
    Protocol, ProtocolGoal, ProtocolItem, ProtocolItemCycleOff, Route, Share, ShareCategory, TimeOfDay, TitrationStep,
    User,
)
from app.protocols.forms import (
    ParsedProtocol, blank_state, parse_protocol_form, state_from_form, state_from_protocol,
)
from app.protocols.status import Status, current_step, current_week, day_number, protocol_status
from app.templating import templates

router = APIRouter()

# How far back the Protocol page's catch-up ("recent Missed items") list looks, regardless of how
# long ago the protocol's start_date was -- see edit_protocol.
CATCH_UP_WINDOW_DAYS = 14


def get_today() -> date:
    """Today's date; a dependency so tests can pin it."""
    return date.today()


def _protocol_query(uid: int):
    """This user's protocols (others' are never visible)."""
    return select(Protocol).where(Protocol.owner_id == uid).options(
        selectinload(Protocol.goals),
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
        selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
    )


def _shared_protocol_query(uid: int):
    """Protocols owned by anyone who granted this user Personal Data sharing."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.PERSONAL_DATA)
    return select(Protocol).where(Protocol.owner_id.in_(shared_owner_ids)).options(
        selectinload(Protocol.goals),
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
        selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
    )


def _get_protocol(session: Session, protocol_id: int, uid: int) -> Protocol:
    p = session.scalar(_protocol_query(uid).where(Protocol.id == protocol_id))
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


def _view(p: Protocol, today: date, owner_name: str | None = None) -> dict:
    week = current_week(p.start_date, today)
    return {
        "p": p,
        "status": protocol_status(p, today),
        "day": day_number(p.start_date, today),
        "owner_name": owner_name,
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
def list_protocols(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    protocols = session.scalars(_protocol_query(uid).order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    views = [_view(p, today) for p in protocols]
    # Active, Paused, and Scheduled protocols all get a card in the Active Protocols section (each
    # its own banner color) -- only Ended protocols drop to Saved. Within the section, cards group
    # by status in this fixed order, each group sorted by start date.
    active = []
    for status in (Status.ACTIVE, Status.PAUSED, Status.SCHEDULED):
        active += sorted((v for v in views if v["status"] is status), key=lambda v: v["p"].start_date)
    saved = [v for v in views if v["status"] is Status.ENDED]

    shared_protocols = session.scalars(
        _shared_protocol_query(uid).order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    owner_ids = {p.owner_id for p in shared_protocols}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())
    shared_views = [_view(p, today, owner_name=owner_names.get(p.owner_id)) for p in shared_protocols]

    return templates.TemplateResponse(request, "protocols/list.html",
                                      {"active_views": active, "saved_views": saved, "shared_views": shared_views,
                                       "goals": GOALS, "statuses": list(Status)})


# ---------------------------------------------------------------- builder

def _builder_data(session: Session, state: dict, errors: dict, *, is_new: bool, uid: int) -> dict:
    stacks: dict[str, list[int]] = {g.slug: [] for g in GOALS}
    for gp in session.scalars(select(GoalPeptide).order_by(GoalPeptide.goal, GoalPeptide.position)):
        stacks.setdefault(gp.goal, []).append(gp.peptide_id)
    peptides = session.scalars(select(Peptide).order_by(Peptide.name)).all()
    inventory = session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid)
                                .order_by(InventoryItem.name)).all()

    def choices(enum_cls):
        return [[m.value, m.label] for m in enum_cls]

    return {
        "state": state,
        "errors": errors,
        "is_new": is_new,
        "goals": [{"slug": g.slug, "label": g.label, "description": g.description} for g in GOALS],
        "stacks": stacks,
        "peptides": [{"id": pp.id, "name": pp.name, "aliases": pp.aliases, "card_class": pp.card_class,
                      "card_number": pp.card_number} for pp in peptides],
        "inventory": [
            {"id": i.id, "name": i.name, "vial_size_mg": i.vial_size_mg, "medium": i.medium.value if i.medium else None}
            for i in inventory
        ],
        "options": {
            "dose_unit": choices(DoseUnit),
            "frequency": choices(Frequency),
            "time_of_day": choices(TimeOfDay),
            "route": choices(Route),
            "weekdays": [[d, WEEKDAY_NAMES[d]] for d in WEEKDAY_LETTERS],
        },
    }


def _render_builder(request: Request, session: Session, state: dict, *, errors: dict | None = None,
                    protocol: Protocol | None = None, repeat_of: Protocol | None = None,
                    today: date, status_code: int = 200,
                    dose_history: list | None = None, missed: list | None = None):
    is_new = protocol is None
    return templates.TemplateResponse(
        request,
        "protocols/builder.html",
        {
            "state": state,
            "errors": errors or {},
            "protocol": protocol,
            "repeat_of": repeat_of,
            "status": protocol_status(protocol, today) if protocol else None,
            "action": "/protocols" if is_new else f"/protocols/{protocol.id}",
            "builder_data": _builder_data(session, state, errors or {}, is_new=is_new, uid=request.state.user.id),
            "dose_history": dose_history or [],
            "missed": missed or [],
        },
        status_code=status_code,
    )


async def _read_form(request: Request) -> dict[str, list[str]]:
    form = await request.form()
    return {key: [str(v) for v in form.getlist(key)] for key in form.keys()}


def _find_or_create_peptide(session: Session, name: str) -> Peptide:
    # The name column uses NOCASE collation, so this match ignores case.
    existing = session.scalar(select(Peptide).where(Peptide.name == name))
    if existing:
        return existing
    peptide = Peptide(name=name, source=PeptideSource.CUSTOM)
    session.add(peptide)
    session.flush()
    return peptide


def save_protocol(session: Session, p: Protocol, parsed: ParsedProtocol) -> Protocol:
    """Write validated builder values onto `p`, replacing its goals, peptides and titration steps.

    Every edit clears and rebuilds all ProtocolItem rows below (simplest way to reconcile arbitrary
    add/remove/reorder from the builder form) -- but DoseLog.protocol_item_id has
    ondelete="SET NULL", so without special handling every previously-logged dose would go orphaned
    on ANY edit, even a no-op rename: Calendar's adherence, the Protocol page's catch-up list, and
    Today's already-logged-today filter all match exclusively on protocol_item_id, so a NULLed row
    would silently look unlogged again. Before the old items are deleted, each one's DoseLog rows
    are re-pointed to whichever NEW item is its best match -- identified by (peptide_id,
    time_of_day), the stable "what this represents" identity across an edit even when dose,
    frequency or route changed. NULL is still correct when nothing in the edited protocol matches
    that identity any more (e.g. the peptide was removed)."""
    p.name = parsed.name
    p.start_date = parsed.start_date
    p.end_date = parsed.end_date
    p.notes = parsed.notes
    p.titration_enabled = parsed.titration_enabled

    logs_by_old_key: dict[tuple[int, TimeOfDay], list[int]] = {}
    if p.id is not None:
        old_key_by_item_id = {it.id: (it.peptide_id, it.time_of_day) for it in p.items}
        if old_key_by_item_id:
            old_logs = session.scalars(
                select(DoseLog).where(DoseLog.protocol_id == p.id,
                                      DoseLog.protocol_item_id.in_(list(old_key_by_item_id)))).all()
            for log in old_logs:
                key = old_key_by_item_id[log.protocol_item_id]
                logs_by_old_key.setdefault(key, []).append(log.id)

    if p.id is None:
        session.add(p)
    else:
        # Remove the old rows first so re-adding the same goal doesn't collide on its primary key.
        p.goals.clear()
        p.items.clear()
        session.flush()

    p.goals = [ProtocolGoal(goal=g) for g in parsed.goals]
    new_items = []
    for position, it in enumerate(parsed.items):
        peptide_id = it.peptide_id if it.peptide_id is not None else _find_or_create_peptide(session, it.new_name).id
        new_item = ProtocolItem(
            peptide_id=peptide_id, position=position, dose=it.dose, dose_unit=it.dose_unit,
            frequency=it.frequency, every_n_days=it.every_n_days, weekdays=it.weekdays,
            time_of_day=it.time_of_day, route=it.route, inventory_item_id=it.inventory_item_id, notes=it.notes,
            steps=[TitrationStep(start_week=s.start_week, end_week=s.end_week, dose=s.dose) for s in it.steps],
            cycle_offs=[ProtocolItemCycleOff(start_week=c.start_week, end_week=c.end_week)
                       for c in it.cycle_offs],
        )
        p.items.append(new_item)
        new_items.append(new_item)

    if logs_by_old_key:
        session.flush()  # new_items need real, persisted ids before DoseLog rows can point at them
        new_item_id_by_key: dict[tuple[int, TimeOfDay], int] = {}
        for new_item in new_items:
            new_item_id_by_key.setdefault((new_item.peptide_id, new_item.time_of_day), new_item.id)
        for key, log_ids in logs_by_old_key.items():
            new_item_id = new_item_id_by_key.get(key)  # None is correct: nothing left in the protocol matches
            # A bulk UPDATE, not session.get()-then-set: the DELETE above already triggered the DB's
            # own ondelete="SET NULL" for these rows at the database level, but any of them still
            # cached in this session's identity map from the old_logs query above wouldn't reflect
            # that -- if the new value happens to equal what was already in memory (e.g. an id SQLite
            # reused), the ORM would see "no change" and silently skip writing it, leaving the
            # database's NULL in place despite the object looking correct in memory.
            session.execute(update(DoseLog).where(DoseLog.id.in_(log_ids)).values(protocol_item_id=new_item_id))

    session.commit()
    return p


def _parse(session: Session, form: dict[str, list[str]], uid: int):
    return parse_protocol_form(
        form,
        peptide_ids=set(session.scalars(select(Peptide.id))),
        inventory_ids=set(session.scalars(select(InventoryItem.id).where(InventoryItem.owner_id == uid))),
    )


@router.get("/protocols/new")
def new_protocol(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    goals = [g for g in dict.fromkeys(request.query_params.getlist("goal")) if g in GOALS_BY_SLUG]
    return _render_builder(request, session, blank_state(goals, today), today=today)


@router.post("/protocols")
async def create_protocol(request: Request, session: Session = Depends(get_session),
                          today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    form = await _read_form(request)
    parsed, errors = _parse(session, form, uid)
    if errors:
        return _render_builder(request, session, state_from_form(form), errors=errors, today=today, status_code=422)
    save_protocol(session, Protocol(owner_id=uid), parsed)
    return RedirectResponse("/protocols", status_code=303)


@router.get("/protocols/{protocol_id}/edit")
def edit_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session),
                  today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    p = _get_protocol(session, protocol_id, uid)
    dose_history = session.scalars(
        select(DoseLog).where(DoseLog.protocol_id == p.id).order_by(DoseLog.scheduled_date.desc())).all()
    # The catch-up list is meant to be "recent Missed items" (the spec's own wording), not one row
    # per missed day since the protocol started -- bound the occurrences window used to compute it
    # to the last CATCH_UP_WINDOW_DAYS, regardless of how far in the past start_date is. This only
    # affects `missed`; dose_history above is unrelated (queries DoseLog directly) and must keep
    # showing full history.
    catch_up_start = max(p.start_date, today - timedelta(days=CATCH_UP_WINDOW_DAYS))
    occs = occurrences([p], catch_up_start, today)
    logged = {(dl.protocol_item_id, dl.scheduled_date) for dl in dose_history if dl.protocol_item_id is not None}
    missed = missed_items(occs, logged, today)
    return _render_builder(request, session, state_from_protocol(p), protocol=p, today=today,
                           dose_history=dose_history, missed=missed)


@router.post("/protocols/{protocol_id}")
async def update_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session),
                          today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    p = _get_protocol(session, protocol_id, uid)
    form = await _read_form(request)
    parsed, errors = _parse(session, form, uid)
    if errors:
        return _render_builder(request, session, state_from_form(form), errors=errors, protocol=p, today=today,
                               status_code=422)
    save_protocol(session, p, parsed)
    return RedirectResponse("/protocols", status_code=303)


@router.get("/protocols/{protocol_id}/repeat")
def repeat_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session),
                    today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    """Builder pre-filled from an existing protocol as a new, unsaved one."""
    p = _get_protocol(session, protocol_id, uid)
    return _render_builder(request, session, state_from_protocol(p, repeat=True, today=today),
                           repeat_of=p, today=today)


# ---------------------------------------------------------------- actions

def _back(protocol_id: int, next_page: str) -> RedirectResponse:
    target = f"/protocols/{protocol_id}/edit" if next_page == "edit" else "/protocols"
    return RedirectResponse(target, status_code=303)


@router.post("/protocols/{protocol_id}/pause")
def pause_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session),
        uid: int = Depends(current_user_id)):
    _get_protocol(session, protocol_id, uid).paused = True
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/resume")
def resume_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session),
        uid: int = Depends(current_user_id)):
    _get_protocol(session, protocol_id, uid).paused = False
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/end")
def end_protocol(protocol_id: int, next: str = Form(""), session: Session = Depends(get_session),
                 today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    _get_protocol(session, protocol_id, uid).ended_on = today
    session.commit()
    return _back(protocol_id, next)


@router.post("/protocols/{protocol_id}/delete")
def delete_protocol(protocol_id: int, session: Session = Depends(get_session),
        uid: int = Depends(current_user_id)):
    session.delete(_get_protocol(session, protocol_id, uid))
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
def api_list_protocols(session: Session = Depends(get_session), today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    protocols = session.scalars(_protocol_query(uid).order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    return [_protocol_json(p, today) for p in protocols]


@router.get("/api/protocols/{protocol_id}")
def api_get_protocol(protocol_id: int, session: Session = Depends(get_session), today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    return _protocol_json(_get_protocol(session, protocol_id, uid), today)


@router.get("/api/peptides")
def api_list_peptides(session: Session = Depends(get_session)):
    peptides = session.scalars(select(Peptide).order_by(Peptide.name)).all()
    return [
        {
            "id": pp.id, "name": pp.name, "aliases": pp.aliases, "card_number": pp.card_number,
            "dose_low": pp.dose_low, "dose_mid": pp.dose_mid, "dose_high": pp.dose_high,
            "dose_unit": pp.dose_unit.value if pp.dose_unit else None,
            "typical_frequency": pp.typical_frequency, "source": pp.source.value,
            "card_class": pp.card_class, "category": pp.category, "evidence_level": pp.evidence_level,
            "status": pp.status, "has_card": pp.card_details is not None,
        }
        for pp in peptides
    ]
