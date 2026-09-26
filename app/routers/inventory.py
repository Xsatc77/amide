import re
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
from app.models import ActiveVial, Category, DoseUnit, InventoryItem, Medium, Order, OrderItem, Sale, Share, ShareCategory, StorageLocation, User
from app.templating import templates

router = APIRouter()

# Text fields on the add/edit form, in form order.
ITEM_FIELDS = ("name", "category", "count", "vial_size_mg", "vial_size_unit", "medium",
              "volume_ml", "units_per_package", "storage", "cost", "vendor", "notes")
ORDER_HEADER_FIELDS = ("order_date", "shipped_date", "tracking_site", "tracking_number", "vendor", "tax", "shipping")
ORDER_LINE_FIELDS = ("quantity", "cost", "lot_number", "expiration_date", "coa_vial_size_mg", "coa_purity_pct")
FORM_FIELDS = tuple(dict.fromkeys(ITEM_FIELDS + ORDER_HEADER_FIELDS + ORDER_LINE_FIELDS + ("received_quantity",)))

_LINE_KEY = re.compile(r"^lines-(\d+)-(\w+)$")


def _group_lines(form: dict[str, list[str]]) -> dict[int, dict[str, str]]:
    """Groups a New Order form's repeated 'lines-{i}-field' keys by index, mirroring
    app/protocols/forms.py's identical 'items-{i}-field' convention."""
    lines: dict[int, dict[str, str]] = {}
    for key, values in form.items():
        m = _LINE_KEY.match(key)
        if m:
            i, field = int(m.group(1)), m.group(2)
            lines.setdefault(i, {})[field] = values[0] if values else ""
    return dict(sorted(lines.items()))


_NEW_LINE_ITEM_FIELDS = ("mode", "item_id", "name", "category", "medium", "vial_size_mg",
                        "vial_size_unit", "volume_ml", "units_per_package", "storage", "notes",
                        "quantity", "cost", "lot_number", "expiration_date", "coa_vial_size_mg",
                        "coa_purity_pct")


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


def _parse_order_header_fields(raw: dict[str, str], session: Session, uid: int, errors: dict) -> dict:
    """Parses the fields shared by every line in one order: dates, tracking, vendor, tax/shipping.
    No arrival_date here -- that's set only by check-in (see check_in_order)."""
    values: dict = {}
    values["order_date"] = _parse_date(raw["order_date"], "order_date", errors)
    if values["order_date"] is None and "order_date" not in errors:
        errors["order_date"] = "Order date is required."
    values["shipped_date"] = _parse_date(raw["shipped_date"], "shipped_date", errors)
    if (values["shipped_date"] and values["order_date"] and "order_date" not in errors
            and "shipped_date" not in errors and values["shipped_date"] < values["order_date"]):
        errors["shipped_date"] = "Shipped date can't be before the order date."

    values["tracking_site"] = raw["tracking_site"] or None
    if values["tracking_site"] and not values["tracking_site"].lower().startswith(("http://", "https://")):
        errors["tracking_site"] = "Tracking site must be a valid http(s) URL."
    values["tracking_number"] = raw["tracking_number"] or None

    vendor = resolve_vendor(session, uid, raw["vendor"])
    values["vendor_id"] = vendor.id if vendor else None
    values["vendor"] = vendor.name if vendor else None

    values["tax_cents"] = _parse_money(raw["tax"], "tax", "Tax", errors)
    values["shipping_cents"] = _parse_money(raw["shipping"], "shipping", "Shipping", errors)
    return values


