from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import uploads
from app.db import get_session
from app.models import InventoryItem, Medium
from app.templating import templates

router = APIRouter()

# Text fields on the add/edit form, in form order.
FORM_FIELDS = (
    "name", "count", "vial_size_mg", "medium", "cost", "vendor",
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


def _parse_form(raw: dict[str, str]) -> tuple[dict, dict[str, str]]:
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

    values["vial_size_mg"] = _parse_positive_float(raw["vial_size_mg"], "vial_size_mg", "Vial size", errors)

    values["medium"] = None
    if raw["medium"]:
        try:
            values["medium"] = Medium(raw["medium"])
        except ValueError:
            errors["medium"] = "Pick a medium from the list."

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

    values["vendor"] = raw["vendor"] or None
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
        "medium": item.medium.value if item.medium else "",
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


def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200):
    items = session.scalars(select(InventoryItem).order_by(InventoryItem.name.collate("NOCASE"))).all()
    return templates.TemplateResponse(
        request,
        "inventory/list.html",
        {
            "items": items,
            "edit_data": {i.id: _form_values(i) for i in items},
            "mediums": list(Medium),
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
async def create_item(request: Request, session: Session = Depends(get_session)):
    raw, coa, _ = await _read_form(request)
    values, errors = _parse_form(raw)

    coa_filename = None
    if not errors and coa:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return _render_list(request, session, form=raw, errors=errors, status_code=422)

    session.add(InventoryItem(**values, coa_filename=coa_filename))
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}")
async def update_item(item_id: int, request: Request, session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_form(raw)

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
def delete_item(item_id: int, session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    uploads.delete_coa(item.coa_filename)
    session.delete(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)


@router.get("/inventory/{item_id}/coa")
def get_coa(item_id: int, session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
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
        "medium": item.medium.value if item.medium else None,
        "cost": item.cost,
        "vendor": item.vendor,
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
def api_list_inventory(session: Session = Depends(get_session)):
    items = session.scalars(select(InventoryItem).order_by(InventoryItem.name.collate("NOCASE"))).all()
    return [_to_json(i) for i in items]


@router.get("/api/inventory/{item_id}")
def api_get_inventory(item_id: int, session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    return _to_json(item)
