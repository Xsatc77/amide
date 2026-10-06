from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Peptide, PeptideSource, PriceAlertIgnore, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)

# Invented vendors (Acme, Zephyr, Borealis) and peptides (Zorvex, Quillamine) only.


def make_vendor(db, name):
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor


def make_card(db, name, aliases=None):
    card = Peptide(name=name, aliases=aliases, source=PeptideSource.CUSTOM)
    db.add(card)
    db.commit()
    return card


def item(name, amount, price, *, unit="mg", pack=10, card=None, code="ZX"):
    pack_type = "kit" if pack == 10 else "box" if pack and pack < 10 else None
    return PriceListItem(code=code, product_name=name, peptide_id=card.id if card else None, vial_amount=amount,
                         vial_unit=unit, pack_size=pack, pack_price=price, pack_type=pack_type)


def make_list(db, vendor, when, *items, warehouse=Warehouse.CHINA):
    plist = PriceList(vendor_name=vendor.name, vendor_id=vendor.id, warehouse=warehouse,
                      warehouse_source=WarehouseSource.FILENAME, list_date=when,
                      source_filename=f"{vendor.name} - {warehouse.value} - {when.isoformat()}.pdf")
    plist.items.extend(items)
    db.add(plist)
    db.commit()
    return plist


# ---------------------------------------------------------------- ignore table

def test_an_ignored_product_round_trips_and_its_key_is_unique(db):
    db.add(PriceAlertIgnore(product_key="zorvex", product_name="Zorvex"))
    db.commit()
    assert db.query(PriceAlertIgnore).one().product_name == "Zorvex"
    db.add(PriceAlertIgnore(product_key="zorvex", product_name="Zorvex again"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