def _parse_order_line_fields(raw: dict[str, str], *, prefix: str = "") -> tuple[dict, dict]:
    """Parses one order line's own fields (quantity/cost/lot/expiration/COA numbers) -- the part
    that varies per item within an order, as opposed to _parse_order_header_fields' shared fields.
    `prefix` namespaces error keys for the New Order form's repeated lines (e.g. 'lines-0-')."""
    errors: dict[str, str] = {}
    values: dict = {}

    values["quantity"] = None
    try:
        if raw["quantity"]:
            values["quantity"] = int(raw["quantity"])
        if not values["quantity"] or values["quantity"] <= 0:
            errors[f"{prefix}quantity"] = "Quantity must be a whole number greater than 0."
    except ValueError:
        errors[f"{prefix}quantity"] = "Quantity must be a whole number."

    values["cost_cents"] = _parse_money(raw["cost"], f"{prefix}cost", "Cost", errors)
    values["lot_number"] = raw["lot_number"] or None
    values["expiration_date"] = _parse_date(raw["expiration_date"], f"{prefix}expiration_date", errors)
    values["coa_vial_size_mg"] = _parse_positive_float(
        raw["coa_vial_size_mg"], f"{prefix}coa_vial_size_mg", "Lab vial size", errors)
    values["coa_purity_pct"] = None
    purity = raw["coa_purity_pct"].rstrip("%").strip()
    if purity:
        try:
            values["coa_purity_pct"] = float(purity)
            if not 0 <= values["coa_purity_pct"] <= 100:
                errors[f"{prefix}coa_purity_pct"] = "Purity must be between 0 and 100%."
        except ValueError:
            errors[f"{prefix}coa_purity_pct"] = "Purity must be a number, e.g. 99.2."

    return values, errors


def _parse_sale_fields(raw: dict[str, str], prefix: str, label: str, max_quantity: int,
                       errors: dict) -> dict:
    """Parses one item's portion of a sale (quantity/price), keyed by `prefix` ('' for the item
    the Sold button lives on, 'bac_' for a bundled BAC Water portion). Errors are stored under the
    prefixed field name so the template highlights the right input."""
    values: dict = {}
    qty_field, price_field = f"{prefix}quantity", f"{prefix}price"

    values["quantity"] = None
    try:
        if raw[qty_field]:
            values["quantity"] = int(raw[qty_field])
        if not values["quantity"] or values["quantity"] <= 0:
            errors[qty_field] = f"{label} quantity must be a whole number greater than 0."
        elif values["quantity"] > max_quantity:
            errors[qty_field] = f"Only {max_quantity} available to sell."
    except ValueError:
        errors[qty_field] = f"{label} quantity must be a whole number."

    values["price_cents"] = _parse_money(raw[price_field], price_field, f"{label} price", errors)
    if values["price_cents"] is None and price_field not in errors:
        errors[price_field] = f"{label} price is required."

    return values


async def _read_sale_form(request: Request) -> dict[str, str]:
    form = await request.form()
    fields = ("sale_date", "quantity", "price", "include_bac_water", "bac_item_id", "bac_quantity", "bac_price")
    return {f: str(form.get(f) or "").strip() for f in fields}


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


def _bac_water_options(session: Session, uid: int) -> list[InventoryItem]:
    """The owner's BAC Water items that currently have stock, for the Sold dialog's bundle
    dropdown on a Medicine item's page."""
    items = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid, InventoryItem.category == Category.BAC_WATER)
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()
    return [i for i in items if i.available_count > 0]


def _arrived_quantity(item: InventoryItem) -> int:
    """Sum of received_quantity across this item's checked-in order lines -- the numerator
    available_count itself uses, exposed separately for the detail page's "Arrived" stat."""
    return sum((li.received_quantity or 0) for li in item.order_items if li.order.arrival_date is not None)


def _detail_context(session: Session, item: InventoryItem, uid: int) -> dict:
    """Template keys detail.html needs regardless of which route rendered it -- the GET route, or
    any of update_item/add_order/update_order/sell_item re-rendering it after a validation
    error."""
    return {
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
        "bac_water_options": _bac_water_options(session, uid) if item.category == Category.MEDICINE else [],
        "today_iso": date.today().isoformat(),
        "checkin_data": {
            li.order_id: [
                {"id": sib.id, "quantity": sib.quantity, "item_name": sib.inventory_item.name}
                for sib in li.order.items
            ]
            for li in item.order_items if li.order.arrival_date is None
        },
    }


