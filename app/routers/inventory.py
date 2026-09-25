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
from app.models import ActiveVial, Category, DoseUnit, InventoryItem, Medium, Order, Share, ShareCategory, StorageLocation, User
from app.templating import templates

router = APIRouter()

# Text fields on the add/edit form, in form order.
ITEM_FIELDS = ("name", "category", "count", "vial_size_mg", "vial_size_unit", "medium",
              "volume_ml", "units_per_package", "storage", "cost", "vendor", "notes")
ORDER_FIELDS = ("quantity", "order_date", "shipped_date", "arrival_date", "tracking_site",
                "tracking_number", "vendor", "cost", "tax", "shipping", "lot_number",
                "expiration_date", "coa_vial_size_mg", "coa_purity_pct")
FORM_FIELDS = tuple(dict.fromkeys(ITEM_FIELDS + ORDER_FIELDS))  # union, order preserved, no dupes


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


def _parse_money(raw: str, field: str, label: str, errors: dict) -> int | None:
    raw = raw.lstrip("$").replace(",", "")
    if not raw:
        return None
    try:
        cents = (Decimal(raw) * 100).quantize(Decimal("1"))
    except InvalidOperation:
        errors[field] = f"{label} must be a number, e.g. 45.99."
        return None
    if cents < 0:
        errors[field] = f"{label} can't be negative."
    return int(cents)


def _parse_item_fields(raw: dict[str, str], session: Session, uid: int, category: "Category") -> tuple[dict, dict]:
    """Parses the Details fields for an item of the given (already-resolved) category. Used by
    both create (with a freshly-parsed category) and update (with the item's existing, immutable
    category)."""
    errors: dict[str, str] = {}
    values: dict = {"category": category}

    values["name"] = raw["name"]
    if not values["name"]:
        errors["name"] = "Item name is required."
    values["storage"] = _parse_choice(StorageLocation, raw["storage"], None, "storage", errors)
    values["notes"] = raw["notes"] or None

    if category == Category.SUPPLY:
        try:
            values["count"] = int(raw["count"]) if raw["count"] else 1
            if values["count"] < 0:
                errors["count"] = "Count can't be negative."
        except ValueError:
            values["count"] = 1
            errors["count"] = "Count must be a whole number."
        values["cost_cents"] = _parse_money(raw["cost"], "cost", "Cost", errors)
        vendor = resolve_vendor(session, uid, raw["vendor"])
        values["vendor_id"] = vendor.id if vendor else None
        values["vendor"] = vendor.name if vendor else None
        values["medium"] = None
        values["vial_size_mg"] = None
        values["vial_size_unit"] = DoseUnit.MG
        values["volume_ml"] = None
        values["units_per_package"] = None
        return values, errors

    # Medicine / BAC Water: count/cost/vendor are vestigial here (available_count is derived from
    # Orders); the item-level columns are simply not written to by these categories.
    values["count"] = 0
    values["cost_cents"] = None
    values["vendor_id"] = None
    values["vendor"] = None

    if category == Category.MEDICINE:
        values["medium"] = _parse_choice(Medium, raw["medium"], None, "medium", errors)
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
        for field in required_fields_for(values["medium"]):
            if values.get(field) is None and field not in errors:
                errors[field] = f"{field_label(field, values['medium'])} is required for {values['medium'].value}."
    else:  # BAC_WATER
        values["medium"] = None
        values["vial_size_mg"] = None
        values["vial_size_unit"] = DoseUnit.MG
        values["volume_ml"] = None
        values["units_per_package"] = None

    return values, errors


def _parse_order_form(raw: dict[str, str], session: Session, uid: int) -> tuple[dict, dict]:
    """Parses the Order fields (quantity, dates, tracking, vendor/cost/tax/shipping/lot/
    expiration/COA numbers). Used by create (the item's first order) and by Task 5's Add/Edit
    order routes."""
    errors: dict[str, str] = {}
    values: dict = {}

    values["quantity"] = None
    try:
        if raw["quantity"]:
            values["quantity"] = int(raw["quantity"])
        if not values["quantity"] or values["quantity"] <= 0:
            errors["quantity"] = "Quantity must be a whole number greater than 0."
    except ValueError:
        errors["quantity"] = "Quantity must be a whole number."

    values["order_date"] = _parse_date(raw["order_date"], "order_date", errors)
    if values["order_date"] is None and "order_date" not in errors:
        errors["order_date"] = "Order date is required."
    values["shipped_date"] = _parse_date(raw["shipped_date"], "shipped_date", errors)
    values["arrival_date"] = _parse_date(raw["arrival_date"], "arrival_date", errors)
    values["tracking_site"] = raw["tracking_site"] or None
    values["tracking_number"] = raw["tracking_number"] or None

    vendor = resolve_vendor(session, uid, raw["vendor"])
    values["vendor_id"] = vendor.id if vendor else None
    values["vendor"] = vendor.name if vendor else None
    values["lot_number"] = raw["lot_number"] or None

    values["cost_cents"] = _parse_money(raw["cost"], "cost", "Cost", errors)
    values["tax_cents"] = _parse_money(raw["tax"], "tax", "Tax", errors)
    values["shipping_cents"] = _parse_money(raw["shipping"], "shipping", "Shipping", errors)
    values["expiration_date"] = _parse_date(raw["expiration_date"], "expiration_date", errors)

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

    return values, errors


