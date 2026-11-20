"""Hostile files: small, hand-built files that once made a parser raise something the route did not expect (a 500), spin, or balloon.

Each file here is a few hundred bytes; the findings came from a larger fuzzing run, and these are the cases it found."""

import gzip
import io
import json
import time
import zipfile

import pytest

from app import config, pdfsafe
from app.backup import archive
from app.backup.container import BackupError
from app.health import parse as health_parse
from app.ingest import readers, zipsafe
from app.measurements import pacer, renpho


def _zip(entries, compression=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as z:
        for name, data in entries:
            z.writestr(name, data)
    return out.getvalue()


# ---------------------------------------------------------------- CSV: a field longer than the csv module reads

LONG = "9" * 200_000


def test_a_scale_export_with_a_huge_value_is_refused_not_raised():
    with pytest.raises(renpho.ImportError_):
        renpho.parse(f"Date,Time,Weight(kg)\n2025.01.01,08:00:00,{LONG}\n")


def test_a_pacer_file_with_a_huge_value_is_refused_not_raised():
    with pytest.raises(pacer.ImportError_):
        pacer.parse_texts([f"Time,Weight(kg)\n2025-01-01 08:00:00,{LONG}\n"])
    with pytest.raises(pacer.ImportError_):
        pacer.parse_texts([f"Time,Systolic,Diastolic\n2025-01-01 08:00:00,{LONG},80\n"])


def test_the_scale_import_route_answers_422_for_a_huge_value(client):
    body = f"Date,Time,Weight(kg)\n2025.01.01,08:00:00,{LONG}\n".encode()
    r = client.post("/measurements/import-scale", files={"file": ("scale.csv", body, "text/csv")}, follow_redirects=False)
    assert r.status_code == 422


# ---------------------------------------------------------------- zips with a damaged entry

def test_a_pacer_zip_with_a_damaged_entry_is_refused_not_raised():
    good = _zip([("WeightLogs.csv", b"Time,Weight(kg)\n2025-01-01 08:00:00,80\n")], zipfile.ZIP_STORED)
    broken = good.replace(b"2025-01-01", b"2025-01-02")                 # the stored bytes no longer match the recorded CRC
    assert broken != good
    with pytest.raises(pacer.ImportError_):
        pacer.texts_from_zip(broken)


def test_zipsafe_turns_a_damaged_entry_into_zipunsafe():
    data = _zip([("a.xml", b"<a>hello hello hello</a>")], zipfile.ZIP_STORED).replace(b"hello hello", b"HELLO hello")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()
        with pytest.raises(zipsafe.ZipUnsafe):
            zipsafe.read_bounded(z, "a.xml", 1000)
        with pytest.raises(zipsafe.ZipUnsafe):
            zipsafe.refuse_entities(z, info, part_limit=1000, total_limit=1000)


def test_a_spreadsheet_declaring_thousands_of_sheets_is_refused_unread():
    sheets = b"".join(b'<sheet name="S%d" sheetId="%d" r:id="rId1"/>' % (i, i) for i in range(1, 3001))
    workbook = b'<workbook xmlns:r="x"><sheets>' + sheets + b"</sheets></workbook>"
    with pytest.raises(readers.ReadError, match="too many sheets"):
        readers.read_xlsx(_zip([("xl/workbook.xml", workbook)]))


# ---------------------------------------------------------------- the backup's inner zip

def _manifest_zip(manifest: bytes) -> bytes:
    return _zip([("manifest.json", manifest)])


@pytest.mark.parametrize("manifest", [b"[1,2]", b'"text"', b"5", b"null", b'{"format":1,"entries":[1,2]}', b'{"format":1,"entries":"x"}'])
def test_a_manifest_of_the_wrong_shape_is_a_damaged_backup(manifest):
    with pytest.raises(BackupError):
        archive.read_archive(_manifest_zip(manifest), max_bytes=1 << 20)


def test_a_backup_entry_with_a_corrupt_deflate_stream_is_a_damaged_backup():
    data = _zip([("manifest.json", json.dumps({"format": 1, "entries": {}}).encode() * 50)])
    start = data.index(b"PK\x03\x04") + 30 + len("manifest.json")
    broken = data[:start] + b"\xff\xff\xff\xff" + data[start + 4:]
    with pytest.raises(BackupError):
        archive.read_archive(broken, max_bytes=1 << 20)


def test_a_backup_zip_with_an_unsupported_compression_method_is_a_damaged_backup():
    data = bytearray(_zip([("manifest.json", b"{}")], zipfile.ZIP_STORED))
    for marker, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):    # method 99 in the local header and the directory
        data[data.index(marker) + offset] = 99
    with pytest.raises(BackupError):
        archive.read_archive(bytes(data), max_bytes=1 << 20)


