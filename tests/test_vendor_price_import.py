"""Saving a vendor with a price-list PDF reads the prices and imports them. Invented vendor and peptide names only."""

import html
from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.library.price_lists.importer import import_for_vendor
from app.library.price_lists.reader import PriceListData
from app.library.price_lists.rows import ParsedRow, Spec
from app.models import PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource
from price_helpers import make_card, make_vendor

PDF = b"%PDF-1.4 fake"


def row(code, name, price=50.0):
    return ParsedRow(code=code, name=name, spec=Spec(10, "mg", 10), pack_price=price)


def fake_reader(*rows, hint=None):
    return lambda path: PriceListData(rows=list(rows), warehouse_hint=hint)


@pytest.fixture
def vendor(client, db):
    v = make_vendor(db, "Acme Labs")
    make_card(db, "Zorvex")
    yield v


def post(client, vendor, *, file=("prices.pdf", PDF, "application/pdf"), **fields):
    data = {"name": vendor.name, **fields}
    files = {"price_list_file": file} if file else {"x": ("", b"")}
    return client.post(f"/vendors/{vendor.id}", data=data, files=files, follow_redirects=False)


def lists(vendor_id):
    with SessionLocal() as s:
        return s.scalars(select(PriceList).where(PriceList.vendor_id == vendor_id).order_by(PriceList.id)).all()


# ---------------------------------------------------------------- the importer entry point

def test_import_for_vendor_uses_the_given_vendor_warehouse_and_date(db, vendor):
    report = import_for_vendor(db, vendor, PriceListData(rows=[row("ZX10", "Zorvex")]),
                               warehouse="us", list_date=date(2026, 10, 6))
    plist = db.scalar(select(PriceList))
    assert (plist.vendor_id, plist.warehouse, plist.warehouse_source, plist.list_date) == (
        vendor.id, Warehouse.US, WarehouseSource.MANUAL, date(2026, 10, 6))
    assert report.list_id == plist.id and report.rows == 1 and report.matched == 1 and report.skipped is None


def test_importing_the_same_vendor_warehouse_and_date_twice_replaces_the_first(db, vendor):
    for price in (50.0, 55.0):
        import_for_vendor(db, vendor, PriceListData(rows=[row("ZX10", "Zorvex", price)]),
                          warehouse="china", list_date=date(2026, 10, 6))
    db.expire_all()
    assert db.query(PriceList).count() == 1
    assert db.scalar(select(PriceListItem.pack_price)) == 55.0


def test_a_different_warehouse_on_the_same_day_is_a_separate_list(db, vendor):
    for warehouse in ("china", "us"):
        import_for_vendor(db, vendor, PriceListData(rows=[row("ZX10", "Zorvex")]),
                          warehouse=warehouse, list_date=date(2026, 10, 6))
    assert db.query(PriceList).count() == 2


def test_a_list_with_no_rows_is_reported_and_stores_nothing(db, vendor):
    report = import_for_vendor(db, vendor, PriceListData(rows=[]), warehouse="china", list_date=date(2026, 10, 6))
    assert report.skipped and report.list_id is None and db.query(PriceList).count() == 0


# ---------------------------------------------------------------- through the vendor edit form

