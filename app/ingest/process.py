"""Reading, deciding and importing what the intake stored; approve, reject and undo."""

import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.ingest import decide as deciding, infer, readers
from app.library.price_lists.importer import import_for_vendor
from app.library.price_lists.reader import PriceListData
from app.models import IngestItem, IngestSource, PriceList, Vendor, naive_utcnow

lock = threading.RLock()                    # the worker, approvals and undo never touch the same item at once


def _group(session: Session, key: str) -> list[IngestItem]:
    return list(session.scalars(select(IngestItem).where(IngestItem.group_key == key).order_by(IngestItem.id)))


def _read(items: list[IngestItem], recognizer) -> PriceListData:
    first = items[0]
    if first.kind == "text":
        return readers.read_text("\n".join(i.caption or "" for i in items))
    path = config.INGEST_DIR / (first.stored_file or "")
    if not path.is_file():
        raise readers.ReadError("the stored file is missing")
    if first.kind == "pdf":
        return readers.read_pdf_file(path, recognize=recognizer)
    if first.kind == "xlsx":
        return readers.read_xlsx(path.read_bytes())
    from PIL import Image
    images = []
    for i in items:
        p = config.INGEST_DIR / (i.stored_file or "")
        if not p.is_file():
            raise readers.ReadError("a stored photo is missing")
        try:
            image = Image.open(p)
            if image.width * image.height > config.PHOTO_MAX_PIXELS:      # judged from the header, before any pixels are decoded
                raise readers.ReadError("a photo is too large")
            images.append(image.convert("RGB"))
        except readers.ReadError:
            raise
        except Exception:
            raise readers.ReadError("a photo could not be opened") from None
    return readers.read_images(images, recognize=recognizer)


def _set(items, now, **fields):
    for i in items:
        for k, v in fields.items():
            setattr(i, k, v)
        i.decided_at = now


def _drop_files(items):
    for i in items:
        if i.stored_file:
            (config.INGEST_DIR / i.stored_file).unlink(missing_ok=True)
            i.stored_file = None


def _context(items, source, data):
    first = items[0]
    caption = " ".join(i.caption or "" for i in items if i.caption)
    warehouse, assumed = infer.infer_warehouse(data.warehouse_hint, caption, first.filename, source.default_warehouse)
    texts = [caption, first.filename or "", data.shipping_note or ""]
    return warehouse, assumed, infer.infer_date(texts, first.received_at.date())


def _import(session, items, vendor, data, warehouse, list_date, now, user_id=None):
    report = import_for_vendor(session, vendor, data, warehouse=warehouse, list_date=list_date)
    _set(items, now, status="imported", reason=None, vendor_id=vendor.id, warehouse=warehouse, list_date=list_date,
         price_list_id=report.list_id, rows_found=report.rows, rows_matched=report.matched, decided_by=user_id)
    _drop_files(items)


def _file_dupe(session, items):
    seen = session.scalar(select(IngestItem.id).where(
        IngestItem.source_id == items[0].source_id, IngestItem.file_hash == items[0].file_hash, IngestItem.group_key != items[0].group_key,
        IngestItem.status.in_(("imported", "needs_review", "duplicate", "undone"))))
    return seen is not None


def _handle(session, items, now, recognizer):
    source = session.get(IngestSource, items[0].source_id)
    vendor = session.get(Vendor, source.vendor_id) if source.vendor_id else None
    try:
        data = _read(items, recognizer)
    except readers.ReadError as exc:
        _set(items, now, status="failed", reason=str(exc)[:300])
        return
    ok, why = readers.looks_like_price_list(data, " ".join(i.caption or "" for i in items), items[0].filename)
    if not ok:
        _set(items, now, status="ignored", reason=why)
        _drop_files(items)
        return
    warehouse, assumed, list_date = _context(items, source, data)
    verdict = deciding.decide(session, vendor=vendor, enabled=source.enabled, data=data, warehouse=warehouse, assumed=assumed,
                              list_date=list_date, file_dupe=_file_dupe(session, items))
    common = dict(vendor_id=vendor.id if vendor else None, warehouse=warehouse, list_date=list_date, rows_found=len(data.rows))
    if verdict.status == "imported":
        _import(session, items, vendor, data, warehouse, list_date, now)
    elif verdict.status == "duplicate":
        _set(items, now, status="duplicate", reason=verdict.reason, **common)
        _drop_files(items)
    else:
        _set(items, now, status="needs_review", reason=verdict.reason, **common)


def process_due(session_factory, now: datetime, recognizer=None) -> int:
    """Handle every received group that is ready: single files at once, photos 60 seconds after the last one arrived."""
    handled = 0
    with lock, session_factory() as session:
        waiting = list(session.scalars(select(IngestItem).where(IngestItem.status == "received").order_by(IngestItem.id)))
        for key in dict.fromkeys(i.group_key for i in waiting):
            items = [i for i in _group(session, key) if i.status == "received"]
            if not items:
                continue
            if items[0].kind == "image" and max(i.created_at for i in items) > now - timedelta(seconds=config.INGEST_SETTLE_SECONDS):
                continue
            try:
                if len(_group(session, key)) > len(items):          # part of this list was already decided: never stand in for it
                    _set(items, now, status="needs_review", reason="a late part of an earlier list; check it against that list")
                else:
                    _handle(session, items, now, recognizer)
            except Exception as exc:                       # a bug or a bad file must never stop the others
                session.rollback()
                items = [i for i in _group(session, key) if i.status == "received"]
                _set(items, now, status="failed", reason=f"unexpected error ({type(exc).__name__})")
            session.commit()
            handled += 1
    return handled


def _pending(session, item_id, allowed):
    item = session.get(IngestItem, item_id)
    if item is not None:
        session.refresh(item)                       # another session (the worker) may have changed it
    if item is None:
        raise LookupError("item not found")
    if item.status not in allowed:
        raise ValueError(f"an item that is {item.status} cannot be changed this way")
    return item, _group(session, item.group_key)


def approve_group(session: Session, item_id: int, *, vendor_id: int, warehouse: str, list_date: date, user_id: int,
                  recognizer=None, now: datetime | None = None) -> IngestItem:
    with lock:
        item, items = _pending(session, item_id, ("needs_review", "failed"))
        vendor = session.get(Vendor, vendor_id)
        if vendor is None or warehouse not in ("us", "china"):
            raise ValueError("choose a vendor and a warehouse")
        try:
            data = _read([i for i in items if i.kind == "text" or i.stored_file], recognizer)
        except readers.ReadError as exc:
            raise ValueError(str(exc)) from None
        _import(session, items, vendor, data, warehouse, list_date, now or naive_utcnow(), user_id)
        session.commit()
        return item


def reject_group(session: Session, item_id: int, user_id: int, now: datetime | None = None) -> IngestItem:
    with lock:
        item, items = _pending(session, item_id, ("needs_review", "failed"))
        _set(items, now or naive_utcnow(), status="rejected", decided_by=user_id)
        _drop_files(items)
        session.commit()
        return item


def undo_group(session: Session, item_id: int, user_id: int, now: datetime | None = None) -> IngestItem:
    with lock:
        item, items = _pending(session, item_id, ("imported",))
        plist = session.get(PriceList, item.price_list_id) if item.price_list_id is not None else None
        if plist is not None:
            session.delete(plist)
        _set(items, now or naive_utcnow(), status="undone", price_list_id=None, decided_by=user_id)
        session.commit()
        return item
