"""Backup and restore: export the signed-in user's own data as JSON or CSV, and import a previously
exported JSON file. Import is additive only: it always creates new rows, and never updates, matches, or
deletes anything that already exists. The uploaded COA image/PDF files are not included.
"""

import csv
import io
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.db import get_session
from app.goals import GOALS_BY_SLUG
from app.models import (
    Category, DoseUnit, Frequency, InventoryItem, Medium, Order, Protocol, ProtocolGoal, ProtocolItem, Route,
    StorageLocation, TimeOfDay, TitrationStep,
)
from app.routers.protocols import _find_or_create_peptide
from app.templating import templates

router = APIRouter()


# ---------------------------------------------------------------- export

def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _inventory_row(i: InventoryItem) -> dict:
    return {
        "name": i.name, "category": i.category.value, "count": i.count, "vial_size_mg": i.vial_size_mg,
        "vial_size_unit": i.vial_size_unit.value, "medium": i.medium.value if i.medium else None,
        "volume_ml": i.volume_ml, "units_per_package": i.units_per_package,
        "storage": i.storage.value if i.storage else None,
        "cost": i.cost, "vendor": i.vendor, "notes": i.notes,
        "orders": [_order_row(o) for o in i.orders],
    }


def _order_row(o: Order) -> dict:
    return {
        "quantity": o.quantity, "order_date": _iso(o.order_date), "shipped_date": _iso(o.shipped_date),
        "arrival_date": _iso(o.arrival_date), "tracking_site": o.tracking_site,
        "tracking_number": o.tracking_number, "vendor": o.vendor, "lot_number": o.lot_number,
        "cost": o.cost, "tax": o.tax, "shipping": o.shipping, "expiration_date": _iso(o.expiration_date),
        "coa_vial_size_mg": o.coa_vial_size_mg, "coa_purity_pct": o.coa_purity_pct,
    }


def _protocol_row(p: Protocol) -> dict:
    return {
        "name": p.name, "start_date": _iso(p.start_date), "end_date": _iso(p.end_date), "notes": p.notes,
        "titration_enabled": p.titration_enabled, "goals": p.goal_slugs,
        "items": [
            {
                "peptide": it.peptide.name, "dose": it.dose, "dose_unit": it.dose_unit.value,
                "frequency": it.frequency.value, "every_n_days": it.every_n_days, "weekdays": it.weekdays,
                "time_of_day": it.time_of_day.value, "route": it.route.value, "notes": it.notes,
                "steps": [{"start_week": s.start_week, "end_week": s.end_week, "dose": s.dose} for s in it.steps],
            }
            for it in p.items
        ],
    }


@router.get("/backup")
def backup_page(request: Request):
    return templates.TemplateResponse(request, "backup/backup.html", {})


@router.get("/backup/export.json")
def export_json(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.orders)).order_by(InventoryItem.name)
    ).all()
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid)
        .options(selectinload(Protocol.goals), selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
                selectinload(Protocol.items).selectinload(ProtocolItem.steps))
        .order_by(Protocol.name)
    ).all()
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "inventory": [_inventory_row(i) for i in inventory],
        "protocols": [_protocol_row(p) for p in protocols],
    }
    body = json.dumps(payload, indent=2)
    filename = f"amide-backup-{date.today().isoformat()}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


CSV_COLUMNS = [
    ("Name", "name"), ("Category", "category"), ("Count", "count"), ("Amount", "vial_size_mg"),
    ("Unit", "vial_size_unit"), ("Medium", "medium"), ("Volume (mL)", "volume_ml"),
    ("Units per package", "units_per_package"), ("Storage", "storage"), ("Cost", "cost"),
    ("Vendor", "vendor"), ("Notes", "notes"),
]
ORDER_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Order date", "order_date"),
    ("Shipped date", "shipped_date"), ("Arrival date", "arrival_date"), ("Tracking site", "tracking_site"),
    ("Tracking number", "tracking_number"), ("Vendor", "vendor"), ("Lot/Batch #", "lot_number"),
    ("Cost", "cost"), ("Tax", "tax"), ("Shipping", "shipping"), ("Expiration", "expiration_date"),
]


