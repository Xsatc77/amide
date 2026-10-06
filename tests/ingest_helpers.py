"""Helpers for the price-list ingest tests: a cookie-less client (as the watcher would be), tokens, sources and tiny files."""

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.ingest import tokens
from app.main import app
from app.models import IngestSource, Vendor


def anon_client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def make_token(db, me) -> str:
    return tokens.create_token(db, me, "test")[1]


def bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


_DEFAULT = object()


def make_source(db, vendor=_DEFAULT, enabled=True, chat_id="-100123", title="Acme group", **kw) -> IngestSource:
    """A group mapped to a vendor (made on demand) unless `vendor=None` says it is unmapped."""
    if vendor is _DEFAULT:
        vendor = db.query(Vendor).filter_by(name="Acme Labs").first() or Vendor(name="Acme Labs")
        db.add(vendor)
        db.commit()
    source = IngestSource(platform="telegram", chat_id=chat_id, title=title, enabled=enabled,
                          vendor_id=vendor.id if vendor is not None else None, **kw)
    db.add(source)
    db.commit()
    return source


def pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


def _image(seed: int, fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (seed % 256, (seed * 7) % 256, 90)).save(buffer, fmt)
    return buffer.getvalue()


def png_bytes(seed: int = 0) -> bytes:
    return _image(seed, "PNG")


def jpeg_bytes(seed: int = 0) -> bytes:
    return _image(seed, "JPEG")


def xlsx_bytes(rows) -> bytes:
    from openpyxl import Workbook
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
