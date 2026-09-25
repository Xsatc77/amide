from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import uploads
from app.auth.deps import current_user_id
from app.auth.sessions import now_utc
from app.db import get_session
from app.inventory.rules import FIELD_LABEL_OVERRIDES, field_label, required_fields_for
from app.inventory.vendors import resolve_vendor
from app.models import ActiveVial, DoseUnit, InventoryItem, Medium, Share, ShareCategory, StorageLocation, User
from app.templating import templates

router = APIRouter()

# Text fields on the add/edit form, in form order.
FORM_FIELDS = (
    "name", "count", "vial_size_mg", "vial_size_unit", "medium", "volume_ml", "units_per_package",
    "expiration_date", "storage", "cost", "vendor",
    "lot_number", "order_date", "shipped_date", "arrival_date",
    "coa_vial_size_mg", "coa_purity_pct", "notes",
)


# ---------------------------------------------------------------- form parsing

def _parse_positive_float(raw: str, field: str, label: str, errors: dict) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        errors[field] = f"{label} must be a number."
        return None
    if value <= 0:
        errors[field] = f"{label} must be greater than 0."
    return value


def _parse_date(raw: str, field: str, errors: dict) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[field] = "Enter a valid date."
        return None


def _parse_choice(enum_cls, raw: str, default, field: str, errors: dict):
    if not raw:
        return default
    try:
        return enum_cls(raw)
    except ValueError:
        errors[field] = "Pick an option from the list."
        return default


def _parse_form(raw: dict[str, str], session: Session, uid: int) -> tuple[dict, dict[str, str]]:
    """Returns (column values, field name -> error message)."""
    errors: dict[str, str] = {}
    values: dict = {}

    values["name"] = raw["name"]
    if not values["name"]:
        errors["name"] = "Item name is required."

    try:
        values["count"] = int(raw["count"]) if raw["count"] else 1
        if values["count"] < 0:
            errors["count"] = "Count can't be negative."
    except ValueError:
        errors["count"] = "Count must be a whole number."

    values["vial_size_mg"] = _parse_positive_float(raw["vial_size_mg"], "vial_size_mg", "Amount", errors)
    values["vial_size_unit"] = _parse_choice(DoseUnit, raw["vial_size_unit"], DoseUnit.MG, "vial_size_unit", errors)
    values["volume_ml"] = _parse_positive_float(raw["volume_ml"], "volume_ml", "Volume", errors)
    values["units_per_package"] = None
    if raw["units_per_package"]:
        try:
            values["units_per_package"] = int(raw["units_per_package"])
            if values["units_per_package"] <= 0:
                errors["units_per_package"] = "Units per package must be greater than 0."
        except ValueError:
            errors["units_per_package"] = "Units per package must be a whole number."

    values["medium"] = None
    if raw["medium"]:
        try:
            values["medium"] = Medium(raw["medium"])
        except ValueError:
            errors["medium"] = "Pick a medium from the list."

    for field in required_fields_for(values["medium"]):
        if values.get(field) is None and field not in errors:
            errors[field] = f"{field_label(field, values['medium'])} is required for {values['medium'].value}."

    values["expiration_date"] = _parse_date(raw["expiration_date"], "expiration_date", errors)
    values["storage"] = _parse_choice(StorageLocation, raw["storage"], None, "storage", errors)

    cost = raw["cost"].lstrip("$").replace(",", "")
    values["cost_cents"] = None
    if cost:
        try:
            cents = (Decimal(cost) * 100).quantize(Decimal("1"))
            if cents < 0:
                errors["cost"] = "Cost can't be negative."
            values["cost_cents"] = int(cents)
        except InvalidOperation:
            errors["cost"] = "Cost must be a number, e.g. 45.99."

    vendor = resolve_vendor(session, uid, raw["vendor"])
    values["vendor_id"] = vendor.id if vendor else None
    values["vendor"] = vendor.name if vendor else None
    values["lot_number"] = raw["lot_number"] or None

    for field in ("order_date", "shipped_date", "arrival_date"):
        values[field] = _parse_date(raw[field], field, errors)
    ordered, shipped, arrived = values["order_date"], values["shipped_date"], values["arrival_date"]
    if ordered and shipped and shipped < ordered:
        errors["shipped_date"] = "Shipped date can't be before the order date."
    if arrived and (shipped or ordered) and arrived < (shipped or ordered):
        errors["arrival_date"] = f"Arrival date can't be before the {'shipped' if shipped else 'order'} date."

    values["coa_vial_size_mg"] = _parse_positive_float(
        raw["coa_vial_size_mg"], "coa_vial_size_mg", "Lab vial size", errors)

    values["coa_purity_pct"] = None
    purity = raw["coa_purity_pct"].rstrip("%").strip()
    if purity:
        try:
            values["coa_purity_pct"] = float(purity)
            if not 0 <= values["coa_purity_pct"] <= 100:
                errors["coa_purity_pct"] = "Purity must be between 0 and 100%."
        except ValueError:
            errors["coa_purity_pct"] = "Purity must be a number, e.g. 99.2."

    values["notes"] = raw["notes"] or None
    return values, errors


