import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import config, uploads
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import (
    ContactMethodType, InventoryItem, Order, OrderItem, PaymentMethodType, PriceList, Share, ShareCategory,
    WALLET_COINS, User, Vendor, VendorContact, VendorFavorite, VendorPaymentMethod, VendorWallet,
)
from app.library.price_lists.importer import import_for_vendor, summarize_list
from app.library.price_lists.ocr import OcrUnavailable
from app.ingest.readers import OcrMissing, ReadError, read_images, read_xlsx
from app.library.price_lists.reader import read_pdf
from app.library.price_lists.vendor_view import build_price_history
from app.templating import templates
from app.vendors.links import contact_link
from app.vendors.resolve import resolve_contact_method_type, resolve_payment_method_type

router = APIRouter()


# ---------------------------------------------------------------- list page

def _favorited_vendor_ids(session: Session, uid: int) -> set[int]:
    """This user's own VendorFavorite rows, and only this user's -- a favorite is strictly
    per-user, never a global "is this favorited by anyone" flag (Review Focus item 1)."""
    return set(session.scalars(select(VendorFavorite.vendor_id).where(VendorFavorite.user_id == uid)))


def _vendor_recent_order_dates(session: Session, uid: int) -> dict[int, date]:
    """Each vendor's own most recent Order.order_date, scoped to what this viewer may see -- the
    signed-in user's own orders, plus orders belonging to anyone who granted them Inventory sharing.
    Vendor itself is a global/shared row (like the peptide library), but Order/OrderItem are
    private-by-default activity logs: even a bare date, with no other detail attached, is still an
    aggregate over another user's private order history, and a vendor visibly jumping to the top of
    this sort is itself a signal about who ordered from it. Same shared_owner_ids/InventoryItem.owner_id
    join shape as _visible_order_lines_for_vendor -- never a global cross-user aggregate."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY)
    rows = session.execute(
        select(Order.vendor_id, func.max(Order.order_date))
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(Order.vendor_id.is_not(None),
              (InventoryItem.owner_id == uid) | (InventoryItem.owner_id.in_(shared_owner_ids)))
        .group_by(Order.vendor_id)
    ).all()
    return dict(rows)


def _sorted_vendors(vendors: list[Vendor], favorite_ids: set[int], recent_dates: dict[int, date],
                    sort: str) -> list[Vendor]:
    """Favorites always pin to the top (of either sort mode); within each group, alphabetical by
    name, or by most-recent-order-date descending with no-orders-yet vendors sorted last."""
    if sort == "recent":
        def key(v: Vendor):
            recent = recent_dates.get(v.id)
            return (v.id not in favorite_ids, recent is None, recent.toordinal() * -1 if recent else 0,
                    v.name.lower())
    else:
        def key(v: Vendor):
            return (v.id not in favorite_ids, v.name.lower())
    return sorted(vendors, key=key)


@router.get("/vendors")
def list_vendors(request: Request, sort: str = "alpha", session: Session = Depends(get_session),
                 uid: int = Depends(current_user_id)):
    sort = "recent" if sort == "recent" else "alpha"
    vendors = session.scalars(select(Vendor)).all()
    favorite_ids = _favorited_vendor_ids(session, uid)
    recent_dates = _vendor_recent_order_dates(session, uid)
    vendors = _sorted_vendors(vendors, favorite_ids, recent_dates, sort)
    return templates.TemplateResponse(request, "vendors/list.html", {
        "vendors": vendors,
        "favorite_ids": favorite_ids,
        "recent_dates": recent_dates,
        "sort": sort,
        "is_admin": bool(session.get(User, uid).is_admin),
    })


@router.post("/vendors")
async def create_vendor(request: Request, session: Session = Depends(get_session),
                       uid: int = Depends(current_user_id)):
    form = await request.form()
    name = str(form.get("name") or "").strip()

    if not name:
        return RedirectResponse("/vendors", status_code=303)

    # Check for duplicate name
    existing = session.scalar(select(Vendor).where(Vendor.name == name))
    if existing is not None:
        return RedirectResponse("/vendors", status_code=303)

    vendor = Vendor(name=name, created_by_id=uid)
    session.add(vendor)
    session.commit()
    return RedirectResponse(f"/vendors/{vendor.id}", status_code=303)


# ---------------------------------------------------------------- favorite toggle

@router.post("/vendors/{vendor_id}/favorite")
def favorite_vendor(vendor_id: int, next: str = "/vendors", session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    existing = session.scalar(select(VendorFavorite).where(
        VendorFavorite.user_id == uid, VendorFavorite.vendor_id == vendor_id))
    if existing is None:
        session.add(VendorFavorite(user_id=uid, vendor_id=vendor_id))
        session.commit()
    return RedirectResponse(next, status_code=303)


@router.post("/vendors/{vendor_id}/unfavorite")
def unfavorite_vendor(vendor_id: int, next: str = "/vendors", session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    session.query(VendorFavorite).filter_by(user_id=uid, vendor_id=vendor_id).delete()
    session.commit()
    return RedirectResponse(next, status_code=303)


@router.post("/vendors/{vendor_id}/delete")
def delete_vendor(vendor_id: int, next: str = "/vendors", session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    # Vendors are shared by every user, so only an admin may remove one (404, not 403, matching
    # settings' _require_admin: don't reveal the action exists).
    if not session.get(User, uid).is_admin:
        raise HTTPException(404)
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    qr_files = [w.qr_filename for w in vendor.wallets]
    session.delete(vendor)
    session.commit()
    for name in qr_files:
        uploads.delete_wallet_qr(name)
    return RedirectResponse(next, status_code=303)


# ---------------------------------------------------------------- detail page / Purchase History

def _visible_order_lines_for_vendor(session: Session, vendor_id: int, uid: int):
    """This user's own order lines placed with this vendor, plus order lines belonging to anyone
    who granted them Inventory sharing -- the exact blend-and-tag-by-owner-name shape as
    inventory._visible_items/_visible_active_vials (Review Focus item 2 in the design spec): never
    a global cross-user view, never blended without attribution."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY)
    lines = session.scalars(
        select(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(Order.vendor_id == vendor_id,
              (InventoryItem.owner_id == uid) | (InventoryItem.owner_id.in_(shared_owner_ids)))
        .order_by(Order.order_date.desc())
    ).all()
    other_owner_ids = {li.inventory_item.owner_id for li in lines if li.inventory_item.owner_id != uid}
    owner_names = {}
    if other_owner_ids:
        owner_names = dict(session.execute(
            select(User.id, User.username).where(User.id.in_(other_owner_ids))).all())
    return lines, owner_names


def _contact_method_types(session: Session):
    return session.scalars(select(ContactMethodType).order_by(ContactMethodType.name.collate("NOCASE"))).all()


def _payment_method_types(session: Session):
    return session.scalars(select(PaymentMethodType).order_by(PaymentMethodType.name.collate("NOCASE"))).all()


def _contact_view(vendor: Vendor) -> list[dict]:
    """Each contact entry with its clickable link (or None -- rendered as plain text), used by both
    the read-only display and to prefill the edit form's repeatable rows."""
    return [
        {"id": c.id, "method_type_id": c.method_type_id, "method_type_name": c.method_type.name,
        "value": c.value, "link": contact_link(c.method_type.name, c.value)}
        for c in vendor.contacts
    ]


def _form_values(vendor: Vendor) -> dict:
    return {
        "name": vendor.name,
        "website": vendor.website or "",
        "supplier": vendor.supplier or "",
        "contact_name": vendor.contact_name or "",
        "notes": vendor.notes or "",
        "recommended": "" if vendor.recommended is None else ("yes" if vendor.recommended else "no"),
        "price_list_url": vendor.price_list_url or "",
        "price_list_warehouse": "auto",
        "price_list_date": "",
    }


_IMPORT_NOTES = {
    "notpdf": "The file is attached, but only PDFs, photos and spreadsheets (xlsx) can be read for prices. Word files stay as a reference copy.",
    "unreadable": "The file is attached, but it could not be read (it may be protected, damaged or not a price list), so no prices were imported.",
    "needsocr": "The file is attached, but it is a picture of text and this server cannot read those (OCR is not installed), so no prices were imported.",
    "norows": "The file is attached, but no prices were found in it.",
}


def _price_import_view(session: Session, vendor: Vendor, token: str | None) -> dict | None:
    """The banner shown after saving a vendor with a price-list file: a stored list's summary or a plain note."""
    if not token:
        return None
    if token in _IMPORT_NOTES:
        return {"note": _IMPORT_NOTES[token]}
    plist = session.get(PriceList, int(token)) if token.isdigit() else None
    if plist is None or plist.vendor_id != vendor.id:
        return None
    return summarize_list(plist)


def _detail_context(session: Session, vendor: Vendor, uid: int, price_import: str | None = None) -> dict:
    order_lines, owner_names = _visible_order_lines_for_vendor(session, vendor.id, uid)
    is_favorite = session.scalar(select(VendorFavorite).where(
        VendorFavorite.user_id == uid, VendorFavorite.vendor_id == vendor.id)) is not None
    return {
        "vendor": vendor,
        "viewer_id": uid,
        "is_favorite": is_favorite,
        "contacts": _contact_view(vendor),
        "wallet_coins": WALLET_COINS,
        "contact_types": _contact_method_types(session),
        "payment_types": _payment_method_types(session),
        "checked_payment_type_ids": {pm.method_type_id for pm in vendor.payment_methods},
        "order_lines": order_lines,
        "owner_names": owner_names,
        "today": date.today(),
        "edit_data": _form_values(vendor),
        "price_history": build_price_history(session, vendor.id),
        "price_import": _price_import_view(session, vendor, price_import),
    }


@router.get("/vendors/{vendor_id}")
def vendor_detail(vendor_id: int, request: Request, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    return templates.TemplateResponse(request, "vendors/detail.html", _detail_context(
        session, vendor, uid, request.query_params.get("import")))


@router.get("/vendors/{vendor_id}/price-list")
def get_vendor_price_list(vendor_id: int, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    """Serves an uploaded vendor price-list file. Vendor is a global/shared row (unlike a
    per-owner InventoryItem/Order), so this only needs an existence check -- no ownership check,
    mirroring get_order_coa in app/routers/inventory.py exactly otherwise."""
    vendor = session.get(Vendor, vendor_id)
    if vendor is None or not vendor.price_list_filename:
        raise HTTPException(404, "No price list on file")
    path = uploads.price_list_path(vendor.price_list_filename)
    if not path.exists():
        raise HTTPException(404, "Price list file is missing from disk")
    return FileResponse(path, media_type=uploads.price_list_media_type(vendor.price_list_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")


@router.get("/vendors/{vendor_id}/wallets/{wallet_id}/qr")
def get_wallet_qr(vendor_id: int, wallet_id: int, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    """Serves a wallet's QR photo. Vendors are shared by every user, so this only needs an existence check."""
    wallet = session.get(VendorWallet, wallet_id)
    if wallet is None or wallet.vendor_id != vendor_id or not wallet.qr_filename:
        raise HTTPException(404, "No QR code on file")
    path = uploads.wallet_qr_path(wallet.qr_filename)
    if not path.exists():
        raise HTTPException(404, "QR code file is missing from disk")
    return FileResponse(path, media_type=uploads.wallet_qr_media_type(wallet.qr_filename),
                        headers={"X-Content-Type-Options": "nosniff"}, content_disposition_type="inline")


# ---------------------------------------------------------------- edit

async def _read_wallet_rows(request: Request) -> list[dict]:
    """The edit form's repeatable 'wallets-{i}-field' rows, in order. A row with no address is dropped."""
    form = await request.form()
    rows: dict[int, dict] = {}
    for key in form.keys():
        if not key.startswith("wallets-"):
            continue
        idx_str, _, field = key[len("wallets-"):].partition("-")
        if idx_str.isdigit() and field:
            rows.setdefault(int(idx_str), {})[field] = form.get(key)
    out = []
    for _, row in sorted(rows.items()):
        upload = row.get("qr")
        out.append({
            "id": str(row.get("id") or "").strip(), "coin": str(row.get("coin") or "").strip().upper(),
            "address": str(row.get("address") or "").strip(), "network": str(row.get("network") or "").strip(),
            "remove_qr": bool(row.get("remove_qr")),
            "qr": upload if isinstance(upload, UploadFile) and upload.filename else None,
        })
    return [r for r in out if r["address"]]


def _wallet_errors(rows: list[dict]) -> str | None:
    for r in rows:
        if r["coin"] not in WALLET_COINS:
            return "Choose a coin (" + ", ".join(WALLET_COINS) + ") for each wallet address."
        if len(r["address"]) > 200 or re.search(r"\s", r["address"]):
            return "A wallet address is one string of letters and numbers (200 characters at most)."
        if len(r["network"]) > 60:
            return "A wallet's network name is 60 characters at most."
    return None



async def _read_edit_form(
    request: Request,
) -> tuple[dict[str, str], dict[int, dict[str, str]], list[str], UploadFile | None, bool]:
    """Returns (profile fields, contact rows grouped by index -- mirrors inventory.py's
    `_group_lines`'s 'lines-{i}-field' convention but for 'contacts-{i}-field', payment_type_ids,
    the uploaded price-list replacement file if any, and whether "remove price list" was checked)."""
    form = await request.form()
    raw = {
        "name": str(form.get("name") or "").strip(),
        "website": str(form.get("website") or "").strip(),
        "supplier": str(form.get("supplier") or "").strip(),
        "contact_name": str(form.get("contact_name") or "").strip(),
        "notes": str(form.get("notes") or "").strip(),
        "recommended": str(form.get("recommended") or "").strip(),
        "new_payment_type": str(form.get("new_payment_type") or "").strip(),
        "price_list_url": str(form.get("price_list_url") or "").strip(),
        "price_list_warehouse": str(form.get("price_list_warehouse") or "auto").strip().lower(),
        "price_list_date": str(form.get("price_list_date") or "").strip(),
    }
    contact_rows: dict[int, dict[str, str]] = {}
    for key in form.keys():
        if not key.startswith("contacts-"):
            continue
        rest = key[len("contacts-"):]
        idx_str, _, field = rest.partition("-")
        if not idx_str.isdigit() or not field:
            continue
        contact_rows.setdefault(int(idx_str), {})[field] = str(form.get(key) or "").strip()
    payment_type_ids = [v.strip() for v in form.getlist("payment_type_ids")]
    price_list_file_raw = form.get("price_list_file")
    price_list_file = price_list_file_raw if (
        isinstance(price_list_file_raw, UploadFile) and price_list_file_raw.filename) else None
    remove_price_list = bool(form.get("remove_price_list"))
    return raw, dict(sorted(contact_rows.items())), payment_type_ids, price_list_file, remove_price_list


@router.post("/vendors/{vendor_id}")
async def update_vendor(vendor_id: int, request: Request, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")

    raw, contact_rows, payment_type_ids, price_list_file, remove_price_list = await _read_edit_form(request)
    wallet_rows = await _read_wallet_rows(request)
    errors: dict[str, str] = {}
    if (wallet_error := _wallet_errors(wallet_rows)):
        errors["wallets"] = wallet_error
    if not raw["name"]:
        errors["name"] = "Vendor name is required."
    if raw["website"] and not raw["website"].lower().startswith(("http://", "https://")):
        errors["website"] = "Website must be a valid http(s) URL."
    if raw["price_list_url"] and not raw["price_list_url"].lower().startswith(("http://", "https://")):
        errors["price_list_url"] = "Price list link must be a valid http(s) URL."
    if raw["name"] and not errors.get("name"):
        clash = session.scalar(select(Vendor).where(Vendor.name == raw["name"], Vendor.id != vendor.id))
        if clash is not None:
            errors["name"] = "Another vendor already has this name."

    # Uploads are validated (extension/size/sniff) only once the rest of the form is known-good --
    # never store a file for a submission that ends up 422ing anyway, mirroring how the New Order
    # flow's own price-list-replace upload is deferred past its own error gate.
    list_date = date.today()
    if raw["price_list_warehouse"] not in ("auto", "us", "china"):
        errors["price_list_warehouse"] = "Choose detect, US or China for the price list."
    if raw["price_list_date"]:
        try:
            list_date = date.fromisoformat(raw["price_list_date"])
        except ValueError:
            errors["price_list_date"] = "Enter the price list date as a valid date."

    price_list_filename_to_save = None
    if not errors and price_list_file is not None:
        try:
            price_list_filename_to_save = await uploads.save_price_list(price_list_file)
        except uploads.UploadError as e:
            errors["price_list_file"] = str(e)

    saved_qr: list[str] = []  # stored this request; removed again if the submission still fails
    if not errors:
        for r in wallet_rows:
            if r["qr"] is None:
                continue
            try:
                r["qr_saved"] = await uploads.save_wallet_qr(r["qr"])
                saved_qr.append(r["qr_saved"])
            except uploads.UploadError as e:
                errors["wallets"] = str(e)
                break

    if errors:
        for name in saved_qr:
            uploads.delete_wallet_qr(name)
        return templates.TemplateResponse(request, "vendors/detail.html", {
            **_detail_context(session, vendor, uid),
            "form": raw,
            "errors": errors,
        }, status_code=422)

    recommended = None
    if raw["recommended"] == "yes":
        recommended = True
    elif raw["recommended"] == "no":
        recommended = False

    vendor.name = raw["name"]
    vendor.website = raw["website"] or None
    vendor.supplier = raw["supplier"] or None
    vendor.contact_name = raw["contact_name"] or None
    vendor.notes = raw["notes"] or None
    vendor.recommended = recommended

    # Price list: either a file, a URL, or "remove" -- mutually exclusive, matching the New Order
    # staleness-replace path's own pattern. Leaving all three untouched (no file, no URL typed, box
    # unchecked) means "leave the existing price list alone" -- these three fields aren't touched at
    # all in that case. The OLD file is only deleted from disk after a successful commit (Fix 5:
    # never delete a file the DB still references in case the commit never lands).
    old_price_list_filename = vendor.price_list_filename
    price_list_changed = False
    if price_list_filename_to_save is not None:
        vendor.price_list_filename = price_list_filename_to_save
        vendor.price_list_url = None
        price_list_changed = True
    elif raw["price_list_url"]:
        vendor.price_list_url = raw["price_list_url"]
        vendor.price_list_filename = None
        price_list_changed = True
    elif remove_price_list:
        vendor.price_list_filename = None
        vendor.price_list_url = None
        price_list_changed = True
    if price_list_changed:
        vendor.price_list_updated_at = date.today()

    vendor.contacts.clear()
    session.flush()  # the deletes must land before the unique/insert side below, mirroring
                     # protocols.save_protocol's items.clear()-then-rebuild convention
    for row in contact_rows.values():
        value = row.get("value", "")
        if not value:
            continue
        method_type_id = row.get("method_type_id", "")
        new_name = row.get("new_method_type", "")
        method_type = None
        if method_type_id == "__new__" or (not method_type_id and new_name):
            method_type = resolve_contact_method_type(session, new_name)
        elif method_type_id.isdigit():
            method_type = session.get(ContactMethodType, int(method_type_id))
        if method_type is None:
            continue
        vendor.contacts.append(VendorContact(method_type_id=method_type.id, value=value))

    vendor.payment_methods.clear()
    session.flush()
    type_ids: set[int] = set()
    for v in payment_type_ids:
        if v.isdigit() and session.get(PaymentMethodType, int(v)) is not None:
            type_ids.add(int(v))
    if raw["new_payment_type"]:
        new_type = resolve_payment_method_type(session, raw["new_payment_type"])
        if new_type is not None:
            type_ids.add(new_type.id)
    for type_id in type_ids:
        vendor.payment_methods.append(VendorPaymentMethod(method_type_id=type_id))

    # Wallets: rows that carry an id of this vendor's own wallet update it in place (keeping its QR unless a
    # new one is uploaded or "remove" is ticked); rows without one are new; wallets left out are deleted.
    existing = {str(w.id): w for w in vendor.wallets}
    kept: set[str] = set()
    stale_qr: list[str] = []
    for r in wallet_rows:
        wallet = existing.get(r["id"]) if r["id"] not in kept else None
        if wallet is None:
            wallet = VendorWallet()
            vendor.wallets.append(wallet)
        else:
            kept.add(r["id"])
        wallet.coin, wallet.address, wallet.network = r["coin"], r["address"], r["network"] or None
        if r.get("qr_saved") or r["remove_qr"]:
            if wallet.qr_filename:
                stale_qr.append(wallet.qr_filename)
            wallet.qr_filename = r.get("qr_saved")
    for wid, wallet in existing.items():
        if wid not in kept:
            if wallet.qr_filename:
                stale_qr.append(wallet.qr_filename)
            vendor.wallets.remove(wallet)

    session.commit()
    for name in stale_qr:
        uploads.delete_wallet_qr(name)
    if price_list_changed and old_price_list_filename and old_price_list_filename != vendor.price_list_filename:
        uploads.delete_price_list(old_price_list_filename)

    target = f"/vendors/{vendor.id}"
    if price_list_filename_to_save is not None:
        outcome = await _import_saved_price_list(session, vendor, raw["price_list_warehouse"], list_date)
        target += f"?import={outcome}"
    return RedirectResponse(target, status_code=303)


_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def _read_photo_file(path):
    """A photo of a price list, turned upright and read by text recognition (same reader the price list inbox uses)."""
    from PIL import Image, ImageOps

    from app import body_photos  # noqa: F401  (registers the HEIC opener)
    with Image.open(path) as opened:
        if opened.width * opened.height > config.PHOTO_MAX_PIXELS:
            raise ReadError("the photo is too large")
        image = ImageOps.exif_transpose(opened).convert("RGB")
    return read_images([image])


def _read_sheet_file(path):
    return read_xlsx(path.read_bytes())


async def _import_saved_price_list(session: Session, vendor: Vendor, warehouse: str, list_date: date) -> str:
    """Read the file just attached to the vendor (a PDF, a photo by text recognition, or an xlsx spreadsheet) and store its
    prices. Returns the new list's id (as text) or a code for why nothing was stored; the file stays attached either way."""
    path = uploads.price_list_path(vendor.price_list_filename)
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".xlsx"} and suffix not in _PHOTO_SUFFIXES:
        return "notpdf"
    try:
        if suffix == ".pdf":
            data = await run_in_threadpool(read_pdf, path)
        elif suffix == ".xlsx":
            data = await run_in_threadpool(_read_sheet_file, path)
        else:
            data = await run_in_threadpool(_read_photo_file, path)
        if warehouse == "auto":  # what the file says about its warehouse; a list that names none is China
            warehouse = data.warehouse_hint or "china"
        report = import_for_vendor(session, vendor, data, warehouse=warehouse, list_date=list_date)
    except (OcrUnavailable, OcrMissing):
        session.rollback()
        return "needsocr"
    except Exception:  # a damaged or protected file is reported on the page, never a server error
        session.rollback()
        return "unreadable"
    return str(report.list_id) if report.list_id else "norows"
