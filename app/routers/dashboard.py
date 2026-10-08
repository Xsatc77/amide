"""The Dashboard: the app's homepage -- today's schedule, alerts, cost/adherence snapshots, and
placeholders for not-yet-built widgets. Read-only; every widget reuses an existing query shape."""

import types
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import compliance, units
from app.alerts import expiration_alerts, low_stock_alerts, shipment_alerts
from app.ingest.alerts import dismiss as dismiss_alert, group_gone_alerts, new_list_alerts
from app.inventory.runout import ALERT_DAYS as RUNOUT_ALERT_DAYS, runs_out
from app.library.price_lists.analysis import new_peptides
from app.auth.deps import current_user_id
from app.calendar.schedule import occurrences
from app.db import get_session
from app.models import (
    TIME_ORDER, ActiveVial, BodyMeasurement, Category, DoseLog, DoseStatus, InventoryItem, Order, OrderItem,
    Protocol, ProtocolItem, Share, ShareCategory, User, WaterLog, naive_utcnow,
)
from app.protocols.status import Status, protocol_status
from app.routers.protocols import get_today
from app.templating import templates

router = APIRouter()


def _resolve_viewer(session: Session, uid: int, viewer_id: int | None) -> tuple[int, set[ShareCategory]]:
    """Returns (effective_viewer_id, categories_shared_by_that_viewer_with_uid). Falls back to
    (uid, {both categories}) when viewer_id is None or the share no longer exists -- a revoked
    share silently reverts to self rather than erroring, since the dropdown itself won't offer a
    stale option on the next render anyway."""
    if viewer_id is None or viewer_id == uid:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    categories = set(session.scalars(
        select(Share.category).where(Share.owner_id == viewer_id, Share.grantee_id == uid)))
    if not categories:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    return viewer_id, categories


def _shared_with_me(session: Session, uid: int) -> list[dict]:
    rows = session.execute(
        select(Share.owner_id, User.username).join(User, User.id == Share.owner_id)
        .where(Share.grantee_id == uid).distinct()).all()
    return [{"id": owner_id, "username": username} for owner_id, username in rows]


def _todays_schedule(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    due = sorted(((occ, item) for occ in occs for item in occ.items), key=lambda pair: TIME_ORDER[pair[1].time_of_day])
    step_counts = {i.id: len(i.steps) for p in protocols if p.titration_enabled for i in p.items}
    logs = {dl.protocol_item_id: dl for dl in session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today))}
    rows = []
    for occ, item in due:
        log = logs.get(item.protocol_item_id)
        status = "Due"
        if log is not None:
            status = "Logged" if log.status in (DoseStatus.ON_TIME, DoseStatus.LATE) else "Skipped"
        total = step_counts.get(item.protocol_item_id, 0)
        rows.append({"peptide": item.peptide, "dose": item.dose, "unit": item.unit,
                    "time_of_day": item.time_of_day, "status": status,
                    "step": f"Step {item.step} of {total}" if item.step and total > 1 else None})
    return rows


def _runout_alerts(session: Session, uid: int, today: date) -> list[dict]:
    """Stock an active protocol will use up within the alert window, with nothing on order to replace it (needs the protocols, so Personal data)."""
    on_order = _items_on_order(session, uid)
    names = {i.id: i.name for i in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid))}
    soon = [(item_id, r) for item_id, r in runs_out(session, uid, today).items() if r.days <= RUNOUT_ALERT_DAYS and item_id not in on_order]
    return [{"id": item_id, "name": names[item_id], "days": r.days, "date": r.date} for item_id, r in sorted(soon, key=lambda x: x[1].days)]


def _items_on_order(session: Session, uid: int) -> set[int]:
    """Ids of `uid`'s items with an order line on an order that has not arrived (been checked in) yet."""
    return set(session.scalars(
        select(OrderItem.inventory_item_id)
        .join(Order, OrderItem.order_id == Order.id)
        .join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.owner_id == uid, Order.arrival_date.is_(None))))