def _form_values(item: InventoryItem) -> dict:
    """An item's Details values as the edit form expects them (all strings)."""
    def num(v):
        return "" if v is None else f"{v:g}"

    return {
        "id": item.id,
        "name": item.name,
        "category": item.category.value,
        "count": str(item.count),
        "vial_size_mg": num(item.vial_size_mg),
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else "",
        "volume_ml": num(item.volume_ml),
        "units_per_package": "" if item.units_per_package is None else str(item.units_per_package),
        "storage": item.storage.value if item.storage else "",
        "cost": "" if item.cost is None else f"{item.cost:.2f}",
        "vendor": item.vendor or "",
        "notes": item.notes or "",
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
            "today_iso": date.today().isoformat(),
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


@router.get("/inventory/{item_id}")
def item_detail(item_id: int, request: Request, session: Session = Depends(get_session),
                uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    arrived = sum(o.quantity for o in item.orders if o.arrival_date is not None)
    return templates.TemplateResponse(request, "inventory/detail.html", {
        "item": item,
        "is_owner": item.owner_id == uid,
        "arrived": arrived,
        "storage_locations": list(StorageLocation),
        "mediums": list(Medium),
        "dose_units": list(DoseUnit),
        "medium_rules": {
            m.value: {
                "required": sorted(required_fields_for(m)),
                "labels": {f: field_label(f, m) for f in ("vial_size_mg", "units_per_package")},
            }
            for m in Medium
        },
        "edit_data": _form_values(item),
    })


@router.post("/inventory")
async def create_item(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    raw, coa, _ = await _read_form(request)
    errors: dict[str, str] = {}
    category = _parse_choice(Category, raw["category"], Category.MEDICINE, "category", errors)
    values, item_errors = _parse_item_fields(raw, session, uid, category)
    errors.update(item_errors)

    order_values = {}
    if category != Category.SUPPLY:
        order_values, order_errors = _parse_order_form(raw, session, uid)
        errors.update(order_errors)

    coa_filename = None
    if not errors and coa and category != Category.SUPPLY:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return _render_list(request, session, form=raw, errors=errors, status_code=422)

    item = InventoryItem(**values, owner_id=uid)
    if category != Category.SUPPLY:
        item.orders.append(Order(**order_values, coa_filename=coa_filename))
    session.add(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}")
async def update_item(item_id: int, request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_item_fields(raw, session, uid, item.category)  # category is immutable

    if errors:
        return _render_list(request, session, form=raw, errors=errors, editing=item, status_code=422)

    for key, value in values.items():
        if key == "category":
            continue  # never reassigned after creation
        setattr(item, key, value)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}/delete")
def delete_item(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    for order in item.orders:
        uploads.delete_coa(order.coa_filename)
    session.delete(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


def _own_order(session: Session, item_id: int, order_id: int, uid: int) -> Order | None:
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    order = session.get(Order, order_id)
    return order if order is not None and order.inventory_item_id == item.id else None


@router.post("/inventory/{item_id}/orders")
async def add_order(item_id: int, request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None or item.category == Category.SUPPLY:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, _ = await _read_form(request)
    values, errors = _parse_order_form(raw, session, uid)

    coa_filename = None
    if not errors and coa:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "arrived": sum(o.quantity for o in item.orders if o.arrival_date is not None),
            "storage_locations": list(StorageLocation), "mediums": list(Medium),
            "dose_units": list(DoseUnit), "edit_data": _form_values(item)},
            status_code=422)

    item.orders.append(Order(**values, coa_filename=coa_filename))
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.post("/inventory/{item_id}/orders/{order_id}")
async def update_order(item_id: int, order_id: int, request: Request,
                       session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order(session, item_id, order_id, uid)
    if order is None:
        raise HTTPException(404, "Order not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_order_form(raw, session, uid)

    new_coa = None
    if not errors and coa:
        try:
            new_coa = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        item = order.inventory_item
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "editing_order": order,
            "arrived": sum(o.quantity for o in item.orders if o.arrival_date is not None),
            "storage_locations": list(StorageLocation),
            "mediums": list(Medium), "dose_units": list(DoseUnit), "edit_data": _form_values(item)},
            status_code=422)

    for key, value in values.items():
        setattr(order, key, value)
    if new_coa or remove_coa:
        uploads.delete_coa(order.coa_filename)
        order.coa_filename = new_coa
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.get("/inventory/{item_id}/orders/{order_id}/coa")
def get_order_coa(item_id: int, order_id: int, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    order = session.get(Order, order_id) if item else None
    if item is None or order is None or order.inventory_item_id != item.id or not order.coa_filename:
        raise HTTPException(404, "No COA on file")
    path = uploads.coa_path(order.coa_filename)
    if not path.exists():
        raise HTTPException(404, "COA file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(order.coa_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")


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


# ---------------------------------------------------------------- JSON API
# Read-only for now; the reconstitution calculator and dose logging will use it.

def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _to_json(item: InventoryItem) -> dict:
    # For backward compatibility with the old flat structure, return the first order's details if available
    first_order = item.orders[0] if item.orders else None
    return {
        "id": item.id,
        "name": item.name,
        "count": item.count,
        "vial_size_mg": item.vial_size_mg,
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else None,
        "volume_ml": item.volume_ml,
        "units_per_package": item.units_per_package,
        "storage": item.storage.value if item.storage else None,
        "cost": item.cost,
        "vendor": item.vendor,
        "vendor_id": item.vendor_id,
        "has_coa": bool(first_order and first_order.coa_filename),
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
