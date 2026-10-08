"""Shop this protocol: the cheapest plan to buy a protocol's course from the vendors' current price lists."""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import Protocol, ProtocolItem, User
from app.shopping.build import parse_fee, saved_shipping, shop_for_protocol
from app.shopping.text import shop_text

router = APIRouter()


def _shop_data(protocol_id: int, request: Request, session: Session, uid: int) -> dict:
    protocol = session.scalar(select(Protocol).where(Protocol.id == protocol_id, Protocol.owner_id == uid).options(
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide), selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs)))
    if protocol is None:
        raise HTTPException(404, "Protocol not found")
    shipping = saved_shipping(session.get(User, uid))
    for key in ("china", "us"):                                       # a fee typed in the dialog applies to this look only
        raw = request.query_params.get(key)
        if raw not in (None, ""):
            fee = parse_fee(raw)
            if fee is None:
                raise HTTPException(422, f"{key} shipping must be a number from 0 to 10000")
            shipping[key] = fee
    sizes = {}
    for key, raw in request.query_params.items():                    # vial_ml_<peptide id> and box_vials_<peptide id>, for oils sold by strength
        if key.startswith("vial_ml_"):
            pid = key[len("vial_ml_"):]
            try:
                ml, per_box = float(raw), int(request.query_params.get(f"box_vials_{pid}", "1"))
                if not (pid.isdigit() and 0 < ml <= 1000 and 1 <= per_box <= 100):
                    raise ValueError
            except ValueError:
                raise HTTPException(422, "Vial size must be 0 to 1000 mL and 1 to 100 vials per box")
            sizes[int(pid)] = (ml, per_box)
    return shop_for_protocol(session, protocol, uid, shipping, sizes) | {"protocol": {"id": protocol.id, "name": protocol.name}}


@router.get("/protocols/{protocol_id}/shop")
def shop_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return _shop_data(protocol_id, request, session, uid)


@router.get("/protocols/{protocol_id}/shop.txt")
def shop_protocol_text(protocol_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """The same plan spelled out as plain text; ?download=1 sends it as a file."""
    data = _shop_data(protocol_id, request, session, uid)
    headers = {}
    if request.query_params.get("download"):
        slug = re.sub(r"[^a-z0-9]+", "-", data["protocol"]["name"].lower()).strip("-") or "protocol"
        headers["Content-Disposition"] = f'attachment; filename="shopping-plan-{slug}.txt"'
    return PlainTextResponse(shop_text(data, date.today()), headers=headers)


@router.post("/protocols/shop/defaults")
async def save_shipping_defaults(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    china, us = parse_fee(form.get("china", "")), parse_fee(form.get("us", ""))
    if china is None or us is None:
        raise HTTPException(422, "Enter both shipping fees as numbers from 0 to 10000")
    user = session.get(User, uid)
    user.shop_china_shipping_cents, user.shop_us_shipping_cents = round(china * 100), round(us * 100)
    session.commit()
    return {"ok": True, "china": china, "us": us}