def _in_transit_groups(session: Session, uid: int) -> list[dict]:
    """Unarrived orders grouped by order, same scope as the Inventory page's In-transit table:
    only `uid`'s own Medicine / BAC Water lines (an order can span users' items; never expose
    another user's line)."""
    rows = session.execute(
        select(InventoryItem, OrderItem)
        .join(OrderItem, OrderItem.inventory_item_id == InventoryItem.id)
        .join(Order, OrderItem.order_id == Order.id)
        .where(InventoryItem.owner_id == uid,
               InventoryItem.category.in_([Category.MEDICINE, Category.BAC_WATER]),
               Order.arrival_date.is_(None))
        .order_by(Order.order_date.desc(), Order.id.desc(), OrderItem.id)).all()
    by_order: dict[int, list] = {}
    for item, line in rows:
        by_order.setdefault(line.order_id, []).append((item, line))
    return [{"order": lines[0][1].order, "lines": lines} for lines in by_order.values()]


def _cost_snapshot(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    active_items = [
        it for p in protocols if protocol_status(p, today) is Status.ACTIVE
        for it in p.items if it.inventory_item_id is not None
    ]
    rows = []
    seen_item_ids = set()
    for it in active_items:
        if it.inventory_item_id in seen_item_ids:
            continue
        seen_item_ids.add(it.inventory_item_id)
        line = session.scalar(
            select(OrderItem).join(Order, OrderItem.order_id == Order.id)
            .where(OrderItem.inventory_item_id == it.inventory_item_id, Order.arrival_date.is_not(None))
            .order_by(Order.arrival_date.desc()))
        if line is None or line.total_cost is None or not line.received_quantity:
            continue
        cost_per_vial = line.total_cost / line.received_quantity
        vial = session.scalar(
            select(ActiveVial).where(ActiveVial.inventory_item_id == it.inventory_item_id,
                                     ActiveVial.discarded_at.is_(None))
            .order_by(ActiveVial.id.desc()))
        cost_per_dose = cost_per_vial / vial.doses_total if vial and vial.doses_total else None
        rows.append({"peptide": it.peptide.name, "cost_per_vial": cost_per_vial,
                    "cost_per_dose": cost_per_dose})
    return rows


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
             uid: int = Depends(current_user_id), viewer_id: str | None = Query(None)):
    # A plain HTML <select> submits its empty "You" option as `viewer_id=` (empty string), which
    # `int | None` query typing would reject with a 422 -- parse manually so both "absent" and
    # "present but empty" mean "no override, view yourself." A garbled, non-numeric value (never
    # produced by our own dropdown, but possible from a hand-typed URL) is treated the same way
    # rather than raising -- consistent with _resolve_viewer's own silent-fallback-to-self
    # philosophy for any other invalid/stale viewer_id.
    try:
        viewer_id_int = int(viewer_id) if viewer_id else None
        # A huge numeric string (e.g. from a hand-typed URL) parses fine as a Python int -- ints
        # are unbounded -- but then overflows when SQLAlchemy binds it against SQLite's integer
        # column. Bound-check against a normal signed 64-bit range and fall back to self, same as
        # the non-numeric case just below.
        if viewer_id_int is not None and abs(viewer_id_int) > 2**63 - 1:
            viewer_id_int = None
    except (ValueError, OverflowError):
        viewer_id_int = None
    effective_uid, categories = _resolve_viewer(session, uid, viewer_id_int)

    schedule = None
    compliance_view = None
    water = None
    body_panel = None
    workout_week = None
    if ShareCategory.PERSONAL_DATA in categories:
        schedule = _todays_schedule(session, effective_uid, today)
        window = compliance.parse_window(request.query_params.get("compliance"))
        compliance_view = {
            "bars": compliance.bars(session, session.get(User, effective_uid), today, window),
            "windows": [{"value": "lifetime" if w == 0 else w, "label": compliance.window_label(w), "selected": w == window} for w in compliance.WINDOWS],
            "viewer_id": effective_uid if effective_uid != uid else None,
        }

        # A 7-day Mon-Sun strip, not a "due today" list -- see week_status's own docstring for why
        # (a day stays visible with its outcome instead of vanishing once it's logged).
        from app.routers.workouts import week_status
        workout_week = week_status(session, effective_uid, today)

        # Same computation as the Macros tab's own Water goal, plus the same Weight chart/Body
        # Silhouette the Measurements page's Overview row shows -- reused via deferred import
        # (measurements.py imports this module's own helpers at load time, so importing the other
        # direction up top would be circular) rather than duplicating that logic a second time.
        from app.routers.measurements import (
            DEFAULT_RANGE, RANGE_DAYS, _chart, _field_current_and_delta, _silhouette_points,
            _silhouette_shape, display_entries,
        )
        from app.measurements.calculations import water_goal_oz, water_pace
        entries = session.scalars(
            select(BodyMeasurement).where(BodyMeasurement.owner_id == effective_uid)
            .order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc())).all()
        latest_weight, _, _, weight_as_of = _field_current_and_delta(entries, "weight_lbs")
        viewer = session.get(User, effective_uid)
        if latest_weight is not None:
            goal_oz = water_goal_oz(latest_weight, viewer.water_goal_oz if viewer else None)
            consumed_oz = session.scalar(
                select(func.sum(WaterLog.ounces)).where(
                    WaterLog.owner_id == effective_uid, WaterLog.logged_at == today)) or 0
            water = {"goal_oz": goal_oz, "pace": water_pace(goal_oz), "weight_as_of": weight_as_of,
                    "consumed_oz": consumed_oz,
                    "pct": min(100, round(100 * consumed_oz / goal_oz)) if goal_oz else 0}

        # Always rendered (like Schedule/Alerts' own "nothing yet" states) rather than hidden
        # outright with no data -- fixed to the Overview's own default range/metric (Weight),
        # since the Dashboard is a glance, not the interactive metric/range picker the full
        # Measurements page has.
        cutoff = today - timedelta(days=RANGE_DAYS[DEFAULT_RANGE])
        windowed = [e for e in entries if e.measured_at >= cutoff]
        u = units.for_user(request.state.user)
        shown_windowed = display_entries(windowed, u)
        body_panel = {
            "chart": _chart([(e.measured_at, e.weight_lbs) for e in shown_windowed if e.weight_lbs is not None]),
            "silhouette": _silhouette_points(display_entries(entries, u), u.length_label) if entries else None,
            "silhouette_shape": _silhouette_shape(viewer.sex.value if viewer and viewer.sex else None),
        }

    alerts = None
    cost_snapshot = None
    in_transit_groups = None
    show_cost_snapshot = False
    if ShareCategory.INVENTORY in categories:
        threshold_items = session.scalars(
            select(InventoryItem).where(InventoryItem.owner_id == effective_uid,
                                        InventoryItem.category.in_([Category.MEDICINE, Category.BAC_WATER]))).all()
        viewer = session.get(User, effective_uid)
        default_threshold = viewer.low_stock_default if viewer.low_stock_default is not None else 5
        delay_days = viewer.shipment_delay_days if viewer.shipment_delay_days is not None else 21

        vials = session.scalars(
            select(ActiveVial).where(ActiveVial.owner_id == effective_uid, ActiveVial.discarded_at.is_(None))).all()
        for v in vials:
            v.item_name = v.inventory_item.name  # convenience attr expected by app.alerts

        # Orders have no owner_id of their own -- scope via their line items' linked InventoryItem.
        order_ids = {li.order_id for li in session.scalars(
            select(OrderItem).join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
            .where(InventoryItem.owner_id == effective_uid))}
        orders = session.scalars(select(Order).where(Order.id.in_(order_ids))).all() if order_ids else []

        # Sealed-stock expiration: InventoryItem.expiration_date is dead -- nothing in the app
        # writes it (see app/routers/inventory.py's ITEM_FIELDS). The real per-lot expiration lives
        # on OrderItem, filled in at order creation and still meaningful once the order arrives.
        # Build a small stand-in per item with the earliest arrived-line expiration date, so
        # expiration_alerts() (a pure function that expects `.expiration_date` on each item) keeps
        # working unchanged -- only an arrived line counts, an in-transit line's date isn't real
        # stock yet.
        expiration_items = []
        for item in threshold_items:
            earliest = session.scalar(
                select(func.min(OrderItem.expiration_date)).join(Order, OrderItem.order_id == Order.id)
                .where(OrderItem.inventory_item_id == item.id, Order.arrival_date.is_not(None),
                      OrderItem.expiration_date.is_not(None)))
            expiration_items.append(types.SimpleNamespace(
                id=item.id, name=item.name, available_count=item.available_count, expiration_date=earliest))

        alerts = {
            "low_stock": low_stock_alerts(threshold_items, default_threshold, on_order=_items_on_order(session, effective_uid)),
            "expiration": expiration_alerts(vials=vials, items=expiration_items, today=today),
            "shipment": shipment_alerts(orders, today=today, threshold_days=delay_days),
            "runout": _runout_alerts(session, effective_uid, today) if ShareCategory.PERSONAL_DATA in categories else [],
            "new_peptides": new_peptides(session),
            "new_lists": new_list_alerts(session, uid, naive_utcnow()),
            "groups_gone": group_gone_alerts(session, session.get(User, uid)),
        }
        in_transit_groups = _in_transit_groups(session, effective_uid)
        # Cost snapshot enumerates the viewer's *active protocols* (which peptides they're
        # currently running) -- that's PERSONAL_DATA information, not INVENTORY, even though the
        # widget lives in the Inventory-gated section. An Inventory-only grantee must not be able
        # to infer it. Self-view is exempt: you always have full access to your own data regardless
        # of any share, so INVENTORY alone (this block's own gate) stays sufficient there.
        show_cost_snapshot = effective_uid == uid or ShareCategory.PERSONAL_DATA in categories
        if show_cost_snapshot:
            cost_snapshot = _cost_snapshot(session, effective_uid, today)

    return templates.TemplateResponse(request, "dashboard/index.html", {
        "schedule": schedule,
        "alerts": alerts,
        "can_ignore_alerts": bool(request.state.user.is_admin),
        "cost_snapshot": cost_snapshot,
        "compliance": compliance_view,
        "water": water,
        "body_panel": body_panel,
        "workout_week": workout_week,
        "today": today,
        "viewer_id": effective_uid,
        "in_transit_groups": in_transit_groups,
        "shared_with_me": _shared_with_me(session, uid),
        # Separate flags rather than reusing `schedule`/`adherence_pct is None` to mean "not
        # shared" -- `_adherence_pct` already returns None for "no doses logged in the window,"
        # which is a real, shared-and-empty state distinct from "this category isn't shared at
        # all." Conflating the two would hide the widget for someone who *did* share but has no
        # doses logged yet.
        "show_personal_data": ShareCategory.PERSONAL_DATA in categories,
        "show_inventory": ShareCategory.INVENTORY in categories,
        # Alerts stays gated on INVENTORY alone (`show_inventory`, above); Cost snapshot needs the
        # additional PERSONAL_DATA requirement when viewing someone else -- see the privacy note
        # by `show_cost_snapshot`'s computation above.
        "show_cost_snapshot": show_cost_snapshot,
        # Quick-capture always writes as the signed-in user (uid), never the effective_uid being
        # viewed -- only show it when you're looking at your own dashboard, so it's never mistaken
        # for adding a note to someone else's journal.
        "show_quick_capture": effective_uid == uid,
    })


@router.post("/dashboard/water/log")
async def log_water(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
                    uid: int = Depends(current_user_id)):
    """Always writes as the signed-in user (uid), never a viewed effective_uid -- same rule as the
    Journal quick-capture. A blank/non-numeric/non-positive amount is a silent no-op, matching the
    quick-note's own "bounce back as if nothing was submitted" behavior for bad input."""
    form = await request.form()
    try:
        ounces = units.for_user(request.state.user).volume_in(float(form.get("ounces", "")))      # typed in the person's units
    except (TypeError, ValueError):
        ounces = None
    if ounces is not None and ounces > 0:
        session.add(WaterLog(owner_id=uid, logged_at=today, ounces=ounces))
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/dashboard/alerts/dismiss")
async def dismiss_ingest_alert(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Dismiss a price-list alert for the signed-in person only (the group-gone alert is acknowledged by the administrator)."""
    form = await request.form()
    try:
        dismiss_alert(session, session.get(User, uid), str(form.get("key") or ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="Unknown alert.") from None
    return RedirectResponse("/dashboard", status_code=303)
