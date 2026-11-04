"""Protocol Portfolio: one PDF with the protocol, each of its peptides' Library pages (without the dosing chart) and a cost breakdown."""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.auth.deps import current_user_id
from app.db import get_session
from app.goals import GOALS
from app.library.interactions import cautions_for
from app.models import GoalPeptide, Peptide, Protocol, ProtocolItem, User, UserMedicine
from app.portfolio.library_pages import library_page
from app.portfolio.pdf import build_portfolio
from app.portfolio.prices import portfolio_prices
from app.protocols.status import protocol_status
from app.routers.protocols import print_items
from app.shopping.build import saved_shipping

router = APIRouter()


@router.get("/protocols/{protocol_id}/portfolio.pdf")
def protocol_portfolio(protocol_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    protocol = session.scalar(select(Protocol).where(Protocol.id == protocol_id, Protocol.owner_id == uid).options(
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide), selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs)))
    if protocol is None:
        raise HTTPException(404, "Protocol not found")
    today = clock.today()
    medicines = list(session.scalars(select(UserMedicine.name).where(UserMedicine.owner_id == uid)))
    goal_slugs: dict[int, list[str]] = {}
    for gp in session.scalars(select(GoalPeptide)):
        goal_slugs.setdefault(gp.peptide_id, []).append(gp.goal)
    seen: set[int] = set()
    pages = []
    for item in protocol.items:                                     # each peptide once, in the protocol's own order
        if item.peptide_id in seen:
            continue
        seen.add(item.peptide_id)
        peptide: Peptide = item.peptide
        cautions = [(c["medicine"], c["note"]) for c in cautions_for(peptide.name, peptide.aliases, medicines)] if medicines else []
        pages.append(library_page(peptide, goal_slugs.get(peptide.id, []), cautions))
    status = protocol_status(protocol, today)
    summary = {"name": protocol.name, "status": status.label, "start": protocol.start_date, "end": protocol.end_date, "notes": protocol.notes,
               "goals": [g.label for g in GOALS if g.slug in protocol.goal_slugs], "items": print_items(protocol)}
    pdf = build_portfolio(summary, pages, portfolio_prices(session, protocol, uid, saved_shipping(session.get(User, uid))), today)
    slug = re.sub(r"[^a-z0-9]+", "-", protocol.name.lower()).strip("-") or "protocol"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="protocol-portfolio-{slug}.pdf"',
                                                                "Cache-Control": "no-store"})
