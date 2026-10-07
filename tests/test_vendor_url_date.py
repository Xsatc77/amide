"""Editing a vendor's other fields must not make a URL price list look freshly verified."""

from datetime import date

import pytest

from app.db import SessionLocal
from app.models import Vendor

OLD = date(2026, 1, 15)


@pytest.fixture
def vendor_id(client, db):
    with SessionLocal() as s:
        v = Vendor(name="Datecheck Vendor", price_list_url="https://example.org/prices", price_list_updated_at=OLD)
        s.add(v)
        s.commit()
        vid = v.id
    yield vid
    with SessionLocal() as s:
        s.query(Vendor).filter_by(id=vid).delete()
        s.commit()


def save(client, vid, **fields):
    data = {"name": "Datecheck Vendor", "price_list_url": "https://example.org/prices", "price_list_warehouse": "auto"} | fields
    return client.post(f"/vendors/{vid}", data=data, follow_redirects=False)


def updated(vid):
    with SessionLocal() as s:
        return s.get(Vendor, vid).price_list_updated_at


def test_changing_only_the_notes_leaves_the_price_list_date_alone(client, vendor_id):
    assert save(client, vendor_id, notes="a new note").status_code == 303
    assert updated(vendor_id) == OLD


def test_a_different_link_counts_as_a_new_price_list(client, vendor_id):
    save(client, vendor_id, price_list_url="https://example.org/prices-2")
    assert updated(vendor_id) == date.today()


def test_replacing_a_file_with_the_same_text_link_is_a_change(client, vendor_id):
    with SessionLocal() as s:
        v = s.get(Vendor, vendor_id)
        v.price_list_url, v.price_list_filename = None, "somefile.pdf"
        s.commit()
    save(client, vendor_id, price_list_url="https://example.org/prices")
    assert updated(vendor_id) == date.today()