def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200,
                 new_order_form: dict | None = None, new_order_errors=None,
                 new_order_line_groups: dict | None = None):
    uid = request.state.user.id
    items, owner_names = _visible_items(session, uid)
    medicine_items = [i for i in items if i.category == Category.MEDICINE]
    bac_water_items = [i for i in items if i.category == Category.BAC_WATER]
    supply_items = [i for i in items if i.category == Category.SUPPLY]
    in_transit_lines = [
        (item, li) for item in medicine_items + bac_water_items for li in item.order_items
        if li.order.arrival_date is None
    ]
    in_transit_by_order: dict[int, list] = {}
    for item, li in in_transit_lines:
        in_transit_by_order.setdefault(li.order_id, []).append((item, li))
    in_transit_groups = [{"order": lines[0][1].order, "lines": lines} for lines in in_transit_by_order.values()]
    own_lyo_ids = [i.id for i in medicine_items if i.owner_id == uid and i.medium == Medium.LYOPHILIZED]
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
            "medicine_items": medicine_items,
            "bac_water_items": bac_water_items,
            "supply_items": supply_items,
            "in_transit_groups": in_transit_groups,
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
            "new_order_form": new_order_form,
            "new_order_errors": new_order_errors or {},
            "new_order_line_groups": new_order_line_groups or {},
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
    arrived = _arrived_quantity(item)
    return templates.TemplateResponse(request, "inventory/detail.html", {
        "item": item,
        "is_owner": item.owner_id == uid,
        "arrived": arrived,
        "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
        **_detail_context(session, item, uid),
    })


