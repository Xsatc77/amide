import io

import pytest
from PIL import Image

from app import config
from app.ingest import readers
from app.library.price_lists.ocr import Word
from app.library.price_lists.rows import Spec
from ingest_helpers import xlsx_bytes


def w(text, cx, cy, width=120, height=24):
    return Word(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2, text)


def scan_words():
    words = [w("name", 150, 130), w("Product name", 450, 130), w("specification", 800, 130), w("prices", 1060, 130)]
    y = 180
    for code, name, spec, price in (("ZX5", "Zorvex", "5mg*10vials", "$50"), ("ZX10", "", "10mg*10vials", "$60"),
                                    ("QU5", "Quillamine", "5mg*10vials", "$55"), ("QU10", "", "10mg*10vials", "$75"),
                                    ("BK10", "Borealin", "10mg*10vials", "$95")):
        words += [w(code, 150, y), w(name, 450, y) if name else None, w(spec, 800, y, 160), w(price, 1060, y, 80)]
        y += 46
    return [x for x in words if x is not None]


def blank(size=(400, 300)):
    return Image.new("RGB", size, "white")


# ---------------------------------------------------------------- photos

def test_photos_are_read_through_the_recognizer_and_several_photos_are_one_list():
    data = readers.read_images([blank(), blank()], recognize=lambda image: scan_words())
    assert len(data.rows) == 10 and {r.name for r in data.rows if r.name} == {"Zorvex", "Quillamine", "Borealin"}
    assert [(r.code, r.spec, r.pack_price) for r in data.rows[:2]] == [("ZX5", Spec(5, "mg", 10), 50.0), ("ZX10", Spec(10, "mg", 10), 60.0)]


def test_a_photo_with_nothing_recognized_gives_no_rows_not_an_error():
    assert readers.read_images([blank()], recognize=lambda image: []).rows == []


def test_too_many_photos_or_a_giant_one_are_refused(monkeypatch):
    with pytest.raises(readers.ReadError, match="too many"):
        readers.read_images([blank((10, 10)) for _ in range(config.INGEST_MAX_IMAGES + 1)], recognize=lambda image: [])
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 1000)
    with pytest.raises(readers.ReadError, match="too large"):
        readers.read_images([blank((100, 100))], recognize=lambda image: [])


# ---------------------------------------------------------------- spreadsheets

def test_a_spreadsheet_with_a_header_row_is_read_with_names_filled_down():
    sheet = [["Code", "Product", "Spec", "Price"], ["ZX5", "Zorvex", "5mg*10vials", 50], ["ZX10", None, "10mg*10vials", 60],
             ["QU5", "Quillamine", "5mg*10vials", 55], ["QU10", None, "10mg*10vials", 75]]
    data = readers.read_xlsx(xlsx_bytes(sheet))
    assert [(r.code, r.spec, r.pack_price) for r in data.rows] == [
        ("ZX5", Spec(5, "mg", 10), 50.0), ("ZX10", Spec(10, "mg", 10), 60.0), ("QU5", Spec(5, "mg", 10), 55.0), ("QU10", Spec(10, "mg", 10), 75.0)]
    assert [r.name for r in data.rows] == ["Zorvex", "Zorvex", "Quillamine", "Quillamine"]


def test_a_warehouse_note_in_a_spreadsheet_is_a_hint():
    sheet = [["Acme Peptides"], ["US WAREHOUSE A"], ["Code", "Product", "Spec", "Price"], ["ZX5", "Zorvex", "5mg*10vials", 50],
             ["ZX10", "Zorvex", "10mg*10vials", 60], ["QU5", "Quillamine", "5mg*10vials", 55]]
    assert readers.read_xlsx(xlsx_bytes(sheet)).warehouse_hint == "us"


