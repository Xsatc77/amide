# tests/test_price_list_importer.py
from datetime import date

from sqlalchemy import func, select

from app.models import (
    Peptide, PeptideSource, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)


def make_list(db, vendor=None, filename="Acme - Price List - 2026-09-01.pdf"):
    plist = PriceList(vendor_name="Acme", vendor_id=vendor.id if vendor else None, list_date=date(2026, 9, 1),
                      source_filename=filename, warehouse=Warehouse.CHINA, warehouse_source=WarehouseSource.ASSUMED)
    plist.items.append(PriceListItem(code="ZX10", product_name="Zorvex", vial_amount=10, vial_unit="mg",
                                     pack_size=10, pack_price=50.0, pack_type="kit", extra_prices={"10kits+": 45.0},
                                     flags=["code-size-mismatch"]))
    db.add(plist)
    db.commit()
    return plist


def test_price_list_round_trips_with_json_columns(db):
    make_list(db)
    db.expire_all()
    item = db.scalar(select(PriceListItem))
    assert (item.vial_amount, item.pack_size, item.pack_price, item.pack_type) == (10, 10, 50.0, "kit")
    assert item.extra_prices == {"10kits+": 45.0} and item.flags == ["code-size-mismatch"]
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source, plist.currency) == (Warehouse.CHINA, WarehouseSource.ASSUMED, "USD")
    assert plist.imported_at is not None


def test_deleting_a_list_deletes_its_items(db):
    plist = make_list(db)
    db.delete(plist)
    db.commit()
    assert db.scalar(select(func.count()).select_from(PriceListItem)) == 0


def test_deleting_the_vendor_leaves_the_list_with_its_name(db):
    vendor = Vendor(name="Acme")
    db.add(vendor)
    db.commit()
    make_list(db, vendor)
    db.delete(vendor)
    db.commit()
    db.expire_all()
    plist = db.scalar(select(PriceList))
    assert plist.vendor_id is None and plist.vendor_name == "Acme"


def test_deleting_a_matched_card_keeps_the_item(db):
    card = Peptide(name="Zorvex Card", source=PeptideSource.CUSTOM)
    db.add(card)
    db.commit()
    plist = make_list(db)
    plist.items[0].peptide_id = card.id
    db.commit()
    db.delete(card)
    db.commit()
    db.expire_all()
    assert db.scalar(select(PriceListItem)).peptide_id is None


def test_source_filename_is_unique(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    make_list(db)
    with pytest.raises(IntegrityError):
        make_list(db)
    db.rollback()
