"""Printable vial labels: for a checked-in order, and one for a vial that was just reconstituted."""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.inventory import avery94500, symbols
from app.inventory.label_layout import Layout, css as custom_css, per_page
from app.inventory.labels import DEFAULT_SIZE, LABEL_SIZES, Label, label_for_vial, labels_for_order, page_rule
from app.models import ActiveVial, Category, InventoryItem, Order, User
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


def _sheet(request: Request, session: Session, uid: int, labels: list, title: str, order_id: int | None = None):
    user = session.get(User, uid)
    size = user.label_size if user.label_size in LABEL_SIZES else DEFAULT_SIZE
    layout = _custom_layout(user) if size == "custom" else None
    if size == "custom" and layout is None:
        size = DEFAULT_SIZE                      # chosen but never filled in: print on the standard sheet rather than fail
    context = {
        "labels": labels, "title": title, "size": size, "page_rule": page_rule(size), "auto": request.query_params.get("auto") == "1",
        "next_page": _next_page(request.query_params.get("next")), "custom_css": "", "pages": None, "shape": "rect", "order_id": order_id,
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
    return _sheet(request, session, uid, labels, "Vial labels", order_id)


@router.get("/active-vials/{vial_id}/label")
def vial_label(vial_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    vial = session.get(ActiveVial, vial_id)
    if vial is None or vial.owner_id != uid:
        raise HTTPException(404, "Vial not found")
    return _sheet(request, session, uid, [label_for_vial(vial, symbols.symbol_for(session, vial.inventory_item.name))], "Vial label")


# ---- Avery 94500 round labels, as a Word file -------------------------------------------------------------------------------------------------

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_LABELS = 480                     # ten sheets


def _word(session: Session, uid: int, specs: list[avery94500.Spec], start: int, name: str, draw=None) -> Response:
    data, _, _ = avery94500.word_file(specs, start, avery94500.Align.from_dict(session.get(User, uid).avery_align), draw)
    name = re.sub(r"[^A-Za-z0-9_.-]", "", name) or "Amide_Labels.docx"         # a symbol can hold any letter; a header holds ASCII and no quote
    return Response(data, media_type=DOCX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _start(raw: str | None) -> int:
    try:
        n = int(raw or 1)
    except ValueError:
        raise HTTPException(422, "The starting label must be a whole number from 1 to 48")
    if not 1 <= n <= avery94500.PER_SHEET:
        raise HTTPException(422, "The starting label must be a whole number from 1 to 48")
    return n


def _exp(raw: str | None) -> str:
    text = (raw or "").strip().replace("-", "/").replace(".", "/")
    if not text:
        return ""
    m = re.fullmatch(r"(\d{1,2})/(\d{2}|\d{4})", text)
    if not m or not 1 <= int(m.group(1)) <= 12:
        raise HTTPException(422, "Use MM/YY for the expiration, for example 12/27")
    return f"{int(m.group(1)):02d}/{m.group(2)[-2:]}"


@router.get("/inventory/{item_id}/labels.docx")
def item_word_labels(item_id: int, count: int = 1, start: str = "1", lot: str = "", exp: str = "", session: Session = Depends(get_session),
                     uid: int = Depends(current_user_id)):
    """Avery 94500 labels for one of your own peptides: `count` of them from circle `start`, with the lot and expiration typed on the form."""
    item = session.get(InventoryItem, item_id)
    if item is None or item.owner_id != uid or item.category != Category.MEDICINE:
        raise HTTPException(404, "Peptide not found")
    if not 1 <= count <= MAX_LABELS:
        raise HTTPException(422, f"Print 1 to {MAX_LABELS} labels at a time")
    spec = avery94500.Spec(symbols.symbol_for(session, item.name), avery94500.strength_text(item.vial_size_mg, item.vial_size_unit.value if item.vial_size_unit else "mg"),
                           lot.strip().upper()[:20], _exp(exp), count)
    first = _start(start)
    session.commit()
    return _word(session, uid, [spec], first, f"Amide_Labels_{spec.symbol}.docx")


@router.get("/inventory/labels/alignment-test.docx")
def alignment_test_sheet(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """All 48 circles as plain rings with a cross, printed with your saved alignment, to hold against a label sheet and see where they land."""
    return _word(session, uid, [avery94500.Spec("", "", "", "", avery94500.PER_SHEET)], 1, "Amide_Alignment_Test.docx", avery94500.test_png)


@router.get("/inventory/orders/{order_id}/labels.docx")
def order_word_labels(order_id: int, start: str = "1", session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Avery 94500 labels for every peptide vial received on a checked-in order, each with its own line's lot and expiration, from circle `start`."""
    order = session.get(Order, order_id)
    specs = avery94500_specs_for_order(session, order, uid) if order is not None else []
    if not specs:
        raise HTTPException(404, "No labels to print for this order")
    if sum(s.count for s in specs) > MAX_LABELS:
        raise HTTPException(422, f"That is more than {MAX_LABELS} labels")
    first = _start(start)
    session.commit()
    return _word(session, uid, specs, first, "Amide_Labels.docx")


def avery94500_specs_for_order(session: Session, order: Order, uid: int) -> list[avery94500.Spec]:
    if order.arrival_date is None:
        return []
    specs = []
    for line in order.items:
        item = line.inventory_item
        if item.owner_id != uid or item.category != Category.MEDICINE or not (line.received_quantity or 0):
            continue
        specs.append(avery94500.Spec(symbols.symbol_for(session, item.name), avery94500.strength_text(item.vial_size_mg, item.vial_size_unit.value if item.vial_size_unit else "mg"),
                                     (line.lot_number or "").strip().upper()[:20], avery94500.exp_text(line.expiration_date or item.expiration_date), line.received_quantity))
    return specs