def test_a_pdf_saved_on_the_vendor_page_is_imported_with_the_chosen_warehouse_and_date(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex"), row("QQ5", "Unknownase")))
    r = post(client, vendor, price_list_warehouse="us", price_list_date="2026-10-06")
    assert r.status_code == 303
    (plist,) = lists(vendor.id)
    assert (plist.warehouse, plist.list_date) == (Warehouse.US, date(2026, 10, 6))
    with SessionLocal() as s:
        assert s.query(PriceListItem).filter_by(price_list_id=plist.id).count() == 2
    assert r.headers["location"] == f"/vendors/{vendor.id}?import={plist.id}"
    with SessionLocal() as s:
        assert s.get(Vendor, vendor.id).price_list_filename.endswith(".pdf")        # still attached as the reference copy


def test_the_page_after_an_import_says_what_was_read(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex"), row("QQ5", "Unknownase")))
    r = post(client, vendor, price_list_warehouse="us", price_list_date="2026-10-06")
    page = html.unescape(client.get(r.headers["location"]).text)
    assert "Prices imported" in page and "2 products" in page and "1 matched a library card" in page
    assert "Unknownase" in page and "US" in page and "10/06/2026" in page


def test_warehouse_defaults_to_china_and_date_to_today(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex")))
    post(client, vendor)
    (plist,) = lists(vendor.id)
    assert (plist.warehouse, plist.list_date) == (Warehouse.CHINA, date.today())


def test_a_second_import_makes_the_newer_list_current_and_keeps_the_old_one(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex", 50.0)))
    post(client, vendor, price_list_date="2026-09-01")
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex", 45.0)))
    post(client, vendor, price_list_date="2026-10-06")
    assert [p.list_date for p in lists(vendor.id)] == [date(2026, 9, 1), date(2026, 10, 6)]


def test_an_unreadable_pdf_is_still_attached_and_the_page_says_it_could_not_be_read(client, db, vendor, monkeypatch):
    def boom(path):
        raise ValueError("damaged")
    monkeypatch.setattr("app.routers.vendors.read_pdf", boom)
    r = post(client, vendor)
    assert r.status_code == 303 and lists(vendor.id) == []
    assert "could not be read" in html.unescape(client.get(r.headers["location"]).text)
    with SessionLocal() as s:
        assert s.get(Vendor, vendor.id).price_list_filename is not None


def test_a_pdf_with_no_price_rows_says_so(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader())
    r = post(client, vendor)
    assert lists(vendor.id) == []
    assert "no prices were found" in html.unescape(client.get(r.headers["location"]).text).lower()


def test_a_file_that_is_not_a_pdf_is_attached_but_not_read(client, db, vendor):
    r = post(client, vendor, file=("prices.docx", b"PK\x03\x04" + b"\0" * 32, "application/octet-stream"))
    assert r.status_code == 303 and lists(vendor.id) == []
    assert "only pdf" in html.unescape(client.get(r.headers["location"]).text).lower()


def test_saving_without_a_new_file_imports_nothing_and_shows_no_banner(client, db, vendor):
    r = post(client, vendor, file=None)
    assert r.status_code == 303 and r.headers["location"] == f"/vendors/{vendor.id}" and lists(vendor.id) == []


def test_a_bad_date_is_rejected_and_nothing_is_saved(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex")))
    r = post(client, vendor, price_list_date="not-a-date")
    assert r.status_code == 422 and lists(vendor.id) == []
    with SessionLocal() as s:
        assert s.get(Vendor, vendor.id).price_list_filename is None


def test_a_bad_warehouse_is_rejected(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex")))
    assert post(client, vendor, price_list_warehouse="mars").status_code == 422


def test_the_banner_never_shows_another_vendors_list(client, db, vendor, monkeypatch):
    other = make_vendor(db, "Borealis Bio")
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex")))
    r = post(client, vendor)
    list_id = lists(vendor.id)[0].id
    page = client.get(f"/vendors/{other.id}?import={list_id}")
    assert "Prices imported" not in page.text


def test_the_banner_does_not_count_a_missing_code_column_as_a_problem(client, db, vendor, monkeypatch):
    flagged = ParsedRow(code=None, name="Zorvex", spec=Spec(10, "mg", 10), pack_price=50.0, flags=["no-code"])
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(flagged))
    r = post(client, vendor)
    assert "flagged" not in html.unescape(client.get(r.headers["location"]).text)


def test_the_default_warehouse_follows_what_the_pdf_says(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex"), hint="us"))
    post(client, vendor)
    assert lists(vendor.id)[0].warehouse == Warehouse.US


def test_a_chosen_warehouse_beats_what_the_pdf_says(client, db, vendor, monkeypatch):
    monkeypatch.setattr("app.routers.vendors.read_pdf", fake_reader(row("ZX10", "Zorvex"), hint="us"))
    post(client, vendor, price_list_warehouse="china")
    assert lists(vendor.id)[0].warehouse == Warehouse.CHINA


def test_a_scan_on_a_server_without_ocr_says_so_and_stays_attached(client, db, vendor, monkeypatch):
    from app.library.price_lists.ocr import OcrUnavailable

    def unavailable(path):
        raise OcrUnavailable("not installed")
    monkeypatch.setattr("app.routers.vendors.read_pdf", unavailable)
    r = post(client, vendor)
    assert lists(vendor.id) == []
    assert "ocr is not installed" in html.unescape(client.get(r.headers["location"]).text).lower()
