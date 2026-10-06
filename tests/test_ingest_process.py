from datetime import date, datetime, timedelta

import pytest

from app import config
from app.db import SessionLocal
from app.ingest import infer, process
from app.models import IngestItem, PriceList, Vendor
from ingest_helpers import make_source, png_bytes
from test_ingest_readers import scan_words

NOW = datetime(2026, 10, 6, 15, 0)
LINES = ["Zorvex ZX5 5mg*10vials $50", "Zorvex ZX10 10mg*10vials $60", "Quillamine QU5 5mg*10vials $55",
         "Quillamine QU10 10mg*10vials $75", "Borealin BK10 10mg*10vials $95", "Borealin BK20 20mg*10vials $150"]


def text_item(db, source, text="\n".join(LINES), *, message_id="1", received=NOW, caption_extra="", **kw):
    row = dict(source_id=source.id, message_id=message_id, group_key=f"{source.id}:m:{message_id}", received_at=received, kind="text",
               file_hash=f"{message_id:0>64}", caption=(caption_extra + "\n" + text).strip(), status="received", created_at=received)
    item = IngestItem(**{**row, **kw})
    db.add(item)
    db.commit()
    return item


def run(now=NOW, **kw):
    return process.process_due(SessionLocal, now, **kw)


@pytest.fixture
def vendor(db):
    v = Vendor(name="Acme Labs")
    db.add(v)
    db.commit()
    return v


# ---------------------------------------------------------------- inference

@pytest.mark.parametrize("hint,caption,filename,default,expected", [
    ("us", "china", None, None, ("us", False)),
    (None, "New USA list", None, None, ("us", False)),
    (None, None, "acme_china_oct.pdf", "us", ("china", False)),
    (None, "prices", "list.pdf", "us", ("us", False)),
    (None, "prices", "list.pdf", None, ("china", True)),
    (None, "Chinese warehouse restock", None, None, ("china", False)),
    (None, "focus on results", None, None, ("china", True)),
])
def test_the_warehouse_comes_from_the_list_then_the_words_then_the_group_then_china(hint, caption, filename, default, expected):
    assert infer.infer_warehouse(hint, caption, filename, default) == expected


@pytest.mark.parametrize("texts,expected", [
    (["prices 2026-10-03"], date(2026, 10, 3)),
    (["updated 10/02/2026"], date(2026, 10, 2)),
    (["Oct 4 prices"], date(2026, 10, 4)),
    (["list 10.05"], date(2026, 10, 5)),
    (["dated 2025-01-01"], date(2026, 10, 6)),
    (["no date here"], date(2026, 10, 6)),
    ([], date(2026, 10, 6)),
])
def test_the_date_is_read_from_the_text_when_close_to_the_message_else_the_message_date(texts, expected):
    assert infer.infer_date(texts, date(2026, 10, 6)) == expected


# ---------------------------------------------------------------- the decision

def test_a_confident_list_from_an_enabled_mapped_group_is_imported_and_logged(db, vendor):
    source = make_source(db, vendor=vendor)
    item = text_item(db, source, caption_extra="USA warehouse prices")
    assert run() == 1
    db.expire_all()
    item = db.get(IngestItem, item.id)
    plist = db.query(PriceList).one()
    assert (item.status, item.vendor_id, item.warehouse, item.list_date, item.price_list_id, item.rows_found) == (
        "imported", vendor.id, "us", date(2026, 10, 6), plist.id, 6)
    assert plist.warehouse.value == "us" and item.decided_by is None and item.decided_at == NOW


def test_an_assumed_warehouse_goes_to_the_inbox_not_the_data(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "needs_review" and "warehouse" in item.reason and item.warehouse == "china"
    assert db.query(PriceList).count() == 0


def test_the_groups_default_warehouse_makes_it_confident(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor, default_warehouse="china"))
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "imported"


def test_a_disabled_or_unmapped_group_never_imports_by_itself(db, vendor):
    disabled = make_source(db, vendor=vendor, enabled=False, chat_id="-2", default_warehouse="us")
    unmapped = make_source(db, vendor=None, chat_id="-3", default_warehouse="us")
    a = text_item(db, disabled, message_id="1")
    b = text_item(db, unmapped, message_id="2")
    run()
    db.expire_all()
    assert db.get(IngestItem, a.id).status == "needs_review" and "enabled" in db.get(IngestItem, a.id).reason
    assert db.get(IngestItem, b.id).status == "needs_review" and "vendor" in db.get(IngestItem, b.id).reason
    assert db.query(PriceList).count() == 0