@router.post("/inventory")
async def create_item(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    raw, coa, _ = await _read_form(request)
    errors: dict[str, str] = {}
    category = _parse_choice(Category, raw["category"], Category.MEDICINE, "category", errors)
    values, item_errors = _parse_item_fields(raw, session, uid, category)
    errors.update(item_errors)

    order_header = {}
    order_line = {}
    if category != Category.SUPPLY:
        order_header = _parse_order_header_fields(raw, session, uid, errors)
        order_line, line_errors = _parse_order_line_fields(raw)
        errors.update(line_errors)

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
        order = Order(**order_header)
        session.add(order)
        li = OrderItem(**order_line, coa_filename=coa_filename)
        item.order_items.append(li)
        order.items.append(li)
    session.add(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


async def _read_new_order_form(request: Request) -> tuple[dict[str, list[str]], dict[int, UploadFile]]:
    form = await request.form()
    text_form: dict[str, list[str]] = {}
    coa_files: dict[int, UploadFile] = {}
    for key in form.keys():
        m = _LINE_KEY.match(key)
        if m and m.group(2) == "coa":
            f = form.get(key)
            if isinstance(f, UploadFile) and f.filename:
                coa_files[int(m.group(1))] = f
            continue
        text_form[key] = [str(v) for v in form.getlist(key)]
    return text_form, coa_files


@router.post("/inventory/orders")
async def create_multi_item_order(request: Request, session: Session = Depends(get_session),
                                  uid: int = Depends(current_user_id)):
    form, coa_files = await _read_new_order_form(request)
    header_raw = {f: (form.get(f) or [""])[0] for f in ORDER_HEADER_FIELDS}
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(header_raw, session, uid, errors)

    line_groups = _group_lines(form)
    if not line_groups:
        errors["lines"] = "Add at least one item."

    parsed_lines = []
    for i, raw_group in line_groups.items():
        line_raw = {f: raw_group.get(f, "") for f in _NEW_LINE_ITEM_FIELDS}
        prefix = f"lines-{i}-"
        line_errors: dict[str, str] = {}
        existing_item = None
        new_item_values = None

        if line_raw["mode"] == "existing":
            existing_item = None
            if line_raw["item_id"].isdigit():
                existing_item = _own_item(session, int(line_raw["item_id"]), uid)
            if existing_item is None or existing_item.category == Category.SUPPLY:
                line_errors[f"{prefix}item_id"] = "Select an item you already track."
        else:
            category = _parse_choice(Category, line_raw["category"], Category.MEDICINE, f"{prefix}category", line_errors)
            if category == Category.SUPPLY:
                line_errors[f"{prefix}category"] = "New order lines can only be Medicine or BAC Water."
            else:
                new_item_values, item_errs = _parse_item_fields(line_raw, session, uid, category)
                if category == Category.MEDICINE and new_item_values.get("medium") is None and "medium" not in item_errs:
                    item_errs["medium"] = "Medium is required."
                for f, msg in item_errs.items():
                    line_errors[f"{prefix}{f}"] = msg

        line_values, line_val_errors = _parse_order_line_fields(line_raw, prefix=prefix)
        line_errors.update(line_val_errors)

        errors.update(line_errors)
        parsed_lines.append({"index": i, "existing_item": existing_item,
                             "new_item_values": new_item_values, "line_values": line_values})

    coa_filenames: dict[int, str | None] = {}
    if not errors:
        for line in parsed_lines:
            coa = coa_files.get(line["index"])
            if coa is not None:
                try:
                    coa_filenames[line["index"]] = await uploads.save_coa(coa)
                except uploads.UploadError as e:
                    errors[f"lines-{line['index']}-coa"] = str(e)
            else:
                coa_filenames[line["index"]] = None

    if errors:
        return _render_list(request, session, new_order_form=header_raw, new_order_errors=errors,
                            new_order_line_groups=line_groups, status_code=422)

    order = Order(**header_values)
    session.add(order)
    # As in add_order: an already-persistent existing_item's not-yet-loaded order_items collection
    # triggers a premature autoflush on the first of these two appends -- before the *other* side
    # of the association is wired up in memory -- violating order_items' order_id/inventory_item_id
    # NOT NULL constraints. no_autoflush defers that flush until both sides are set, right before
    # the explicit commit below.
    with session.no_autoflush:
        for line in parsed_lines:
            item = line["existing_item"]
            if item is None:
                item = InventoryItem(**line["new_item_values"], owner_id=uid)
                session.add(item)
            li = OrderItem(**line["line_values"], coa_filename=coa_filenames[line["index"]])
            item.order_items.append(li)
            order.items.append(li)
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
        arrived = _arrived_quantity(item)
        return templates.TemplateResponse(request, "inventory/detail.html", {
            "item": item, "is_owner": True, "arrived": arrived,
            "form": raw, "errors": errors, "editing": item,
            "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
            **_detail_context(session, item, uid),
        }, status_code=422)

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
    order_ids = {li.order_id for li in item.order_items}
    for li in item.order_items:
        uploads.delete_coa(li.coa_filename)
    session.delete(item)
    session.flush()
    for order_id in order_ids:
        order = session.get(Order, order_id)
        if order is not None and not order.items:
            session.delete(order)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


def _own_order_item(session: Session, item_id: int, order_item_id: int, uid: int) -> OrderItem | None:
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    li = session.get(OrderItem, order_item_id)
    return li if li is not None and li.inventory_item_id == item.id else None


def _own_order_for_item(session: Session, item_id: int, order_id: int, uid: int) -> Order | None:
    """The Order if `item_id` is one of the caller's own items with a line in it -- check-in acts
    on every line in the order at once, not just this item's."""
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    order = session.get(Order, order_id)
    if order is None or not any(li.inventory_item_id == item.id for li in order.items):
        return None
    return order


@router.post("/inventory/{item_id}/orders/{order_id}/check-in")
async def check_in_order(item_id: int, order_id: int, request: Request,
                         session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order_for_item(session, item_id, order_id, uid)
    if order is None:
        raise HTTPException(404, "Order not found")

    form = await request.form()
    raw = {"arrival_date": str(form.get("arrival_date") or "").strip()}
    for li in order.items:
        raw[f"received_quantity_{li.id}"] = str(form.get(f"received_quantity_{li.id}") or "").strip()
        raw[f"received_note_{li.id}"] = str(form.get(f"received_note_{li.id}") or "").strip()

    errors: dict[str, str] = {}
    arrival_date = _parse_date(raw["arrival_date"], "arrival_date", errors)
    if arrival_date is None and "arrival_date" not in errors:
        errors["arrival_date"] = "Arrival date is required."
    elif arrival_date and arrival_date > date.today():
        errors["arrival_date"] = "Arrival date can't be in the future."
    elif arrival_date and order.shipped_date and arrival_date < order.shipped_date:
        errors["arrival_date"] = "Arrival date can't be before the shipped date."
    elif arrival_date and arrival_date < order.order_date:
        errors["arrival_date"] = "Arrival date can't be before the order date."

    received: dict[int, tuple[int, str | None]] = {}
    for li in order.items:
        field = f"received_quantity_{li.id}"
        raw_qty = raw[field]
        try:
            qty = int(raw_qty) if raw_qty else li.quantity
        except ValueError:
            errors[field] = "Must be a whole number."
            continue
        if not 0 <= qty <= li.quantity:
            errors[field] = f"Must be between 0 and {li.quantity}."
            continue
        received[li.id] = (qty, raw[f"received_note_{li.id}"] or None)

    if errors:
        item = _own_item(session, item_id, uid)
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "checkin_errors": errors, "checkin_form": raw,
            "checkin_order": order,
            "arrived": _arrived_quantity(item),
            "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
            **_detail_context(session, item, uid)},
            status_code=422)

    order.arrival_date = arrival_date
    for li in order.items:
        qty, note = received[li.id]
        li.received_quantity = qty
        li.received_note = note
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.post("/inventory/{item_id}/orders")
async def add_order(item_id: int, request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None or item.category == Category.SUPPLY:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, _ = await _read_form(request)
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(raw, session, uid, errors)
    line_values, line_errors = _parse_order_line_fields(raw)
    errors.update(line_errors)

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
            "arrived": _arrived_quantity(item),
            "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
            **_detail_context(session, item, uid)},
            status_code=422)

    order = Order(**header_values)
    session.add(order)
    li = OrderItem(**line_values, coa_filename=coa_filename)
    # item (unlike a freshly created item in create_item) is already persistent here, so the first
    # of these two appends that touches its not-yet-loaded order_items collection triggers a
    # premature autoflush -- before the *other* side of the association is wired up in memory --
    # violating order_items' order_id/inventory_item_id NOT NULL constraints. no_autoflush defers
    # that flush until both sides are set, right before the explicit commit below.
    with session.no_autoflush:
        item.order_items.append(li)
        order.items.append(li)
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.post("/inventory/{item_id}/sales")
async def sell_item(item_id: int, request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None or item.category == Category.SUPPLY:
        raise HTTPException(404, "Inventory item not found")

    raw = await _read_sale_form(request)
    errors: dict[str, str] = {}

    sale_date = _parse_date(raw["sale_date"], "sale_date", errors)
    if sale_date is None and "sale_date" not in errors:
        errors["sale_date"] = "Sale date is required."
    elif sale_date and sale_date > date.today():
        errors["sale_date"] = "Sale date can't be in the future."

    main = _parse_sale_fields(raw, "", item.name, item.available_count, errors)

    bac_item = None
    bac_values = None
    include_bac_water = bool(raw["include_bac_water"]) and item.category == Category.MEDICINE
    if include_bac_water:
        bac_item = None
        if raw["bac_item_id"].isdigit():
            try:
                bac_item = _own_item(session, int(raw["bac_item_id"]), uid)
            except (ValueError, OverflowError):
                bac_item = None
        if bac_item is None or bac_item.category != Category.BAC_WATER:
            errors["bac_item_id"] = "Select a BAC Water item."
        else:
            bac_values = _parse_sale_fields(raw, "bac_", bac_item.name, bac_item.available_count, errors)

    if errors:
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "sale_errors": errors, "sale_form": raw,
            "arrived": _arrived_quantity(item),
            "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
            **_detail_context(session, item, uid)},
            status_code=422)

    item.sales.append(Sale(quantity=main["quantity"], sale_date=sale_date, price_cents=main["price_cents"]))
    item.sold_count += main["quantity"]
    if bac_item is not None:
        bac_item.sales.append(Sale(quantity=bac_values["quantity"], sale_date=sale_date,
                                   price_cents=bac_values["price_cents"]))
        bac_item.sold_count += bac_values["quantity"]
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.post("/inventory/{item_id}/orders/{order_item_id}")
async def update_order(item_id: int, order_item_id: int, request: Request,
                       session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    li = _own_order_item(session, item_id, order_item_id, uid)
    if li is None:
        raise HTTPException(404, "Order not found")

    raw, coa, remove_coa = await _read_form(request)
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(raw, session, uid, errors)
    line_values, line_errors = _parse_order_line_fields(raw)
    errors.update(line_errors)

    received_quantity = li.received_quantity
    if li.order.arrival_date is not None:
        raw_received = str((await request.form()).get("received_quantity") or "").strip()
        if raw_received:
            try:
                received_quantity = int(raw_received)
            except ValueError:
                errors["received_quantity"] = "Must be a whole number."
        if ("received_quantity" not in errors and received_quantity is not None
                and line_values["quantity"] is not None
                and not 0 <= received_quantity <= line_values["quantity"]):
            errors["received_quantity"] = f"Must be between 0 and {line_values['quantity']}."

    new_coa = None
    if not errors and coa:
        try:
            new_coa = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        item = li.inventory_item
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "editing_order": li,
            "arrived": _arrived_quantity(item),
            "sorted_order_items": sorted(item.order_items, key=lambda li: (li.order.order_date, li.id), reverse=True),
            **_detail_context(session, item, uid)},
            status_code=422)

    for key, value in header_values.items():
        setattr(li.order, key, value)
    for key, value in line_values.items():
        setattr(li, key, value)
    li.received_quantity = received_quantity
    if new_coa or remove_coa:
        uploads.delete_coa(li.coa_filename)
        li.coa_filename = new_coa
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.get("/inventory/{item_id}/orders/{order_item_id}/coa")
def get_order_coa(item_id: int, order_item_id: int, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    li = session.get(OrderItem, order_item_id) if item else None
    if item is None or li is None or li.inventory_item_id != item.id or not li.coa_filename:
        raise HTTPException(404, "No COA on file")
    path = uploads.coa_path(li.coa_filename)
    if not path.exists():
        raise HTTPException(404, "COA file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(li.coa_filename),
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
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category.value,
        "available_count": item.available_count,
        "count": item.count,
        "vial_size_mg": item.vial_size_mg,
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else None,
        "volume_ml": item.volume_ml,
        "units_per_package": item.units_per_package,
        "storage": item.storage.value if item.storage else None,
        "cost": item.cost,
        "vendor": item.vendor,
        "notes": item.notes,
        "orders": [{
            "id": li.id, "quantity": li.quantity, "received_quantity": li.received_quantity,
            "received_note": li.received_note,
            "order_date": _iso(li.order.order_date), "shipped_date": _iso(li.order.shipped_date),
            "arrival_date": _iso(li.order.arrival_date), "tracking_site": li.order.tracking_site,
            "tracking_number": li.order.tracking_number, "vendor": li.order.vendor,
            "lot_number": li.lot_number, "cost": li.cost, "tax": li.order.tax,
            "shipping": li.order.shipping, "expiration_date": _iso(li.expiration_date),
            "has_coa": bool(li.coa_filename), "coa_vial_size_mg": li.coa_vial_size_mg,
            "coa_purity_pct": li.coa_purity_pct,
        } for li in sorted(item.order_items, key=lambda li: li.order.order_date, reverse=True)],
        "sales": [{
            "id": s.id, "quantity": s.quantity, "sale_date": _iso(s.sale_date), "price": s.price,
        } for s in item.sales],
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
