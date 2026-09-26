"""Direct unit tests for app/uploads.py's price-list helpers (no HTTP route exists yet for
these -- Task 2 only adds the pure functions; a route consuming them lands in a later task).
"""

import asyncio
import io

import pytest
from starlette.datastructures import UploadFile

from app import config
from app.uploads import (
    ALLOWED_TYPES,
    PRICE_LIST_ALLOWED_TYPES,
    UploadError,
    delete_price_list,
    price_list_media_type,
    price_list_path,
    save_price_list,
)

PDF = b"%PDF-1.7\n" + b"\x00" * 32
DOCX = b"PK\x03\x04" + b"\x00" * 32
DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(data), filename=filename)


def _save(filename: str, data: bytes) -> str:
    return asyncio.run(save_price_list(_upload(filename, data)))


@pytest.fixture(autouse=True)
def _clean_price_list_dir():
    config.ensure_dirs()
    yield
    for f in config.PRICE_LIST_DIR.glob("*"):
        f.unlink()


def test_price_list_allowed_types_differ_from_coa_allowed_types():
    assert PRICE_LIST_ALLOWED_TYPES != ALLOWED_TYPES
    assert ".doc" in PRICE_LIST_ALLOWED_TYPES or ".docx" in PRICE_LIST_ALLOWED_TYPES


def test_save_price_list_accepts_pdf():
    filename = _save("price-list.pdf", PDF)
    assert filename.endswith(".pdf")
    assert price_list_path(filename).read_bytes() == PDF
    assert price_list_media_type(filename) == "application/pdf"
    delete_price_list(filename)
    assert not price_list_path(filename).exists()


def test_save_price_list_accepts_docx():
    filename = _save("price-list.docx", DOCX)
    assert filename.endswith(".docx")
    assert price_list_path(filename).read_bytes() == DOCX
    assert (
        price_list_media_type(filename)
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_save_price_list_accepts_doc():
    filename = _save("price-list.doc", DOC)
    assert filename.endswith(".doc")
    assert price_list_path(filename).read_bytes() == DOC
    assert price_list_media_type(filename) == "application/msword"


def test_save_price_list_rejects_exe():
    with pytest.raises(UploadError):
        _save("evil.exe", b"MZ" + b"\x00" * 32)


def test_save_price_list_rejects_content_mismatch():
    with pytest.raises(UploadError):
        _save("fake.pdf", b"<script>" + b"\x00" * 32)
    assert list(config.PRICE_LIST_DIR.iterdir()) == []


def test_save_price_list_rejects_oversized_file():
    oversized = PDF + b"\x00" * config.MAX_UPLOAD_BYTES
    with pytest.raises(UploadError):
        _save("big.pdf", oversized)
    assert list(config.PRICE_LIST_DIR.iterdir()) == []
