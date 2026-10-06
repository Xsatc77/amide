"""Backup and restore. The page offers encrypted backups, exports and share files of chosen sections, and loading or
restoring them (see app/backup/ and docs/superpowers/specs/2026-10-06-backup-restore-design.md). The older plain JSON
export/import of inventory and protocols and the inventory CSV remain under "Older formats": that import is additive
only and never updates or deletes anything.
"""

import csv
import io
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import config
from app.auth import sessions as login_sessions
from app.auth.deps import current_user_id
from app.backup import load as backup_load
from app.backup import pending
from app.backup import restore as backup_restore
from app.backup import sections as backup_sections
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.service import make_download, open_upload
from app.db import get_session
from app.goals import GOALS_BY_SLUG
from app.models import (
    Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Protocol, ProtocolGoal, ProtocolItem,
    ProtocolItemCycleOff, PurchasingUnit, Route, Sale, StorageLocation, TimeOfDay, TitrationStep, User,
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
        "vial_size_unit": i.vial_size_unit.value, "purchasing_unit": i.purchasing_unit.value,
        "medium": i.medium.value if i.medium else None,
        "volume_ml": i.volume_ml, "units_per_package": i.units_per_package,
        "storage": i.storage.value if i.storage else None,
        "cost": i.cost, "vendor": i.vendor, "notes": i.notes,
        "reconstituted_count": i.reconstituted_count, "sold_count": i.sold_count,
        "orders": [_order_row(li) for li in i.order_items],
        "sales": [_sale_row(s) for s in i.sales],
    }


def _order_row(li) -> dict:
    return {
        "quantity": li.quantity, "received_quantity": li.received_quantity,
        "order_date": _iso(li.order.order_date), "shipped_date": _iso(li.order.shipped_date),
        "arrival_date": _iso(li.order.arrival_date), "tracking_site": li.order.tracking_site,
        "tracking_number": li.order.tracking_number, "vendor": li.order.vendor,
        "lot_number": li.lot_number, "cost": li.cost,
        "tax": (li.allocated_tax_cents / 100) if li.allocated_tax_cents else None,
        "shipping": (li.allocated_shipping_cents / 100) if li.allocated_shipping_cents else None,
        "expiration_date": _iso(li.expiration_date),
        "coa_vial_size_mg": li.coa_vial_size_mg, "coa_purity_pct": li.coa_purity_pct,
    }


def _sale_row(s: Sale) -> dict:
    return {"quantity": s.quantity, "sale_date": _iso(s.sale_date), "price": s.price}


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
                "cycle_offs": [{"start_week": c.start_week, "end_week": c.end_week} for c in it.cycle_offs],
            }
            for it in p.items
        ],
    }


TABS = ("backup", "export", "restore")
RESTORE_WORD = "RESTORE"


def _context(session: Session, uid: int, tab: str, **extra) -> dict:
    me = session.get(User, uid)
    admin = bool(me.is_admin)
    sec = backup_sections.SECTIONS
    return {
        "tab": tab if tab in TABS else "backup", "is_admin": admin,
        "person_sections": [sec[k] for k in backup_sections.PERSON_SECTIONS],
        "shared_sections": [sec[k] for k in backup_sections.SHARED_SECTIONS] if admin else [],
        "shareable_sections": [sec[k] for k in backup_sections.SHAREABLE if admin or sec[k].level == backup_sections.PERSON],
        "max_mb": config.MAX_BACKUP_BYTES // (1024 * 1024), **extra,
    }


def _page(request: Request, session: Session, uid: int, tab: str, status_code: int = 200, **extra):
    return templates.TemplateResponse(request, "backup/backup.html", _context(session, uid, tab, **extra),
                                      status_code=status_code)


@router.get("/backup")
def backup_page(request: Request, tab: str = "backup", session: Session = Depends(get_session),
                uid: int = Depends(current_user_id)):
    return _page(request, session, uid, tab)


