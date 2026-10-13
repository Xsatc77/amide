"""Printable vial labels: for a checked-in order, and one for a vial that was just reconstituted."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.inventory.label_layout import Layout, css as custom_css, per_page
from app.inventory.labels import DEFAULT_SIZE, LABEL_SIZES, Label, label_for_vial, labels_for_order, page_rule
from app.models import ActiveVial, Order, User
from app.templating import templates

router = APIRouter()


def _next_page(raw: str | None) -> str:
    """Where to go after printing: only a page inside the inventory, never an outside address."""
    return raw if raw and raw.startswith("/inventory") and "//" not in raw and "\\" not in raw else "/inventory"


def _custom_layout(user: User) -> Layout | None:
    try:
        return Layout.from_dict(user.label_layout) if user.label_layout else None
    except (KeyError, TypeError):
        return None


def _sheet(request: Request, session: Session, uid: int, labels: list, title: str):
    user = session.get(User, uid)
    size = user.label_size if user.label_size in LABEL_SIZES else DEFAULT_SIZE
    layout = _custom_layout(user) if size == "custom" else None
    if size == "custom" and layout is None:
        size = DEFAULT_SIZE                      # chosen but never filled in: print on the standard sheet rather than fail
    context = {
        "labels": labels, "title": title, "size": size, "page_rule": page_rule(size), "auto": request.query_params.get("auto") == "1",
        "next_page": _next_page(request.query_params.get("next")), "custom_css": "", "pages": None, "shape": "rect",
    }
    if layout is not None:
        n = per_page(layout)
        context.update(custom_css=custom_css(layout), pages=[labels[i:i + n] for i in range(0, len(labels), n)] or [[]], shape=layout.shape)
    return templates.TemplateResponse(request, "inventory/labels.html", context)


@router.get("/inventory/labels/test")
def test_sheet(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """A sheet of sample labels in the person's custom layout, to check the alignment on plain paper before using real labels."""
    layout = _custom_layout(session.get(User, uid))
    if layout is None:
        raise HTTPException(404, "Set up a custom label sheet in Settings first")
    samples = [Label(name="Test peptide", size="5 mg", lot="LOT123", received=date.today()) for _ in range(per_page(layout))]
    return _sheet(request, session, uid, samples, "Label test sheet")


@router.get("/inventory/orders/{order_id}/labels")
def order_labels(order_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = session.get(Order, order_id)
    labels = labels_for_order(order, uid) if order is not None else []
    if not labels:
        raise HTTPException(404, "No labels to print for this order")
    return _sheet(request, session, uid, labels, "Vial labels")


@router.get("/active-vials/{vial_id}/label")
def vial_label(vial_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    vial = session.get(ActiveVial, vial_id)
    if vial is None or vial.owner_id != uid:
        raise HTTPException(404, "Vial not found")
    return _sheet(request, session, uid, [label_for_vial(vial)], "Vial label")