@router.get("/backup/export/inventory.csv")
def export_inventory_csv(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.orders)).order_by(InventoryItem.name)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for i in inventory:
        row = _inventory_row(i)
        writer.writerow(["" if row[key] is None else row[key] for _, key in CSV_COLUMNS])
    writer.writerow([])
    writer.writerow([header for header, _ in ORDER_CSV_COLUMNS])
    for i in inventory:
        for o in i.orders:
            row = {**_order_row(o), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in ORDER_CSV_COLUMNS])
    filename = f"amide-inventory-{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- import (additive only)

def _import_inventory_row(session: Session, uid: int, row: dict) -> None:
    medium = Medium(row["medium"]) if row.get("medium") else None
    item = InventoryItem(
        owner_id=uid, name=row["name"], category=Category(row.get("category") or "Medicine"),
        count=row.get("count", 1), vial_size_mg=row.get("vial_size_mg"),
        vial_size_unit=DoseUnit(row.get("vial_size_unit") or "mg"), medium=medium,
        volume_ml=row.get("volume_ml"), units_per_package=row.get("units_per_package"),
        storage=StorageLocation(row["storage"]) if row.get("storage") else None,
        cost_cents=round(row["cost"] * 100) if row.get("cost") is not None else None,
        vendor=row.get("vendor"), notes=row.get("notes"),
    )
    for o in row.get("orders", []):  # absent entirely in a pre-Order-history backup file -- treat as none
        item.orders.append(Order(
            quantity=o["quantity"], order_date=date.fromisoformat(o["order_date"]),
            shipped_date=date.fromisoformat(o["shipped_date"]) if o.get("shipped_date") else None,
            arrival_date=date.fromisoformat(o["arrival_date"]) if o.get("arrival_date") else None,
            tracking_site=o.get("tracking_site"), tracking_number=o.get("tracking_number"),
            vendor=o.get("vendor"), lot_number=o.get("lot_number"),
            cost_cents=round(o["cost"] * 100) if o.get("cost") is not None else None,
            tax_cents=round(o["tax"] * 100) if o.get("tax") is not None else None,
            shipping_cents=round(o["shipping"] * 100) if o.get("shipping") is not None else None,
            expiration_date=date.fromisoformat(o["expiration_date"]) if o.get("expiration_date") else None,
            coa_vial_size_mg=o.get("coa_vial_size_mg"), coa_purity_pct=o.get("coa_purity_pct"),
        ))
    session.add(item)


def _import_protocol_row(session: Session, uid: int, row: dict) -> None:
    p = Protocol(
        owner_id=uid, name=row["name"], start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]) if row.get("end_date") else None,
        notes=row.get("notes"), titration_enabled=bool(row.get("titration_enabled")),
    )
    p.goals = [ProtocolGoal(goal=g) for g in row.get("goals", []) if g in GOALS_BY_SLUG]
    for position, item in enumerate(row.get("items", [])):
        peptide = _find_or_create_peptide(session, item["peptide"])
        p.items.append(ProtocolItem(
            peptide_id=peptide.id, position=position, dose=item.get("dose"),
            dose_unit=DoseUnit(item.get("dose_unit") or "mg"), frequency=Frequency(item.get("frequency") or "daily"),
            every_n_days=item.get("every_n_days"), weekdays=item.get("weekdays"),
            time_of_day=TimeOfDay(item.get("time_of_day") or "any"), route=Route(item.get("route") or "subq"),
            notes=item.get("notes"),
            steps=[TitrationStep(start_week=s["start_week"], end_week=s.get("end_week"), dose=s["dose"])
                  for s in item.get("steps", [])],
        ))
    session.add(p)


@router.post("/backup/import")
async def import_backup(request: Request, file: UploadFile, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    try:
        payload = json.loads(await file.read())
        assert isinstance(payload, dict)
    except (json.JSONDecodeError, UnicodeDecodeError, AssertionError):
        return templates.TemplateResponse(
            request, "backup/backup.html",
            {"error": "That file doesn't look like an Amide backup (not valid JSON)."}, status_code=422)

    for row in payload.get("inventory", []):
        _import_inventory_row(session, uid, row)
    for row in payload.get("protocols", []):
        _import_protocol_row(session, uid, row)
    session.commit()
    return RedirectResponse("/backup?imported=1", status_code=303)