def _form_values(item: InventoryItem) -> dict:
    """An item's values as the edit form expects them (all strings)."""
    def num(v):
        return "" if v is None else f"{v:g}"

    return {
        "id": item.id,
        "name": item.name,
        "count": str(item.count),
        "vial_size_mg": num(item.vial_size_mg),
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else "",
        "volume_ml": num(item.volume_ml),
        "units_per_package": "" if item.units_per_package is None else str(item.units_per_package),
        "expiration_date": item.expiration_date.isoformat() if item.expiration_date else "",
        "storage": item.storage.value if item.storage else "",
        "cost": "" if item.cost is None else f"{item.cost:.2f}",
        "vendor": item.vendor or "",
        "lot_number": item.lot_number or "",
        "order_date": item.order_date.isoformat() if item.order_date else "",
        "shipped_date": item.shipped_date.isoformat() if item.shipped_date else "",
        "arrival_date": item.arrival_date.isoformat() if item.arrival_date else "",
        "coa_vial_size_mg": num(item.coa_vial_size_mg),
        "coa_purity_pct": num(item.coa_purity_pct),
        "notes": item.notes or "",
        "has_coa": item.coa_filename is not None,
    }


async def _read_form(request: Request) -> tuple[dict[str, str], UploadFile | None, bool]:
    """Returns (stripped text fields, uploaded COA or None, remove_coa checked)."""
    form = await request.form()
    raw = {f: str(form.get(f) or "").strip() for f in FORM_FIELDS}
    coa = form.get("coa")
    has_file = isinstance(coa, UploadFile) and bool(coa.filename)
    return raw, (coa if has_file else None), bool(form.get("remove_coa"))


def _own_item(session: Session, item_id: int, uid: int) -> InventoryItem | None:
    """The item if it belongs to this user; someone else's item is treated as not existing."""
    item = session.get(InventoryItem, item_id)
    return item if item is not None and item.owner_id == uid else None


def _own_items(session: Session, uid: int):
    return session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid)
                           .order_by(InventoryItem.name.collate("NOCASE"))).all()


def _visible_items(session: Session, uid: int):
    """This user's own items, plus items owned by anyone who granted them Inventory sharing."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY)
    items = session.scalars(
        select(InventoryItem)
        .where((InventoryItem.owner_id == uid) | (InventoryItem.owner_id.in_(shared_owner_ids)))
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()
    other_owner_ids = {i.owner_id for i in items if i.owner_id != uid}
    owner_names = {}
    if other_owner_ids:
        owner_names = dict(session.execute(
            select(User.id, User.username).where(User.id.in_(other_owner_ids))).all())
    return items, owner_names


def _visible_item(session: Session, item_id: int, uid: int) -> InventoryItem | None:
    """The item if it's this user's own, or if its owner granted them Inventory sharing.
    Read access only -- never use this to authorize a mutating route."""
    item = session.get(InventoryItem, item_id)
    if item is None:
        return None
    if item.owner_id == uid:
        return item
    shared = session.scalar(select(Share).where(
        Share.owner_id == item.owner_id, Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY))
    return item if shared else None


def _open_active_vials(session: Session, item_ids: list[int]) -> dict[int, ActiveVial]:
    """The earliest-discard-by open (non-discarded) ActiveVial per inventory_item_id, for the
    duplicate-vial warning check. Only one per item is shown even if more than one exists."""
    if not item_ids:
        return {}
    vials = session.scalars(
        select(ActiveVial).where(ActiveVial.inventory_item_id.in_(item_ids), ActiveVial.discarded_at.is_(None))
        .order_by(ActiveVial.discard_by)
    ).all()
    result: dict[int, ActiveVial] = {}
    for v in vials:
        result.setdefault(v.inventory_item_id, v)  # first (earliest discard_by) wins per item
    return result


def _visible_active_vials(session: Session, uid: int):
    """This user's own open Active Vials, plus open vials on items owned by anyone who granted
    them Inventory sharing -- the same grant, no new category."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY)
    vials = session.scalars(
        select(ActiveVial)
        .where(ActiveVial.discarded_at.is_(None),
              (ActiveVial.owner_id == uid) | (ActiveVial.owner_id.in_(shared_owner_ids)))
        .order_by(ActiveVial.discard_by)
    ).all()
    item_ids = {v.inventory_item_id for v in vials}
    items_by_id = {i.id: i for i in session.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(item_ids)))} if item_ids else {}
    owner_ids = {v.owner_id for v in vials if v.owner_id != uid}
    owner_names = dict(session.execute(
        select(User.id, User.username).where(User.id.in_(owner_ids))).all()) if owner_ids else {}
    return vials, items_by_id, owner_names