# ---------------------------------------------------------------- the stored Health export (a gzip copy)

def _load_bytes(tmp_path, monkeypatch, blob: bytes):
    monkeypatch.setattr(config, "HEALTH_RAW_DIR", tmp_path)
    (tmp_path / "x.gz").write_bytes(blob)
    return health_parse._load(type("Raw", (), {"file_name": "x.gz"})())


def test_a_stored_export_reads(tmp_path, monkeypatch):
    assert _load_bytes(tmp_path, monkeypatch, gzip.compress(b'{"metrics": []}')) == {"metrics": []}


@pytest.mark.parametrize("blob", [gzip.compress(b"{}") * 3, gzip.compress(b"{}") + b"junk", gzip.compress(b"{}")[:-6] + b"\xff" * 6 + b"x"])
def test_a_stored_export_with_more_than_one_gzip_member_or_trailing_junk_is_bad_content(tmp_path, monkeypatch, blob):
    with pytest.raises(ValueError):
        _load_bytes(tmp_path, monkeypatch, blob)


def test_a_stored_export_with_a_corrupt_deflate_stream_is_bad_content_not_our_fault(tmp_path, monkeypatch):
    good = gzip.compress(b'{"metrics": []}' * 100)
    broken = good[:12] + b"\xff\xff\xff\xff" + good[16:]
    with pytest.raises(ValueError):
        _load_bytes(tmp_path, monkeypatch, broken)


def test_a_truncated_stored_export_is_bad_content(tmp_path, monkeypatch):
    with pytest.raises(EOFError):
        _load_bytes(tmp_path, monkeypatch, gzip.compress(b'{"metrics": []}' * 100)[:-12])


def test_a_stored_export_never_expands_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HEALTH_MAX_BYTES", 1000)
    monkeypatch.setattr(health_parse, "MAX_EXPANSION", 1)
    with pytest.raises(ValueError, match="expands"):
        _load_bytes(tmp_path, monkeypatch, gzip.compress(b" " * 5_000_000))


# ---------------------------------------------------------------- PDFs are read in a child process that can be stopped

TINY_PDF = b"\n".join([
    b"%PDF-1.4",
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj",
    b"4 0 obj<</Length 44>>stream",
    b"BT /F1 12 Tf 20 100 Td (Glucose 90) Tj ET",
    b"endstream",
    b"endobj",
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
    b"trailer<</Root 1 0 R/Size 6>>",
    b"%%EOF",
])


def test_a_pdf_reads_through_the_child_process():
    from app.labs import labcorp
    assert "Glucose 90" in labcorp.pdf_text(TINY_PDF)
    assert labcorp.pdf_page_count(TINY_PDF) == 1


def test_a_reader_that_runs_too_long_is_stopped():
    started = time.monotonic()
    with pytest.raises(pdfsafe.PdfUnreadable, match="too long"):
        pdfsafe.run("time:sleep", 60, timeout=1.5)
    assert time.monotonic() - started < 20


def test_a_reader_that_dies_is_reported_not_raised_as_a_crash():
    with pytest.raises(pdfsafe.PdfUnreadable):
        pdfsafe.run("os:_exit", 3)


def test_an_exception_in_the_child_is_raised_as_the_same_type():
    with pytest.raises(ValueError):
        pdfsafe.run("builtins:int", "not a number")


def test_a_pdf_that_is_not_a_pdf_is_an_error_the_routes_already_handle():
    from app.labs import reports
    with pytest.raises(Exception):
        reports.extract_text(b"%PDF-1.4 not really")


# ---------------------------------------------------------------- price-list text: a long run of digits must not take quadratic time

def test_a_huge_line_in_a_price_list_is_read_in_bounded_time():
    from app.library.price_lists import reader
    line = "9" * 300_000
    started = time.monotonic()
    reader.rows_from_lines([line, "Example Peptide 5mg*10vials $40"])
    reader.rows_from_shorthand([line + " $4"])
    reader.scan_notes([line])
    reader.unread_spec_lines([line], [])
    assert time.monotonic() - started < 5
    assert len(reader.rows_from_lines(["Example Peptide 5mg*10vials $40"])) == 1