@router.post("/backup/create")
async def create_backup(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Build an encrypted backup, export or share file and send it as a download."""
    form = await request.form()
    kind = str(form.get("kind") or "backup")
    me = session.get(User, uid)
    try:
        name, blob = make_download(
            session, me, kind=kind, keys=[str(k) for k in form.getlist("section")],
            installation=str(form.get("scope") or "person") == "installation",
            passphrase=str(form.get("passphrase") or ""), confirm=str(form.get("confirm") or ""))
    except BackupError as exc:
        return _page(request, session, uid, "backup" if kind == "backup" else "export", 422, error=str(exc), error_kind=kind)
    return Response(blob, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _preview(request: Request, session: Session, uid: int, token: str, archive, status_code: int = 200, **extra):
    me = session.get(User, uid)
    manifest = archive.manifest
    return templates.TemplateResponse(request, "backup/preview.html", {
        "token": token, "manifest": manifest, "is_admin": bool(me.is_admin),
        "sections": backup_load.describe(archive, username_key=me.username_key, is_admin=bool(me.is_admin),
                                         session=session, uid=uid),
        "can_restore": bool(me.is_admin) and manifest.get("level") == "installation" and manifest.get("kind") == "backup",
        "restore_word": RESTORE_WORD, **extra}, status_code=status_code)


@router.post("/backup/open")
async def open_backup(request: Request, file: UploadFile, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    """Decrypt and verify an uploaded backup and show what is in it. Nothing is changed yet."""
    form = await request.form()
    blob = await file.read(config.MAX_BACKUP_BYTES + 1)
    try:
        if len(blob) > config.MAX_BACKUP_BYTES:
            raise BackupError("This file is larger than the allowed size.")
        zipped, archive = open_upload(blob, str(form.get("passphrase") or ""))
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    token = pending.put(uid, zipped)
    return _preview(request, session, uid, token, archive)


def _opened(uid: int, token: str):
    return read_archive(pending.get(uid, token), max_bytes=config.MAX_BACKUP_BYTES)


@router.post("/backup/load")
async def load_backup(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Load the ticked sections of an opened backup, each as Add or Replace."""
    form = await request.form()
    token = str(form.get("token") or "")
    me = session.get(User, uid)
    try:
        archive = _opened(uid, token)
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    plan = {str(k): str(form.get(f"mode-{k}") or "") for k in form.getlist("load")}
    try:
        report = backup_load.load(session, archive, uid=uid, username_key=me.username_key, is_admin=bool(me.is_admin),
                                  plan=plan)
    except BackupError as exc:
        return _preview(request, session, uid, token, archive, 422, error=str(exc))
    pending.drop(token)
    return templates.TemplateResponse(request, "backup/result.html", {
        "report": report, "labels": {k: backup_sections.SECTIONS[k].label for k in backup_sections.SECTIONS}})


@router.post("/backup/restore")
async def restore_everything(request: Request, session: Session = Depends(get_session),
                             uid: int = Depends(current_user_id)):
    """Replace the whole installation with an opened whole-installation backup (administrators only)."""
    me = session.get(User, uid)
    if not me.is_admin:
        raise HTTPException(404)
    form = await request.form()
    token = str(form.get("token") or "")
    try:
        archive = _opened(uid, token)
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    try:
        if str(form.get("confirm") or "").strip() != RESTORE_WORD:
            raise BackupError(f"Type {RESTORE_WORD} to confirm.")
        if str(form.get("safety_passphrase") or "") != str(form.get("safety_confirm") or ""):
            raise BackupError("The two safety-copy passphrases do not match.")
        report = backup_restore.restore_installation(
            session, archive, uid=uid, creator=me.username, safety_passphrase=str(form.get("safety_passphrase") or ""))
    except BackupError as exc:
        return _preview(request, session, uid, token, archive, 422, error=str(exc))
    pending.drop(token)
    response = templates.TemplateResponse(request, "backup/restored.html", {"report": report})
    response.delete_cookie(login_sessions.COOKIE)
    return response


@router.get("/backup/export.json")
def export_json(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.order_items).selectinload(OrderItem.order),
                selectinload(InventoryItem.sales)).order_by(InventoryItem.name)
    ).all()
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid)
        .options(selectinload(Protocol.goals), selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
                selectinload(Protocol.items).selectinload(ProtocolItem.steps),
                selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs))
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
    ("Reconstituted", "reconstituted_count"), ("Sold", "sold_count"),
]
# Tax/Shipping here are each line's own allocated share of the order-level tax/shipping (see
# OrderItem.allocated_tax_cents/allocated_shipping_cents), not the order's full amount -- a
# multi-item order's total tax/shipping is split across its lines so re-importing doesn't
# double-count it once per line.
ORDER_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Received", "received_quantity"), ("Order date", "order_date"),
    ("Shipped date", "shipped_date"), ("Arrival date", "arrival_date"), ("Tracking site", "tracking_site"),
    ("Tracking number", "tracking_number"), ("Vendor", "vendor"), ("Lot/Batch #", "lot_number"),
    ("Cost", "cost"), ("Tax", "tax"), ("Shipping", "shipping"), ("Expiration", "expiration_date"),
]
SALE_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Sale date", "sale_date"), ("Price", "price"),
]


