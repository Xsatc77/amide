"""Scanned (picture) price lists: words with positions are rebuilt into a table and read by the normal reader.
Invented vendors and products only; the recognizer is faked except in the one test that runs the real engine."""

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.library.price_lists import ocr
from app.library.price_lists.ocr import Word, lines_from_words, table_from_words
from app.library.price_lists.reader import read_pdf
from app.library.price_lists.rows import Spec


def w(text, cx, cy, width=120, height=24):
    return Word(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2, text)


def scan(*groups):
    """groups: (code, name, [(spec, price), ...]) laid out like a scanned table: the product name is centered
    vertically across its group's rows. Columns at x = 150 (code), 450 (name), 800 (spec), 1060 (price)."""
    words = [w("Price list", 620, 40, 300), w("(The Zorvexes, Quillia)", 620, 80, 400),
             w("name", 150, 130), w("Product name", 450, 130), w("specification", 800, 130), w("prices", 1060, 130)]
    y = 180
    for codes, name, lines in groups:
        top = y
        for code, (spec, price) in zip(codes, lines):
            words += [w(code, 150, y), w(spec, 800, y, 160), w(price, 1060, y, 80)]
            y += 46
        words.append(w(name, 450, (top + y - 46) / 2))
    words.append(w("Oil-based drug specifications: Concentration ##mg per milliliter", 500, y + 40, 800))
    return words


GROUPS = [
    (["ZX5", "ZX10", "ZX20"], "Zorvex", [("5mg*10vials", "$50"), ("10mg*10vials", "$60"), ("20mg*10vials", "$90")]),
    (["QU5", "QU10"], "Quillamine", [("5mg*10vials", "$55"), ("10mg*10vials", "$75")]),
    (["BK10"], "Borealin 10mg", [("10mg*10vials", "$95")]),
]


def test_a_scanned_table_is_rebuilt_with_merged_names_and_a_header():
    table = table_from_words(scan(*GROUPS))
    assert table[0] == ["Code", "Name", "Specification", "Price"]
    assert [row[0] for row in table[1:]] == ["ZX5", "ZX10", "ZX20", "QU5", "QU10", "BK10"]
    assert [row[2:] for row in table[1:3]] == [["5mg*10vials", "$50"], ["10mg*10vials", "$60"]]
    assert [row[1] for row in table[1:]].count("") == 3 and "Zorvex" in [row[1] for row in table[1:]]


def test_notes_above_and_below_the_table_are_not_rows():
    table = table_from_words(scan(*GROUPS))
    assert len(table) == 1 + 6


def test_a_two_line_product_name_is_joined():
    words = scan(GROUPS[2])
    words = [x for x in words if x.text != "Borealin 10mg"] + [w("Borealin", 450, 170), w("10mg", 450, 196)]
    table = table_from_words(words + [w("ZX5", 150, 226), w("5mg*10vials", 800, 226, 160), w("$50", 1060, 226, 80)])
    assert any("Borealin" in row[1] and "10mg" in row[1] for row in table[1:])


def test_text_with_fewer_than_two_priced_lines_is_not_a_table():
    assert table_from_words([w("Hello", 100, 100), w("5mg*10vials", 800, 150, 160)]) is None


def test_lines_are_words_grouped_by_height_and_ordered_left_to_right():
    lines = lines_from_words([w("$50", 1060, 100), w("ZX5", 150, 102), w("5mg*10vials", 800, 98, 160), w("Next", 150, 200)])
    assert lines == ["ZX5 5mg*10vials $50", "Next"]


# ---------------------------------------------------------------- through read_pdf

def picture_pdf(tmp_path, pages=1):
    image = Image.new("RGB", (400, 300), "white")
    path = tmp_path / "scan.pdf"
    image.save(path, "PDF", save_all=True, append_images=[image.copy() for _ in range(pages - 1)])
    return path


def test_a_picture_only_pdf_is_read_through_the_recognizer(tmp_path):
    rows = read_pdf(picture_pdf(tmp_path), recognize=lambda page_image: scan(*GROUPS)).rows
    assert [(r.code, r.spec, r.pack_price) for r in rows][:3] == [
        ("ZX5", Spec(5, "mg", 10), 50.0), ("ZX10", Spec(10, "mg", 10), 60.0), ("ZX20", Spec(20, "mg", 10), 90.0)]
    assert len(rows) == 6 and {r.name for r in rows if r.name} == {"Zorvex", "Quillamine", "Borealin 10mg"}


