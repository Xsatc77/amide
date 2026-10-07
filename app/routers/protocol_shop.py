"""Shop this protocol: the cheapest plan to buy a protocol's course from the vendors' current price lists."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import Protocol, ProtocolItem, User
from app.shopping.build import parse_fee, saved_shipping, shop_for_protocol

router = APIRouter()


@router.get("/protocols/{protocol_id}/shop")
def shop_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
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
    return shop_for_protocol(session, protocol, uid, shipping)


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