# ---------------------------------------------------------------- recognition never gets a picture bigger than it can hold

def test_a_very_large_picture_is_shrunk_before_text_recognition(monkeypatch):
    np = pytest.importorskip("numpy")
    from PIL import Image
    from app.library.price_lists import ocr
    pytest.importorskip("rapidocr")
    seen = []

    class Result:
        txts = None
        boxes = None
    monkeypatch.setattr(ocr, "_engine", lambda array: seen.append(array.shape) or Result())
    assert ocr.recognize(Image.new("RGB", (9000, 20))) == []
    assert max(seen[0][:2]) == ocr.MAX_SIDE


# ---------------------------------------------------------------- the real upload routes answer with a refusal or a page, never a 500

def _png_with_size(width: int, height: int) -> bytes:
    import struct
    import zlib as z

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", z.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", z.compress(b"x")) + chunk(b"IEND", b""))


LOOPING_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[2 0 R]/Count 1>>endobj\n"
               b"trailer<</Root 1 0 R/Size 3>>\n%%EOF\n")


@pytest.mark.parametrize("body", [LOOPING_PDF, b"%PDF-1.4 truncated", b"%PDF-" + b"\x00" * 300])
def test_a_hostile_pdf_on_the_lab_import_and_workout_routes_is_not_a_server_error(client, body):
    r = client.post("/labs/import", data={"lab": "quest"}, files={"report": ("r.pdf", body, "application/pdf")})
    assert r.status_code < 500
    r = client.post("/workouts/upload", files={"pdf": ("plan.pdf", body, "application/pdf")}, follow_redirects=False)
    assert r.status_code < 500


@pytest.mark.parametrize("body", [_png_with_size(100_000, 100_000), _png_with_size(2**31 - 1, 2**31 - 1), _png_with_size(0, 0), b"\x89PNG\r\n\x1a\n" + b"\xff" * 40])
def test_a_hostile_picture_on_the_body_photo_route_is_a_422(client, body):
    r = client.post("/measurements/photos", data={"taken_on": "2026-03-01"}, files={"file": ("p.png", body, "image/png")})
    assert r.status_code == 422


def test_a_sealed_backup_whose_manifest_is_a_list_is_a_422_not_a_500(client):
    from app.backup.container import seal
    r = client.post("/backup/open", files={"file": ("x.amidebackup", seal(_manifest_zip(b"[1,2]"), "passphrase1", n=2**10), "application/octet-stream")},
                    data={"passphrase": "passphrase1"})
    assert r.status_code == 422


def test_hostile_files_posted_as_telegram_messages_are_accepted_for_review_not_crashed(client, db, me):
    from ingest_helpers import anon_client, bearer, make_source, make_token
    source = make_source(db, chat_id="-100777")
    secret = make_token(db, me)
    bad_xlsx = _zip([("xl/workbook.xml", b"<workbook>" + b'<sheet name="a"/>' * 3000 + b"</workbook>")])
    with anon_client() as c:
        files = [("files", ("a.xlsx", bad_xlsx, "application/octet-stream")), ("files", ("b.pdf", LOOPING_PDF, "application/octet-stream")),
                 ("files", ("c.png", _png_with_size(100_000, 100_000), "application/octet-stream"))]
        r = c.post("/api/ingest/messages", data={"chat_id": source.chat_id, "message_id": "77", "date": "2026-10-06T14:30:00+00:00"},
                   headers=bearer(secret), files=files)
    assert r.status_code == 200


def test_a_lab_report_with_a_huge_line_is_read_in_bounded_time():
    from app.labs import labcorp, reports
    line = "a" + " " * 150_000 + "5"
    started = time.monotonic()
    reports.parse_generic(line + "\nGlucose 90 mg/dL 70-99")
    reports.parse_quest(line)
    labcorp.parse_text(line)
    assert time.monotonic() - started < 5


def test_a_pacer_zip_of_an_unknown_zip_version_is_refused_not_raised():
    data = bytearray(_zip([("WeightLogs.csv", b"Time,Weight(kg)\n")], zipfile.ZIP_STORED))
    for marker, offset in ((b"PK\x01\x02", 6),):               # "version needed to extract" in the directory entry
        data[data.index(marker) + offset] = 255
    try:
        pacer.texts_from_zip(bytes(data))
    except pacer.ImportError_:
        pass                                                   # refused (the python in use may also simply read it)