def test_every_page_of_a_picture_pdf_is_read(tmp_path):
    assert len(read_pdf(picture_pdf(tmp_path, pages=2), recognize=lambda image: scan(*GROUPS)).rows) == 12


def test_nothing_recognized_gives_an_empty_list_not_an_error(tmp_path):
    assert read_pdf(picture_pdf(tmp_path), recognize=lambda image: []).rows == []


def test_a_missing_ocr_engine_is_a_clear_error(tmp_path, monkeypatch):
    def unavailable(image):
        raise ocr.OcrUnavailable("not installed")
    with pytest.raises(ocr.OcrUnavailable):
        read_pdf(picture_pdf(tmp_path), recognize=unavailable)


def test_a_text_pdf_never_calls_the_recognizer(tmp_path):
    def boom(image):
        raise AssertionError("OCR should not run on a page that has text")
    body = b"BT /F1 12 Tf 72 700 Td (Hello price list) Tj ET"
    objects = [b"%PDF-1.4",
               b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj",
               b"4 0 obj<</Length " + str(len(body)).encode() + b">>stream",
               body,
               b"endstream endobj",
               b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
               b"trailer<</Root 1 0 R/Size 6>>",
               b"%%EOF"]
    path = tmp_path / "text.pdf"
    path.write_bytes(bytes([10]).join(objects))
    assert read_pdf(path, recognize=boom).rows == []


# ---------------------------------------------------------------- the real engine

@pytest.mark.skipif(not ocr.engine_available(), reason="RapidOCR is not installed")
def test_the_real_engine_reads_a_rendered_table(tmp_path):
    image = Image.new("RGB", (1300, 520), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    for y, (code, name, spec, price) in zip((60, 130, 200, 270), [
            ("ZX5", "Zorvex", "5mg*10vials", "$50"), ("ZX10", "", "10mg*10vials", "$60"),
            ("QU5", "Quillamine", "5mg*10vials", "$55"), ("QU10", "", "10mg*10vials", "$75")]):
        for x, text in ((60, code), (360, name), (760, spec), (1100, price)):
            draw.text((x, y), text, fill="black", font=font)
    path = tmp_path / "rendered.pdf"
    image.save(path, "PDF")
    rows = read_pdf(path).rows
    assert [(r.spec, r.pack_price) for r in rows] == [
        (Spec(5, "mg", 10), 50.0), (Spec(10, "mg", 10), 60.0), (Spec(5, "mg", 10), 55.0), (Spec(10, "mg", 10), 75.0)]


def test_a_misread_multiplication_sign_and_a_trailing_dollar_sign_are_repaired():
    words = [w("ZX5", 150, 180), w("5mg+10vials", 800, 180, 160), w("88$", 1060, 180, 80),
             w("ZX10", 150, 226), w("250mg/ml.1vials", 800, 226, 160), w("$30/1vial", 1060, 226, 100),
             w("Zorvex", 450, 203)]
    table = table_from_words(words)
    assert [row[2:] for row in table[1:]] == [["5mg*10vials", "$88"], ["250mg/ml*1vials", "$30/1vial"]]


def test_names_in_consecutive_single_row_cells_stay_on_their_own_rows():
    words = []
    for i, (code, name) in enumerate([("ZX5", "Zorvex"), ("QU5", "Quillamine"), ("BK5", "Borealin")]):
        y = 180 + i * 46
        words += [w(code, 150, y), w(name, 450, y), w("5mg*10vials", 800, y, 160), w("$50", 1060, y, 80)]
    table = table_from_words(words)
    assert [row[1] for row in table[1:]] == ["Zorvex", "Quillamine", "Borealin"]


def test_a_wrapped_name_inside_one_tall_row_is_joined():
    words = [w("ZX5", 150, 180), w("5mg*10vials", 800, 180, 160), w("$50", 1060, 180, 80),
             w("BK10", 150, 280), w("Borealin 10mg+", 450, 255), w("Quillamine 10mg", 450, 280), w("10mg*10vials", 800, 280, 160),
             w("$99", 1060, 280, 80), w("ZX10", 150, 380), w("10mg*10vials", 800, 380, 160), w("$60", 1060, 380, 80)]
    table = table_from_words(words)
    assert table[2][1] == "Borealin 10mg+ Quillamine 10mg"