@router.get("/backup/export/inventory.csv")
def export_inventory_csv(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.order_items).selectinload(OrderItem.order),
                selectinload(InventoryItem.sales)).order_by(InventoryItem.name)
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
        for li in i.order_items:
            row = {**_order_row(li), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in ORDER_CSV_COLUMNS])
    writer.writerow([])
    writer.writerow([header for header, _ in SALE_CSV_COLUMNS])
    for i in inventory:
        for sale in i.sales:
            row = {**_sale_row(sale), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in SALE_CSV_COLUMNS])
    filename = f"amide-inventory-{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- import (additive only)

def _import_inventory_row(session: Session, uid: int, row: dict) -> None:
    medium = Medium(row["medium"]) if row.get("medium") else None
    item = InventoryItem(
        owner_id=uid, name=row["name"], category=Category(row.get("category") or "Medicine"),
        count=row.get("count", 1), vial_size_mg=row.get("vial_size_mg"),
        vial_size_unit=DoseUnit(row.get("vial_size_unit") or "mg"),
        purchasing_unit=PurchasingUnit(row.get("purchasing_unit") or "individual"), medium=medium,
        volume_ml=row.get("volume_ml"), units_per_package=row.get("units_per_package"),
        storage=StorageLocation(row["storage"]) if row.get("storage") else None,
        cost_cents=round(row["cost"] * 100) if row.get("cost") is not None else None,
        vendor=row.get("vendor"), notes=row.get("notes"),
        reconstituted_count=row.get("reconstituted_count") or 0, sold_count=row.get("sold_count") or 0,
    )
    for o in row.get("orders", []):  # absent entirely in a pre-multi-item-orders backup file -- treat as none
        order = Order(
            order_date=date.fromisoformat(o["order_date"]),
            shipped_date=date.fromisoformat(o["shipped_date"]) if o.get("shipped_date") else None,
            arrival_date=date.fromisoformat(o["arrival_date"]) if o.get("arrival_date") else None,
            tracking_site=o.get("tracking_site"), tracking_number=o.get("tracking_number"),
            vendor=o.get("vendor"),
            tax_cents=round(o["tax"] * 100) if o.get("tax") is not None else None,
            shipping_cents=round(o["shipping"] * 100) if o.get("shipping") is not None else None,
        )
        # A file with no received_quantity key predates multi-item orders -- if it had arrived, it
        # already counted as fully available under the old model, so backfill received_quantity to
        # quantity (matching migration 0013's own backfill rule) rather than leaving it NULL.
        received_quantity = o.get("received_quantity")
        if received_quantity is None and o.get("arrival_date"):
            received_quantity = o["quantity"]
        li = OrderItem(
            quantity=o["quantity"], received_quantity=received_quantity,
            lot_number=o.get("lot_number"),
            cost_cents=round(o["cost"] * 100) if o.get("cost") is not None else None,
            expiration_date=date.fromisoformat(o["expiration_date"]) if o.get("expiration_date") else None,
            coa_vial_size_mg=o.get("coa_vial_size_mg"), coa_purity_pct=o.get("coa_purity_pct"),
        )
        item.order_items.append(li)
        order.items.append(li)
        session.add(order)
    for sale in row.get("sales", []):  # absent entirely in a pre-Sold-flow backup file -- treat as none
        item.sales.append(Sale(
            quantity=sale["quantity"], sale_date=date.fromisoformat(sale["sale_date"]),
            price_cents=round(sale["price"] * 100),
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
            cycle_offs=[ProtocolItemCycleOff(start_week=c["start_week"], end_week=c["end_week"])
                       for c in item.get("cycle_offs", [])],
        ))
    session.add(p)


@router.post("/backup/import")
async def import_backup(request: Request, file: UploadFile, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    try:
        payload = json.loads(await file.read())
        assert isinstance(payload, dict)
    except (json.JSONDecodeError, UnicodeDecodeError, AssertionError):
        return _page(request, session, uid, "restore", 422,
                     legacy_error="That file doesn't look like an Amide backup (not valid JSON).")

    for row in payload.get("inventory", []):
        _import_inventory_row(session, uid, row)
    for row in payload.get("protocols", []):
        _import_protocol_row(session, uid, row)
    session.commit()
    return RedirectResponse("/backup?imported=1", status_code=303)
