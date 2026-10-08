"""The Spending page: cost per vial, per mg and per dose for each peptide, and spend by month."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.inventory.spending import summarize
from app.models import Category, InventoryItem, Protocol, ProtocolItem
from app.protocols.status import Status, protocol_status
from app.routers.protocols import get_today
from app.templating import templates
from datetime import date

router = APIRouter()


@router.get("/inventory/spending")
def spending_page(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today), uid: int = Depends(current_user_id)):
    items = session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid, InventoryItem.category == Category.MEDICINE)
                            .order_by(InventoryItem.name)).all()
    linked = session.scalars(select(ProtocolItem).where(ProtocolItem.inventory_item_id.in_([i.id for i in items] or [0]))).all()
    protocols = {p.id: p for p in session.scalars(select(Protocol).where(Protocol.id.in_({pi.protocol_id for pi in linked} or {0})))}
    active = [pi for pi in linked if protocol_status(protocols[pi.protocol_id], today) is Status.ACTIVE]
    return templates.TemplateResponse(request, "inventory/spending.html", {"summary": summarize(items, active)})