def test_chatter_is_ignored(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor), text="Hello all, shipping is delayed this week")
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "ignored" and "not a price list" in item.reason


def test_a_small_list_is_reviewed_because_it_has_fewer_than_five_rows(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor, default_warehouse="us"), text="\n".join(LINES[:4]) + "\nprice list")
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "needs_review"


def test_a_list_older_than_the_current_one_is_reviewed(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="dated 2026-10-05")
    run()
    older = text_item(db, source, message_id="2", caption_extra="dated 2026-10-01", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    db.expire_all()
    assert db.get(IngestItem, older.id).status == "needs_review" and "older" in db.get(IngestItem, older.id).reason


def test_a_list_whose_prices_are_wildly_different_is_reviewed(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    pricey = [line.rsplit("$", 1)[0] + "$" + str(int(line.rsplit("$", 1)[1]) * 5) for line in LINES]
    item = text_item(db, source, "\n".join(pricey), message_id="2", caption_extra="2026-10-05")
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "needs_review" and "prices" in db.get(IngestItem, item.id).reason
    assert db.query(PriceList).count() == 1


def test_the_same_content_in_a_new_message_is_a_duplicate(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    first = text_item(db, source, message_id="1")
    run()
    again = text_item(db, source, message_id="2", file_hash=first.file_hash)
    run()
    db.expire_all()
    assert db.get(IngestItem, again.id).status == "duplicate" and db.query(PriceList).count() == 1


def test_a_reader_failure_marks_the_item_failed_and_the_rest_carry_on(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    broken = IngestItem(source_id=source.id, message_id="5", group_key="g5", received_at=NOW, kind="xlsx", file_hash="5" * 64,
                        stored_file="missing-file", status="received", created_at=NOW)
    db.add(broken)
    db.commit()
    good = text_item(db, source, message_id="6")
    assert run() == 2
    db.expire_all()
    assert db.get(IngestItem, broken.id).status == "failed" and db.get(IngestItem, good.id).status == "imported"


# ---------------------------------------------------------------- photos settle before they are read

def photo_item(db, source, n, created, album="A"):
    stored = f"photo{n}"
    (config.INGEST_DIR / stored).write_bytes(png_bytes(n))
    item = IngestItem(source_id=source.id, message_id=str(n), album_id=album, group_key=f"{source.id}:a:{album}", received_at=created,
                      kind="image", file_hash=f"{n:0>64}", stored_file=stored, status="received", created_at=created)
    db.add(item)
    db.commit()
    return item


def test_photos_wait_60_seconds_after_the_last_one_then_are_read_as_one_list(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    a = photo_item(db, source, 1, NOW)
    b = photo_item(db, source, 2, NOW + timedelta(seconds=30))
    recognizer = lambda image: scan_words()
    assert run(NOW + timedelta(seconds=75), recognizer=recognizer) == 0
    assert run(NOW + timedelta(seconds=91), recognizer=recognizer) == 1
    db.expire_all()
    assert {db.get(IngestItem, a.id).status, db.get(IngestItem, b.id).status} == {"imported"}
    assert db.get(IngestItem, a.id).price_list_id == db.get(IngestItem, b.id).price_list_id and db.query(PriceList).count() == 1


# ---------------------------------------------------------------- approve, reject, undo

def test_approving_imports_with_the_edited_values_and_undo_removes_the_list(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    other = Vendor(name="Zephyr Labs")
    db.add(other)
    db.commit()
    done = process.approve_group(db, item.id, vendor_id=other.id, warehouse="us", list_date=date(2026, 10, 4), user_id=me)
    plist = db.query(PriceList).one()
    assert (done.status, done.vendor_id, done.warehouse, done.list_date, done.decided_by, plist.vendor_id) == (
        "imported", other.id, "us", date(2026, 10, 4), me, other.id)
    undone = process.undo_group(db, item.id, me)
    assert undone.status == "undone" and db.query(PriceList).count() == 0


def test_undo_restores_the_previous_current_list(db, vendor, me):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    second = text_item(db, source, message_id="2", caption_extra="2026-10-05", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    assert db.query(PriceList).count() == 2
    process.undo_group(db, second.id, me)
    from app.library.price_lists.analysis import current_lists
    assert [p.list_date for p in current_lists(db)] == [date(2026, 10, 1)]


def test_reject_is_final_and_approving_or_undoing_the_wrong_state_is_refused(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    assert process.reject_group(db, item.id, me).status == "rejected"
    with pytest.raises(ValueError):
        process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    with pytest.raises(ValueError):
        process.undo_group(db, item.id, me)


def test_approving_twice_never_imports_twice(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    with pytest.raises(ValueError):
        process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    assert db.query(PriceList).count() == 1


def test_two_warehouses_of_one_vendor_on_one_day_are_two_lists(db, vendor):
    source = make_source(db, vendor=vendor)
    text_item(db, source, message_id="1", caption_extra="USA warehouse")
    text_item(db, source, message_id="2", caption_extra="China warehouse", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    assert sorted(p.warehouse.value for p in db.query(PriceList)) == ["china", "us"]


# ---------------------------------------------------------------- review fixes

def test_a_late_photo_of_an_already_processed_list_goes_to_review_and_never_replaces_it(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    first = photo_item(db, source, 1, NOW)
    recognizer = lambda image: scan_words()
    run(NOW + timedelta(seconds=90), recognizer=recognizer)
    db.expire_all()
    assert db.get(IngestItem, first.id).status == "imported"
    held = db.query(PriceList).one().id
    late = photo_item(db, source, 2, NOW + timedelta(seconds=100))
    run(NOW + timedelta(seconds=200), recognizer=lambda image: scan_words()[:12])
    db.expire_all()
    assert db.get(IngestItem, late.id).status == "needs_review" and "late" in db.get(IngestItem, late.id).reason
    assert db.query(PriceList).one().id == held


def test_a_different_list_for_the_same_vendor_warehouse_and_date_is_reviewed_not_replaced(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-05")
    run()
    other = text_item(db, source, message_id="2", caption_extra="2026-10-05",
                      text="\n".join(f"Merrow MR{n} {n}mg*10vials ${n * 9}" for n in range(1, 8)))
    run()
    db.expire_all()
    assert db.get(IngestItem, other.id).status == "needs_review" and "already" in db.get(IngestItem, other.id).reason
    assert db.query(PriceList).count() == 1


def test_a_list_that_shares_nothing_with_the_current_one_is_reviewed(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    other = text_item(db, source, message_id="2", caption_extra="2026-10-05",
                      text="\n".join(f"Merrow MR{n} {n}mg*10vials ${n * 9}" for n in range(1, 8)))
    run()
    db.expire_all()
    assert db.get(IngestItem, other.id).status == "needs_review" and "compare" in db.get(IngestItem, other.id).reason


def test_the_same_content_in_another_file_or_on_a_later_day_is_a_duplicate(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    again = text_item(db, source, message_id="2", caption_extra="2026-10-05 re-posted")
    run()
    db.expire_all()
    assert db.get(IngestItem, again.id).status == "duplicate" and db.query(PriceList).count() == 1


@pytest.mark.parametrize("caption,expected", [
    ("New list, message us to order", ("china", True)),
    ("US warehouse", ("us", False)),
    ("shipping from the USA", ("us", False)),
])
def test_the_english_word_us_is_not_a_warehouse(caption, expected):
    assert infer.infer_warehouse(None, caption, None, None) == expected


@pytest.mark.parametrize("text,received", [("Retatrutide 10.5mg", date(2026, 9, 25)), ("restock 7.5 ml", date(2026, 6, 25)),
                                           ("dated 2026-10-20", date(2026, 10, 6))])
def test_doses_and_future_dates_are_not_list_dates(text, received):
    assert infer.infer_date([text], received) == received


def test_a_photo_is_size_checked_before_it_is_decoded_to_pixels(db, vendor, monkeypatch):
    from PIL import Image
    source = make_source(db, vendor=vendor, default_warehouse="us")
    item = photo_item(db, source, 1, NOW)
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 10)
    monkeypatch.setattr(Image.Image, "convert", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded before the size check")))
    run(NOW + timedelta(seconds=90), recognizer=lambda image: [])
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "failed" and "too large" in db.get(IngestItem, item.id).reason


def test_a_spreadsheet_that_expands_hugely_is_refused():
    import io, zipfile
    from app.ingest import readers
    from ingest_helpers import xlsx_bytes
    buffer = io.BytesIO(xlsx_bytes([["a"]]))
    with zipfile.ZipFile(buffer, "a") as z:
        z.writestr("xl/sharedStrings.xml", b"x" * (readers.MAX_UNCOMPRESSED + 1))
    with pytest.raises(readers.ReadError, match="too large"):
        readers.read_xlsx(buffer.getvalue())


def test_the_text_of_an_ignored_message_is_not_kept(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor), text="Hello all, what is the shipping time to Texas?")
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "ignored" and item.caption is None