def test_several_sheets_are_all_read_and_a_non_spreadsheet_or_empty_one_is_an_error_or_empty():
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["Code", "Product", "Spec", "Price"])
    book.active.append(["ZX5", "Zorvex", "5mg*10vials", 50])
    book.active.append(["ZX10", "Zorvex", "10mg*10vials", 60])
    second = book.create_sheet("More")
    second.append(["Code", "Product", "Spec", "Price"])
    second.append(["QU5", "Quillamine", "5mg*10vials", 55])
    second.append(["QU10", "Quillamine", "10mg*10vials", 75])
    buffer = io.BytesIO()
    book.save(buffer)
    assert len(readers.read_xlsx(buffer.getvalue()).rows) == 4
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(b"PK" + bytes([3, 4]) + b"not really a workbook")
    assert readers.read_xlsx(xlsx_bytes([[None, None]])).rows == []


def test_a_huge_spreadsheet_is_cut_at_the_caps_without_failing():
    rows = [["Code", "Product", "Spec", "Price"]] + [[f"ZX{i}", f"Zorvex{i}", "10mg*10vials", 50] for i in range(6000)]
    assert len(readers.read_xlsx(xlsx_bytes(rows)).rows) <= 5000


# ---------------------------------------------------------------- typed text

def test_typed_text_lines_become_rows_and_chatter_is_ignored():
    text = "New prices today!\nSemaglutide SM10 10mg*10vials $21\nTirzepatide TR10 10mg*10vials $19\nthanks everyone"
    data = readers.read_text(text)
    assert [(r.code, r.name, r.pack_price) for r in data.rows] == [("SM10", "Semaglutide", 21.0), ("TR10", "Tirzepatide", 19.0)]
    assert readers.read_text("hello world, what is the shipping time?").rows == []


# ---------------------------------------------------------------- is it a price list

def data_with(priced, unpriced=0):
    rows = [readers.ParsedRow(code="ZX", name="Zorvex", spec=Spec(10, "mg", 10), pack_price=50.0) for _ in range(priced)]
    rows += [readers.ParsedRow(code="ZY", name="Quillamine", spec=Spec(10, "mg", 10), pack_price=None) for _ in range(unpriced)]
    return readers.PriceListData(rows=rows)


def test_enough_priced_rows_make_a_price_list():
    assert readers.looks_like_price_list(data_with(3), None, None) == (True, None)
    ok, reason = readers.looks_like_price_list(data_with(2), None, None)
    assert ok is False and "not a price list" in reason


def test_mostly_unpriced_rows_are_not_a_price_list_unless_the_name_says_so():
    assert readers.looks_like_price_list(data_with(2, 3), None, None)[0] is False
    assert readers.looks_like_price_list(data_with(1), "latest price list attached", None) == (True, None)
    assert readers.looks_like_price_list(data_with(1), None, "Acme_pricelist.pdf") == (True, None)
    assert readers.looks_like_price_list(data_with(0), "price list", None)[0] is False
    assert readers.priced_fraction(data_with(3, 1)) == 0.75 and readers.priced_fraction(data_with(0)) == 0.0


def test_a_photo_keeps_whichever_reading_found_more_prices():
    priced = [readers.ParsedRow(code="ZX", name="Zorvex", spec=Spec(10, "mg", 10), pack_price=50.0) for _ in range(3)]
    unpriced = [readers.ParsedRow(code=None, name=None, spec=Spec(10, "mg", 10), pack_price=None) for _ in range(5)]
    assert readers.prefer_priced(unpriced, priced) == priced          # a table guess with no prices loses to readable lines
    assert readers.prefer_priced(priced, unpriced) == priced
    assert readers.prefer_priced([], priced) == priced and readers.prefer_priced(priced, []) == priced and readers.prefer_priced([], []) == []


def test_a_photo_of_plain_price_lines_is_read_from_its_lines():
    def lines_only(image):
        return [w(text, 450, 130 + 46 * n, width=700) for n, text in enumerate(
            ["Zorvex ZX5 5mg*10vials $50", "Zorvex ZX10 10mg*10vials $60", "Quillamine QU5 5mg*10vials $55"])]
    data = readers.read_images([blank()], recognize=lines_only)
    assert [(r.code, r.pack_price) for r in data.rows] == [("ZX5", 50.0), ("ZX10", 60.0), ("QU5", 55.0)]