def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200):
    uid = request.state.user.id
    items, owner_names = _visible_items(session, uid)
    own_lyo_ids = [i.id for i in items if i.owner_id == uid and i.medium == Medium.LYOPHILIZED]
    open_vials = _open_active_vials(session, own_lyo_ids)
    vials, vial_items, vial_owner_names = _visible_active_vials(session, uid)
    now = now_utc()
    expired_prompts = {
        v.id for v in vials
        if v.owner_id == uid and v.discard_by < date.today()
        and (v.last_discard_prompt_at is None or now - v.last_discard_prompt_at > timedelta(hours=24))
    }
    return templates.TemplateResponse(
        request,
        "inventory/list.html",
        {
            "items": items,
            "viewer_id": uid,
            "owner_names": owner_names,
            "open_vials": open_vials,
            "active_vials": vials,
            "vial_items": vial_items,
            "vial_owner_names": vial_owner_names,
            "expired_prompts": expired_prompts,
            "today": date.today(),
            "edit_data": {i.id: _form_values(i) for i in items if i.owner_id == uid},
            "mediums": list(Medium),
            "dose_units": list(DoseUnit),
            "storage_locations": list(StorageLocation),
            "medium_rules": {
                m.value: {
                    "required": sorted(required_fields_for(m)),
                    "labels": {f: field_label(f, m) for f in ("vial_size_mg", "units_per_package")},
                }
                for m in Medium
            },
            "form": form,
            "errors": errors or {},
            "editing": editing,
        },
        status_code=status_code,
    )


# ---------------------------------------------------------------- HTML routes

@router.get("/inventory")
def list_inventory(request: Request, session: Session = Depends(get_session)):
    return _render_list(request, session)


@router.post("/inventory")
async def create_item(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    raw, coa, _ = await _read_form(request)
    values, errors = _parse_form(raw, session, uid)

    coa_filename = None
    if not errors and coa:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return _render_list(request, session, form=raw, errors=errors, status_code=422)

    session.add(InventoryItem(**values, coa_filename=coa_filename, owner_id=uid))
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}")
async def update_item(item_id: int, request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_form(raw, session, uid)

    new_coa = None
    if not errors and coa:
        try:
            new_coa = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return _render_list(request, session, form=raw, errors=errors, editing=item, status_code=422)

    for key, value in values.items():
        setattr(item, key, value)
    if new_coa or remove_coa:
        uploads.delete_coa(item.coa_filename)
        item.coa_filename = new_coa
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}/delete")
def delete_item(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    uploads.delete_coa(item.coa_filename)
    session.delete(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


def _own_active_vial(session: Session, vial_id: int, uid: int) -> ActiveVial | None:
    vial = session.get(ActiveVial, vial_id)
    return vial if vial is not None and vial.owner_id == uid else None


@router.post("/active-vials/{vial_id}/discard")
def discard_active_vial(vial_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    vial = _own_active_vial(session, vial_id, uid)
    if vial is None:
        raise HTTPException(404)
    vial.discarded_at = now_utc()
    session.commit()
    return RedirectResponse(f"/inventory?just_discarded={vial.inventory_item_id}#active-vials", status_code=303)


@router.post("/active-vials/{vial_id}/snooze-prompt")
def snooze_active_vial_prompt(vial_id: int, session: Session = Depends(get_session),
                              uid: int = Depends(current_user_id)):
    vial = _own_active_vial(session, vial_id, uid)
    if vial is None:
        raise HTTPException(404)
    vial.last_discard_prompt_at = now_utc()
    session.commit()
    return {"ok": True}


@router.get("/inventory/{item_id}/coa")
def get_coa(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    if item is None or not item.coa_filename:
        raise HTTPException(404, "No COA on file")
    path = uploads.coa_path(item.coa_filename)
    if not path.exists():
        raise HTTPException(404, "COA file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(item.coa_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")


# ---------------------------------------------------------------- JSON API
# Read-only for now; the reconstitution calculator and dose logging will use it.

def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _to_json(item: InventoryItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "count": item.count,
        "vial_size_mg": item.vial_size_mg,
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else None,
        "volume_ml": item.volume_ml,
        "units_per_package": item.units_per_package,
        "expiration_date": _iso(item.expiration_date),
        "storage": item.storage.value if item.storage else None,
        "cost": item.cost,
        "vendor": item.vendor,
        "vendor_id": item.vendor_id,
        "lot_number": item.lot_number,
        "order_date": _iso(item.order_date),
        "shipped_date": _iso(item.shipped_date),
        "arrival_date": _iso(item.arrival_date),
        "has_coa": bool(item.coa_filename),
        "coa_vial_size_mg": item.coa_vial_size_mg,
        "coa_purity_pct": item.coa_purity_pct,
        "notes": item.notes,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("/api/inventory")
def api_list_inventory(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    items = _own_items(session, uid)
    return [_to_json(i) for i in items]


@router.get("/api/inventory/{item_id}")
def api_get_inventory(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    return _to_json(item)
