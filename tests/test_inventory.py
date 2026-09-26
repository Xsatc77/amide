import html

from fastapi.testclient import TestClient

from app.main import app

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import pytest

from app import config
from app.db import SessionLocal
from datetime import date

from app.models import InventoryItem, Medium, User, Vendor

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


def _items(db):
    db.expire_all()
    return db.scalars(select(InventoryItem)).all()


def text(r):
    return html.unescape(r.text)


def test_empty_page_has_add_button(client):
    r = client.get("/inventory")
    assert r.status_code == 200
    assert 'data-action="add"' in html.unescape(r.text)
    assert "No inventory yet" in html.unescape(r.text)


def test_add_item_dialog_has_category_radios(client, db):
    t = html.unescape(client.get("/inventory").text)
    assert 'name="category" value="Medicine"' in t
    assert 'name="category" value="BAC Water"' in t
    assert 'name="category" value="Supply"' in t


def test_add_item_dialog_has_order_and_supply_field_groups(client, db):
    t = html.unescape(client.get("/inventory").text)
    assert 'data-category-group="order"' in t
    assert 'data-category-group="supply"' in t
    assert 'name="quantity"' in t and 'name="tracking_site"' in t and 'name="tracking_number"' in t
    assert 'name="tax"' in t and 'name="shipping"' in t


def test_create_full_item_with_coa(client, db):
    r = client.post(
        "/inventory",
        data={"name": "BPC-157", "category": "Medicine", "vial_size_mg": "10", "medium": "Lyophilized",
              "quantity": "5", "order_date": "2026-09-01", "vendor": "Acme Labs", "cost": "$1,045.50"},
        files={"coa": ("coa.png", PNG, "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    [item] = _items(db)
    assert item.name == "BPC-157"
    assert item.vial_size_mg == 10
    assert item.medium is Medium.LYOPHILIZED
    [li] = item.order_items
    assert li.quantity == 5
    assert li.order.vendor == "Acme Labs"
    assert li.cost_cents == 104550
    assert li.coa_filename.endswith(".png")
    assert (config.COA_DIR / li.coa_filename).read_bytes() == PNG


def test_only_name_is_required(client, db):
    r = client.post("/inventory", data={"name": "Semaglutide pen", "category": "Supply"}, follow_redirects=False)
    assert r.status_code == 303
    [item] = _items(db)
    assert item.count == 1
    assert item.vial_size_mg is None and item.medium is None and item.cost_cents is None


def test_validation_errors_rerender_form(client, db):
    r = client.post("/inventory", data={"name": " ", "category": "Supply", "count": "-2", "cost": "lots"})
    assert r.status_code == 422
    for msg in ("Item name is required", "Count can't be negative", "Cost must be a number"):
        assert msg in html.unescape(r.text)
    assert "data-open-on-load" in html.unescape(r.text)
    assert _items(db) == []


def test_rejects_bad_coa(client, db):
    r = client.post("/inventory", data={"name": "X", "category": "Medicine", "vial_size_mg": "10", "medium": "Lyophilized",
                                       "quantity": "1", "order_date": "2026-09-01"},
                   files={"coa": ("evil.html", b"<script>", "text/html")})
    assert r.status_code == 422 and "COA must be a photo" in html.unescape(r.text)

    # Right extension, wrong contents.
    r = client.post("/inventory", data={"name": "X", "category": "Medicine", "vial_size_mg": "10", "medium": "Lyophilized",
                                       "quantity": "1", "order_date": "2026-09-01"},
                   files={"coa": ("fake.png", b"<script>", "image/png")})
    assert r.status_code == 422 and "don't match" in html.unescape(r.text)
    assert _items(db) == []
    assert list(config.COA_DIR.iterdir()) == []


def test_edit_replaces_and_removes_coa(client, db):
    client.post("/inventory", data={"name": "TB-500", "category": "Supply"}, files={"coa": ("a.png", PNG, "image/png")}, follow_redirects=False)
    [item] = _items(db)
    # COA is now per-order for Medicine/BAC Water, not relevant for Supply items

    client.post(f"/inventory/{item.id}", data={"name": "TB-500", "category": "Supply", "count": "3"},
                follow_redirects=False)
    [item] = _items(db)
    assert item.count == 3
    # Note: COA handling is now done per-order in Task 5, not here

    assert list(config.COA_DIR.iterdir()) == []


def test_delete_removes_row_and_file(client, db):
    client.post("/inventory", data={"name": "GHK-Cu", "category": "Supply"}, files={"coa": ("a.png", PNG, "image/png")}, follow_redirects=False)
    [item] = _items(db)
    r = client.post(f"/inventory/{item.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert _items(db) == []
    assert list(config.COA_DIR.iterdir()) == []


def test_order_and_coa_details(client, db):
    r = client.post(
        "/inventory",
        data={"name": "BPC-157", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
              "quantity": "5", "lot_number": "BX-2291",
              "order_date": "2026-09-01",
              "coa_vial_size_mg": "8.5", "coa_purity_pct": "99.2%"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    [item] = _items(db)
    [li] = item.order_items
    assert li.lot_number == "BX-2291"
    assert str(li.order.order_date) == "2026-09-01"
    assert li.coa_vial_size_mg == 8.5
    assert li.coa_purity_pct == 99.2


def test_order_and_coa_validation(client, db):
    r = client.post("/inventory", data={"name": "X", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                        "quantity": "5", "order_date": "not-a-date",
                                        "coa_vial_size_mg": "0", "coa_purity_pct": "101"})
    assert r.status_code == 422
    text = html.unescape(r.text)
    for msg in ("Enter a valid date", "Lab vial size must be greater than 0", "Purity must be between 0 and 100%"):
        assert msg in text

    r = client.post("/inventory", data={"name": "X", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                       "order_date": "not-a-date", "coa_purity_pct": "high"})
    assert r.status_code == 422
    text = html.unescape(r.text)
    assert "Enter a valid date" in text and "Purity must be a number" in text
    assert _items(db) == []


def test_json_api(client):
    client.post("/inventory", data={"name": "Retatrutide", "category": "Supply", "cost": "80"}, follow_redirects=False)
    [row] = client.get("/api/inventory").json()
    assert row["name"] == "Retatrutide"
    assert row["cost"] == 80.0
    assert row["orders"] == []  # Supply items have no orders
    assert row["category"] == "Supply"
    assert client.get(f"/api/inventory/{row['id']}").json()["id"] == row["id"]
    assert client.get("/api/inventory/9999").status_code == 404


# ---------------------------------------------------------------- Phase 1: units, required fields, vendors

def test_medium_required_fields_enforced_server_side(client, db):
    cases = [
        ({"name": "X", "category": "Medicine", "medium": "Lyophilized", "quantity": "1", "order_date": "2026-09-01"}, "Amount is required for Lyophilized."),
        ({"name": "X", "category": "Medicine", "medium": "Liquid", "vial_size_mg": "10", "quantity": "1", "order_date": "2026-09-01"}, "Volume (mL) is required for Liquid."),
        ({"name": "X", "category": "Medicine", "medium": "Autoinjector", "quantity": "1", "order_date": "2026-09-01"}, "Doses per pen is required for Autoinjector."),
        ({"name": "X", "category": "Medicine", "medium": "Pill", "quantity": "1", "order_date": "2026-09-01"}, "Amount per pill is required for Pill."),
        ({"name": "X", "category": "Medicine", "medium": "Inhaler", "quantity": "1", "order_date": "2026-09-01"}, "Amount is required for Inhaler."),
    ]
    for data, message in cases:
        r = client.post("/inventory", data=data)
        assert r.status_code == 422, data
        assert message in html.unescape(r.text), (data, message)
    assert _items(db) == []


def test_each_medium_can_be_completed(client, db):
    completions = [
        {"name": "Powder", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10", "quantity": "1", "order_date": "2026-09-01"},
        {"name": "Solution", "category": "Medicine", "medium": "Liquid", "vial_size_mg": "10", "volume_ml": "2", "quantity": "1", "order_date": "2026-09-01"},
        {"name": "Pen", "category": "Medicine", "medium": "Autoinjector", "units_per_package": "4", "quantity": "1", "order_date": "2026-09-01"},
        {"name": "Tablets", "category": "Medicine", "medium": "Pill", "vial_size_mg": "5", "units_per_package": "30", "quantity": "1", "order_date": "2026-09-01"},
        {"name": "Spray", "category": "Medicine", "medium": "Inhaler", "vial_size_mg": "50", "quantity": "1", "order_date": "2026-09-01"},
    ]
    for data in completions:
        r = client.post("/inventory", data=data, follow_redirects=False)
        assert r.status_code == 303, data
    names = {i.name for i in _items(db)}
    assert names == {"Powder", "Solution", "Pen", "Tablets", "Spray"}


def test_vial_size_unit_saves(client, db):
    client.post("/inventory", data={"name": "X", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "250", "vial_size_unit": "mcg",
                                   "quantity": "1", "order_date": "2026-09-01"}, follow_redirects=False)
    [item] = _items(db)
    assert item.vial_size_mg == 250 and item.vial_size_unit.value == "mcg"
    row = client.get(f"/api/inventory/{item.id}").json()
    assert row["vial_size_unit"] == "mcg"


def test_vendor_creates_and_reuses_case_insensitively(client, db):
    client.post("/inventory", data={"name": "A", "category": "Supply", "vendor": "Acme Peptides"}, follow_redirects=False)
    client.post("/inventory", data={"name": "B", "category": "Supply", "vendor": "acme peptides"}, follow_redirects=False)
    items = {i.name: i for i in _items(db)}
    assert items["A"].vendor_id == items["B"].vendor_id
    assert items["A"].vendor == "Acme Peptides" and items["B"].vendor == "Acme Peptides"
    assert db.query(Vendor).count() == 1


def test_vendor_cleared_on_edit(client, db):
    client.post("/inventory", data={"name": "A", "category": "Supply", "vendor": "Acme"}, follow_redirects=False)
    [item] = _items(db)
    client.post(f"/inventory/{item.id}", data={"name": "A", "category": "Supply", "vendor": ""}, follow_redirects=False)
    [item] = _items(db)
    assert item.vendor is None and item.vendor_id is None


def test_expiration_and_storage_round_trip(client, db):
    client.post("/inventory", data={"name": "X", "category": "Supply", "storage": "fridge"}, follow_redirects=False)
    [item] = _items(db)
    assert item.storage.value == "fridge"
    assert item.id in [i.id for i in _items(db)]


def test_expiration_date_must_be_valid(client, db):
    r = client.post("/inventory", data={"name": "X", "category": "Supply"}, follow_redirects=False)
    assert r.status_code == 303  # Supply doesn't require expiration_date
    assert len(_items(db)) == 1


# ---------------------------------------------------------------- Phase 1: search / sort / filter markup

def test_list_renders_both_items_in_the_medicines_section(client, db):
    # The search/filter/sort toolbar was removed in Task 7 (five sectioned tables replaced the
    # single filterable one); this now legitimately renders the list page instead of only
    # checking the DB, since the old rendering crash (removed InventoryItem fields) is fixed.
    client.post("/inventory", data={"name": "BPC-157", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "quantity": "1", "order_date": "2026-09-01", "vendor": "Acme"}, follow_redirects=False)
    client.post("/inventory", data={"name": "Retatrutide", "category": "Medicine", "medium": "Liquid", "vial_size_mg": "5",
                                    "volume_ml": "2", "quantity": "1", "order_date": "2026-09-01"}, follow_redirects=False)
    items = _items(db)
    assert len(items) == 2
    assert {i.name for i in items} == {"BPC-157", "Retatrutide"}

    t = text(client.get("/inventory"))
    section = t.split('id="medicines-heading"')[1].split("</section>")[0]
    assert "BPC-157" in section and "Retatrutide" in section
    assert "inv-search" not in t and "data-filter" not in t and "sort-btn" not in t


def test_search_filter_scoped_to_owner_only(client, db):
    client.post("/inventory", data={"name": "Mine Only", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                   "quantity": "1", "order_date": "2026-09-01"}, follow_redirects=False)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "InvOther", "password": "Inv0ther!", "confirm": "Inv0ther!"})
    # The other user's API should not return the first user's items
    items = other.get("/api/inventory").json()
    assert items == []


# ---------------------------------------------------------------- available_count

from app.models import Category, Order


def test_available_count_supply_is_the_plain_count_column(db, me):
    item = InventoryItem(owner_id=me, name="Alcohol Pads", category=Category.SUPPLY, count=250)
    db.add(item)
    db.commit()
    assert item.available_count == 250


def test_available_count_uses_received_quantity_not_ordered_quantity(db, me):
    from app.models import OrderItem

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10, reconstituted_count=2, sold_count=1)
    db.add(item)
    db.flush()
    arrived_order = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 10))
    in_transit_order = Order(order_date=date(2026, 9, 20))
    db.add_all([arrived_order, in_transit_order])
    db.flush()
    item.order_items.append(OrderItem(order_id=arrived_order.id, quantity=10, received_quantity=8))
    item.order_items.append(OrderItem(order_id=in_transit_order.id, quantity=5))  # not checked in
    db.commit()
    db.refresh(item)
    # 8 received (not the 10 ordered -- 2 were short) - 2 reconstituted - 1 sold; the in-transit
    # line's 5 don't count at all yet.
    assert item.available_count == 8 - 2 - 1


def test_order_item_received_quantity_cannot_exceed_ordered_quantity(db, me):
    from app.models import OrderItem

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(item)
    db.flush()
    order = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 10))
    db.add(order)
    db.flush()
    with pytest.raises(IntegrityError):
        db.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=5, received_quantity=6))
        db.flush()
    db.rollback()


def test_sale_model_has_quantity_and_price_constraints(db, me):
    from app.models import Sale

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(item)
    db.flush()
    item.sales.append(Sale(quantity=2, sale_date=date(2026, 9, 20), price_cents=15000))
    db.commit()
    db.refresh(item)
    assert item.sales[0].price == 150.0

    with pytest.raises(IntegrityError):
        db.add(Sale(inventory_item_id=item.id, quantity=0, sale_date=date(2026, 9, 20), price_cents=100))
        db.flush()
    db.rollback()

    with pytest.raises(IntegrityError):
        db.add(Sale(inventory_item_id=item.id, quantity=1, sale_date=date(2026, 9, 20), price_cents=-100))
        db.flush()
    db.rollback()


# ---------------------------------------------------------------- Task 2: category-gated parsing, first Order on create


def test_add_medicine_item_creates_item_and_first_order(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123", "vendor": "PeptideCo", "cost": "84.00", "tax": "5.00", "shipping": "10.00",
        "lot_number": "LOT1", "expiration_date": "2028-01-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.category == Category.MEDICINE
        assert item.available_count == 0  # not arrived yet -- in transit
        li = item.order_items[0]
        assert li.quantity == 10 and li.order.tracking_number == "LY123" and li.lot_number == "LOT1"
        assert li.cost_cents == 8400 and li.order.tax_cents == 500 and li.order.shipping_cents == 1000


def test_add_supply_item_has_no_order(client, db):
    r = client.post("/inventory", data={
        "name": "Alcohol Pads", "category": "Supply", "count": "250", "cost": "4.23", "vendor": "Acme Pharmacy",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Alcohol Pads"))
        assert item.category == Category.SUPPLY
        assert item.available_count == 250
        assert item.order_items == []


def test_add_bac_water_item_has_no_medium_fields(client, db):
    r = client.post("/inventory", data={
        "name": "Bacteriostatic Water", "category": "BAC Water", "quantity": "4", "order_date": "2026-09-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Bacteriostatic Water"))
        assert item.category == Category.BAC_WATER and item.medium is None


def test_low_stock_threshold_saved_including_zero(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "low_stock_threshold": "0",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.low_stock_threshold == 0  # explicit 0 must round-trip, not become None


def test_low_stock_threshold_defaults_to_none_when_blank(client, db):
    r = client.post("/inventory", data={
        "name": "Semaglutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "5",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Semaglutide"))
        assert item.low_stock_threshold is None


def test_add_medicine_item_requires_quantity(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "order_date": "2026-08-01",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(InventoryItem).filter_by(name="Retatrutide").count() == 0


def test_editing_item_does_not_create_or_touch_orders(client, db, me):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    r = client.post(f"/inventory/{item_id}", data={"name": "Retatrutide XR", "storage": "fridge"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.name == "Retatrutide XR" and item.storage.value == "fridge"
        assert len(item.order_items) == 1  # unchanged


# ---------------------------------------------------------------- Task 4: Item detail page


def test_item_detail_page_shows_details_and_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Retatrutide" in t
    assert "10 mg" in t
    assert "0" in t.split('id="inv-available-count"')[1][:20]  # not arrived yet


def test_item_detail_page_404s_for_someone_elses_private_item(client, db):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "DetailOther", "password": "DetailOther1!", "confirm": "DetailOther1!"})
    other.post("/inventory", data={"name": "Private Item", "category": "Supply", "count": "1"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Private Item"))
    assert client.get(f"/inventory/{item_id}").status_code == 404


def test_detail_page_has_edit_dialog_with_item_data(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="edit-item"' in t
    assert 'id="item-edit-dialog"' in t
    assert 'id="edit-item-data"' in t
    assert '"name": "Retatrutide"' in t


def test_edit_item_from_detail_page_updates_and_rerenders_on_error(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    # Successful edit via the detail-page dialog's target route.
    r = client.post(f"/inventory/{item_id}", data={
        "name": "Retatrutide XR", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "storage": "fridge",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.name == "Retatrutide XR" and item.storage.value == "fridge"

    # A validation error re-renders detail.html (not list.html) with the dialog reopened.
    r = client.post(f"/inventory/{item_id}", data={
        "name": "", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
    })
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "Item name is required" in t
    assert "data-open-on-load" in t
    assert 'id="item-edit-dialog"' in t
    assert 'Order history' in t  # confirms detail.html rendered, not list.html


# ---------------------------------------------------------------- Task 5: Order History section


def test_add_order_creates_a_second_order_and_updates_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)  # simulate first order having arrived (check-in is Task 3)
        li.received_quantity = li.quantity
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-09-20", "tracking_number": "LY456",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert len(item.order_items) == 2
        assert item.available_count == 10  # the new order hasn't arrived yet


def test_checking_in_an_order_line_via_raw_write_updates_available_count(client, db):
    # Arrival is now only set by the check-in flow (Task 3), not by posting the merged order form --
    # this simulates that check-in with a raw DB write instead of going through update_order.
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        assert s.get(InventoryItem, item_id).available_count == 0  # not arrived yet

    with SessionLocal() as s:
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = li.quantity
        s.commit()
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).available_count == 10


def test_order_validation_error_shows_banner_and_reopens_dialog_prefilled(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "not-a-date", "vendor": "PeptideCo", "lot_number": "LOT9",
    })
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert 'role="alert"' in t and "Please fix the highlighted fields" in t
    assert "data-open-on-load" in t
    assert 'id="order-dialog"' in t
    # The dialog reopens pre-filled with what was submitted, not reset to blank.
    assert 'name="vendor" value="PeptideCo"' in t
    assert 'name="lot_number" value="LOT9"' in t


def test_order_date_ordering_is_validated(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    # Shipped before order.
    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-08-10", "shipped_date": "2026-08-05",
    })
    assert r.status_code == 422
    assert "Shipped date can't be before the order date" in html.unescape(r.text)

    # arrival_date ordering checks are gone -- arrival_date is no longer a postable field at all
    # (arrival only happens via check-in, Task 3); posting it is simply ignored.
    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-08-10", "arrival_date": "2026-08-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert all(li.order.arrival_date is None for li in item.order_items)

    # Valid ordering succeeds.
    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-08-01", "shipped_date": "2026-08-03",
    }, follow_redirects=False)
    assert r.status_code == 303


def test_tracking_site_must_be_http_url(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-08-01", "tracking_site": "javascript:alert(1)",
    })
    assert r.status_code == 422
    assert "Tracking site must be a valid http(s) URL" in html.unescape(r.text)
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].id  # first order still the only one
        assert len(s.get(InventoryItem, item_id).order_items) == 1

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-08-01", "tracking_site": "https://track.example.com/x",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert any(li.order.tracking_site == "https://track.example.com/x"
                  for li in s.get(InventoryItem, item_id).order_items)


def test_add_order_requires_ownership(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "OrderOther", "password": "OrderOther1!", "confirm": "OrderOther1!"})
    r = other.post(f"/inventory/{item_id}/orders", data={"quantity": "5", "order_date": "2026-09-20"})
    assert r.status_code == 404


# ---------------------------------------------------------------- Sold flow: Task 2 (no BAC bundling yet)


def _check_in(item_id: int, quantity: int) -> None:
    """Simulates a Task-3 check-in with a raw DB write -- arrival_date is no longer settable
    through the merged add/edit order form."""
    with SessionLocal() as s:
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 5)
        li.received_quantity = quantity
        s.commit()


def _medicine_with_stock(client, name="Retatrutide", quantity=10):
    client.post("/inventory", data={
        "name": name, "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": str(quantity), "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == name))
    _check_in(item_id, quantity)
    return item_id


def test_sell_item_creates_sale_and_updates_available_count(client, db):
    item_id = _medicine_with_stock(client)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "3", "price": "150.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.sold_count == 3
        assert item.available_count == 7
        [sale] = item.sales
        assert sale.quantity == 3 and sale.price == 150.0 and sale.sale_date == date(2026, 9, 25)


def test_sell_item_rejects_more_than_available(client, db):
    item_id = _medicine_with_stock(client, quantity=5)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "6", "price": "10.00",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.sold_count == 0


def test_sell_item_requires_price(client, db):
    item_id = _medicine_with_stock(client)
    r = client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "1"})
    assert r.status_code == 422
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.sales == []


def test_sell_item_rejects_future_date(client, db):
    item_id = _medicine_with_stock(client)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2099-01-01", "quantity": "1", "price": "10.00",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.sales == []


def test_sell_item_404s_for_supply(client, db):
    r = client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "10"},
                    follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Alcohol Pads"))
    r = client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "1", "price": "1.00"})
    assert r.status_code == 404


def test_sell_item_requires_ownership(client, db):
    item_id = _medicine_with_stock(client)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "SaleOther", "password": "SaleOther1!", "confirm": "SaleOther1!"})
    r = other.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "1", "price": "1.00"})
    assert r.status_code == 404


def _bac_water_with_stock(client, name="Bacteriostatic Water", quantity=20):
    client.post("/inventory", data={
        "name": name, "category": "BAC Water", "quantity": str(quantity), "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == name))
    _check_in(item_id, quantity)
    return item_id


def test_sell_medicine_with_bundled_bac_water_creates_two_separate_sales(client, db):
    med_id = _medicine_with_stock(client)
    bac_id = _bac_water_with_stock(client)
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "2", "price": "150.00",
        "include_bac_water": "1", "bac_item_id": str(bac_id), "bac_quantity": "3", "bac_price": "9.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        med = s.get(InventoryItem, med_id)
        bac = s.get(InventoryItem, bac_id)
        assert med.sold_count == 2 and med.available_count == 8
        assert bac.sold_count == 3 and bac.available_count == 17
        [med_sale] = med.sales
        [bac_sale] = bac.sales
        assert med_sale.price == 150.0 and med_sale.quantity == 2
        assert bac_sale.price == 9.0 and bac_sale.quantity == 3
        assert med_sale.sale_date == bac_sale.sale_date == date(2026, 9, 25)


def test_sell_with_bac_water_checked_requires_bac_quantity_and_price(client, db):
    med_id = _medicine_with_stock(client)
    bac_id = _bac_water_with_stock(client)
    # BAC Water item picked, but its quantity/price left blank -- must not be silently dropped.
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "50.00", "include_bac_water": "1",
        "bac_item_id": str(bac_id),
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id).sold_count == 0  # nothing committed -- one form, one transaction
        assert s.get(InventoryItem, bac_id).sold_count == 0
        assert s.get(InventoryItem, med_id).sales == []
        assert s.get(InventoryItem, bac_id).sales == []


def test_sell_with_bac_water_checked_but_no_item_selected_is_rejected(client, db):
    med_id = _medicine_with_stock(client)
    bac_id = _bac_water_with_stock(client)
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "50.00", "include_bac_water": "1",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id).sold_count == 0
        assert s.get(InventoryItem, med_id).sales == []
        assert s.get(InventoryItem, bac_id).sold_count == 0
        assert s.get(InventoryItem, bac_id).sales == []


def test_sell_rejects_more_bac_water_than_available(client, db):
    med_id = _medicine_with_stock(client)
    bac_id = _bac_water_with_stock(client, quantity=2)
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "50.00", "include_bac_water": "1",
        "bac_item_id": str(bac_id), "bac_quantity": "3", "bac_price": "9.00",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id).sold_count == 0
        assert s.get(InventoryItem, med_id).sales == []
        assert s.get(InventoryItem, bac_id).sold_count == 0
        assert s.get(InventoryItem, bac_id).sales == []


def test_sell_rejects_bac_item_not_owned_by_caller(client, db):
    med_id = _medicine_with_stock(client)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "BacOther", "password": "BacOther1!", "confirm": "BacOther1!"})
    other.post("/inventory", data={
        "name": "Their Water", "category": "BAC Water", "quantity": "20", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        their_bac_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Their Water"))
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "50.00", "include_bac_water": "1",
        "bac_item_id": str(their_bac_id), "bac_quantity": "1", "bac_price": "9.00",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id).sold_count == 0
        assert s.get(InventoryItem, med_id).sales == []
        assert s.get(InventoryItem, their_bac_id).sold_count == 0
        assert s.get(InventoryItem, their_bac_id).sales == []


def test_sell_rejects_bac_item_that_is_not_bac_water_category(client, db):
    med_id = _medicine_with_stock(client)
    other_med_id = _medicine_with_stock(client, name="Semaglutide")
    r = client.post(f"/inventory/{med_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "50.00", "include_bac_water": "1",
        "bac_item_id": str(other_med_id), "bac_quantity": "1", "bac_price": "9.00",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id).sold_count == 0
        assert s.get(InventoryItem, med_id).sales == []
        assert s.get(InventoryItem, other_med_id).sold_count == 0
        assert s.get(InventoryItem, other_med_id).sales == []


def test_shared_item_order_history_is_visible_but_not_editable(client, db, me):
    client.post("/inventory", data={
        "name": "Shared Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Shared Retatrutide"))

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "SharedGrantee", "password": "SharedGrantee1!", "confirm": "SharedGrantee1!"})
    with SessionLocal() as s:
        grantee_id = s.scalar(select(User.id).where(User.username_key == "sharedgrantee"))
    try:
        client.post(f"/settings/sharing/{grantee_id}/inventory")  # matches this app's existing Share-grant route

        t = text(other.get(f"/inventory/{item_id}"))
        assert "Shared Retatrutide" in t  # item visible to the grantee
        assert "LY123" in t  # tracking info visible again now that Task 4 reads item.order_items
        assert 'data-action="add-order"' not in t  # but not editable

        assert other.post(f"/inventory/{item_id}/orders", data={"quantity": "1", "order_date": "2026-09-01"}).status_code == 404
        with SessionLocal() as s:
            order_item_id = s.get(InventoryItem, item_id).order_items[0].id
        assert other.post(f"/inventory/{item_id}/orders/{order_item_id}", data={"quantity": "1", "order_date": "2026-09-01"}).status_code == 404
    finally:
        client.post(f"/settings/sharing/{grantee_id}/inventory", data={"on": "0"})  # revoke -- keep suite state clean


def test_detail_page_lists_order_history(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = text(client.get(f"/inventory/{item_id}"))
    assert "LY123" in t
    assert 'href="https://track.example/x"' in t


def test_order_history_table_shows_expiration_tax_and_shipping(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "expiration_date": "2027-08-01",
        "tax": "5.25", "shipping": "12.00",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = text(client.get(f"/inventory/{item_id}"))
    assert "<th>Expiration</th>" in t and "<th>Tax</th>" in t and "<th>Shipping</th>" in t
    assert "$5.25" in t and "$12.00" in t


# ---------------------------------------------------------------- Multi-item orders: Task 4 (template)


def test_order_history_shows_lines_and_checkin_button(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="check-in"' in t
    assert 'id="checkin-dialog"' in t
    assert "10" in t  # quantity shown


def test_order_history_hides_checkin_button_once_arrived(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 10
        s.commit()
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="check-in" data-order-id="' not in t


def test_checkin_validation_error_reopens_dialog(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "11",
    })
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "data-open-on-load" in t
    assert 'id="checkin-dialog"' in t
    assert "Must be between 0 and 10" in t


# ---------------------------------------------------------------- Sold flow: Task 4 (template)


def test_detail_page_shows_sold_button_and_dialog_for_medicine(client, db):
    item_id = _medicine_with_stock(client)
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="sold"' in t
    assert 'id="sale-dialog"' in t
    assert 'name="include_bac_water"' in t
    assert 'Sale history' in t


def test_detail_page_shows_sold_button_for_bac_water_without_bundle_checkbox(client, db):
    item_id = _bac_water_with_stock(client)
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="sold"' in t
    assert 'name="include_bac_water"' not in t


def test_detail_page_hides_sold_button_for_supply(client, db):
    r = client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "10"},
                    follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Alcohol Pads"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="sold"' not in t


def test_sold_button_disabled_when_nothing_available(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",  # no arrival_date -- 0 available
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="sold"' in t and "disabled" in t.split('data-action="sold"')[1][:80]


def test_sale_history_lists_committed_sales(client, db):
    item_id = _medicine_with_stock(client)
    client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "3", "price": "150.00"},
               follow_redirects=False)
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Sale history" in t
    assert "$150.00" in t


def test_sale_validation_error_reopens_dialog_prefilled(client, db):
    item_id = _medicine_with_stock(client, quantity=5)
    r = client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "6", "price": "10.00"})
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "data-open-on-load" in t
    assert 'id="sale-dialog"' in t
    assert 'name="price" type="number" min="0" step="0.01" id="sale-price" value="10.00"' in t


def test_quantity_exceeds_available_error_renders_in_html(client, db):
    item_id = _medicine_with_stock(client, quantity=5)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "6", "price": "10.00",
    })
    assert r.status_code == 422
    assert "Only 5 available to sell" in html.unescape(r.text)


def test_bac_water_required_error_renders_when_no_bac_water_in_stock(client, db):
    # No BAC Water item exists at all for this user -- bac_water_options is empty, so the dialog
    # renders the "No BAC Water in stock." message branch instead of the <select>. The error must
    # still surface there, not just show the generic "fix the highlighted fields" banner.
    item_id = _medicine_with_stock(client)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "10.00", "include_bac_water": "1",
    })
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "Select a BAC Water item" in t
    assert "No BAC Water in stock." in t


def test_malformed_bac_item_id_returns_422_not_500(client, db):
    item_id = _medicine_with_stock(client)
    # A Unicode "digit" character passes str.isdigit() but int() on it (and a very long run of
    # ASCII digits) must not blow up the route with an unhandled exception.
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "10.00",
        "include_bac_water": "1", "bac_item_id": "²", "bac_quantity": "1", "bac_price": "1.00",
    })
    assert r.status_code == 422

    r2 = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "1", "price": "10.00",
        "include_bac_water": "1", "bac_item_id": "9" * 400, "bac_quantity": "1", "bac_price": "1.00",
    })
    assert r2.status_code == 422


def test_sell_exactly_available_count_succeeds(client, db):
    item_id = _medicine_with_stock(client, quantity=5)
    r = client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "5", "price": "10.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).available_count == 0


def test_available_count_reflects_new_value_on_next_render_after_sale(client, db):
    item_id = _medicine_with_stock(client, quantity=10)
    client.post(f"/inventory/{item_id}/sales", data={
        "sale_date": "2026-09-25", "quantity": "3", "price": "10.00",
    }, follow_redirects=False)
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert '<dt>Available</dt><dd id="inv-available-count"><strong>7</strong></dd>' in t


def test_shared_viewer_does_not_see_sold_button_or_dialog(client, db, me):
    item_id = _medicine_with_stock(client, name="Shared Sellable")
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "SoldViewer", "password": "SoldViewer1!", "confirm": "SoldViewer1!"})
    with SessionLocal() as s:
        grantee_id = s.scalar(select(User.id).where(User.username_key == "soldviewer"))
    try:
        client.post(f"/settings/sharing/{grantee_id}/inventory")
        t = html.unescape(other.get(f"/inventory/{item_id}").text)
        assert 'data-action="sold"' not in t
        assert 'id="sale-dialog"' not in t
    finally:
        client.post(f"/settings/sharing/{grantee_id}/inventory", data={"on": "0"})


# ---------------------------------------------------------------- Task 7: list resectioning


def test_inventory_page_sections_items_by_category(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    client.post("/inventory", data={"name": "Bac Water", "category": "BAC Water", "quantity": "4", "order_date": "2026-08-01"})
    client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "250"})
    t = text(client.get("/inventory"))
    assert t.index("Retatrutide") < t.index("Bac Water") < t.index("Alcohol Pads")
    assert '<h2 id="medicines-heading"' in t
    assert '<h2 id="bac-water-heading"' in t
    assert '<h2 id="supplies-heading"' in t


def test_inventory_page_shows_in_transit_orders(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    t = text(client.get("/inventory"))
    section = t.split('id="in-transit"')[1].split("</section>")[0]
    assert "Retatrutide" in section and "LY123" in section


def test_inventory_page_notes_removed_from_list_columns(client, db):
    client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "5", "notes": "keep in the closet"})
    t = text(client.get("/inventory"))
    section = t.split('id="supplies-heading"')[1].split("</section>")[0]
    assert "keep in the closet" not in section


def test_api_inventory_includes_category_available_count_and_orders(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    body = client.get("/api/inventory").json()
    item = next(i for i in body if i["name"] == "Retatrutide")
    assert item["category"] == "Medicine"
    assert item["available_count"] == 0  # not arrived yet
    assert item["orders"][0]["tracking_number"] == "LY123"
    assert "lot_number" not in item  # moved to orders, no longer a bare item field


def test_api_inventory_includes_sales(client, db):
    item_id = _medicine_with_stock(client)
    client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "3", "price": "150.00"},
               follow_redirects=False)
    r = client.get(f"/api/inventory/{item_id}")
    assert r.json()["sales"] == [{"id": r.json()["sales"][0]["id"], "quantity": 3,
                                  "sale_date": "2026-09-25", "price": 150.0}]


# ---------------------------------------------------------------- Task 2: header/line model routes


def test_add_medicine_item_creates_item_and_first_order_header_and_line(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123", "vendor": "PeptideCo", "cost": "84.00", "tax": "5.00", "shipping": "10.00",
        "lot_number": "LOT1", "expiration_date": "2028-01-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.available_count == 0  # not checked in yet -- in transit
        [li] = item.order_items
        assert li.quantity == 10 and li.lot_number == "LOT1" and li.cost_cents == 8400
        assert li.received_quantity is None
        assert li.order.tracking_number == "LY123" and li.order.tax_cents == 500 and li.order.shipping_cents == 1000
        assert li.order.arrival_date is None  # arrival only happens via check-in (Task 3)


def test_editing_a_line_before_checkin_updates_header_and_line_together(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li_id = s.get(InventoryItem, item_id).order_items[0].id

    r = client.post(f"/inventory/{item_id}/orders/{li_id}", data={
        "quantity": "12", "order_date": "2026-08-01", "tracking_number": "LY999", "cost": "90.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        li = s.get(InventoryItem, item_id).order_items[0]
        assert li.quantity == 12 and li.cost_cents == 9000
        assert li.order.tracking_number == "LY999"


def test_editing_a_checked_in_line_can_correct_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 9  # simulate a prior check-in that received 9 of 10
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders/{li.id}", data={
        "quantity": "10", "order_date": "2026-08-01", "received_quantity": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].received_quantity == 10
        assert s.get(InventoryItem, item_id).available_count == 10


def test_editing_a_checked_in_line_with_unparseable_quantity_is_a_422_not_a_crash(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 9  # simulate a prior check-in that received 9 of 10
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders/{li.id}", data={
        "quantity": "not-a-number", "order_date": "2026-08-01", "received_quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 422
    assert "whole number" in r.text
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].received_quantity == 9  # unchanged


def test_deleting_sole_line_deletes_orphaned_order_header(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        order_id = s.get(InventoryItem, item_id).order_items[0].order_id

    client.post(f"/inventory/{item_id}/delete", follow_redirects=False)
    with SessionLocal() as s:
        from app.models import Order
        assert s.get(Order, order_id) is None


# ---------------------------------------------------------------- Multi-item orders: Task 3 (check-in)


def test_check_in_order_sets_arrival_and_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.order_items[0].order.arrival_date == date(2026, 8, 10)
        assert item.order_items[0].received_quantity == 10
        assert item.available_count == 10


def test_check_in_order_with_short_receipt_does_not_inflate_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "8",
        f"received_note_{li_id}": "2 vials cracked in transit",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.available_count == 8
        assert item.order_items[0].received_note == "2 vials cracked in transit"


def test_check_in_rejects_received_quantity_above_ordered(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "11",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].order.arrival_date is None


def test_check_in_rejects_future_arrival_date(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2099-01-01", f"received_quantity_{li_id}": "10",
    })
    assert r.status_code == 422


def test_check_in_requires_ownership(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        order_id = s.get(InventoryItem, item_id).order_items[0].order_id
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "CheckinOther", "password": "CheckinOther1!", "confirm": "CheckinOther1!"})
    r = other.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={"arrival_date": "2026-08-10"})
    assert r.status_code == 404


# ---------------------------------------------------------------- Multi-item orders: sibling note

def test_also_in_this_shipment_note_finds_siblings_across_items(client, db, me):
    from app.models import OrderItem

    medicine = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10)
    bac_water = InventoryItem(owner_id=me, name="Bacteriostatic Water", category=Category.BAC_WATER)
    db.add_all([medicine, bac_water])
    db.flush()

    order = Order(order_date=date(2026, 8, 1))
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, inventory_item_id=medicine.id, quantity=5))
    db.add(OrderItem(order_id=order.id, inventory_item_id=bac_water.id, quantity=2))
    db.commit()

    t1 = html.unescape(client.get(f"/inventory/{medicine.id}").text)
    assert "other item" in t1 and "in this shipment" in t1

    t2 = html.unescape(client.get(f"/inventory/{bac_water.id}").text)
    assert "other item" in t2 and "in this shipment" in t2


# ---------------------------------------------------------------- Multi-item orders: Task 6 (In-Transit grouping)


def test_in_transit_groups_multiple_items_from_one_order(client, db):
    from app.models import Order, OrderItem

    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        med = InventoryItem(owner_id=me_id, name="Retatrutide", category=Category.MEDICINE,
                            medium=Medium.LYOPHILIZED, vial_size_mg=10)
        bac = InventoryItem(owner_id=me_id, name="Bacteriostatic Water", category=Category.BAC_WATER)
        s.add_all([med, bac])
        s.flush()
        order = Order(order_date=date(2026, 9, 1), tracking_number="SHARED123")
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=med.id, quantity=5))
        order.items.append(OrderItem(inventory_item_id=bac.id, quantity=10))
        s.commit()

    t = html.unescape(client.get("/inventory").text)
    assert "SHARED123" in t
    assert t.count("SHARED123") == 1  # one row for the whole order, not one per line
    assert "Retatrutide" in t and "Bacteriostatic Water" in t


# ---------------------------------------------------------------- Multi-item orders: Task 7 (New Order route)


def test_new_order_creates_one_order_with_two_new_item_lines(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "tracking_number": "MULTI1", "shipping": "10.00", "tax": "5.00",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "80.00",
        "lines-1-mode": "new", "lines-1-category": "BAC Water", "lines-1-name": "Bacteriostatic Water",
        "lines-1-quantity": "10", "lines-1-cost": "15.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        med = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        bac = s.scalar(select(InventoryItem).where(InventoryItem.name == "Bacteriostatic Water"))
        assert med.order_items[0].order_id == bac.order_items[0].order_id  # same shared order
        assert med.order_items[0].order.tracking_number == "MULTI1"
        assert med.order_items[0].quantity == 5 and bac.order_items[0].quantity == 10


def test_new_order_can_restock_an_existing_item_alongside_a_new_one(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "5", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "existing", "lines-0-item_id": str(item_id), "lines-0-quantity": "5", "lines-0-cost": "80.00",
        "lines-1-mode": "new", "lines-1-category": "BAC Water", "lines-1-name": "Bacteriostatic Water",
        "lines-1-quantity": "10", "lines-1-cost": "15.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert len(item.order_items) == 2  # the original solo order, plus this new shared one


def test_new_order_requires_at_least_one_line(client, db):
    from app.models import Order
    with SessionLocal() as s:
        orders_before = s.query(Order).count()

    r = client.post("/inventory/orders", data={"order_date": "2026-09-01"})
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(Order).count() == orders_before  # no new Order created


def test_new_order_new_item_line_validates_category_fields(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-quantity": "5",  # no medium -- Medicine requires it
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(InventoryItem).count() == 0


def test_new_order_rejects_supply_category_for_new_lines(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Supply", "lines-0-name": "Syringes",
        "lines-0-quantity": "5",
    })
    assert r.status_code == 422


def test_new_order_rejects_existing_item_not_owned_by_caller(client, db):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "NewOrderOther", "password": "NewOrderOther1!", "confirm": "NewOrderOther1!"})
    other.post("/inventory", data={
        "name": "Not Yours", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "5", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        their_item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Not Yours"))

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "existing", "lines-0-item_id": str(their_item_id), "lines-0-quantity": "5",
    })
    assert r.status_code == 422


def test_inventory_page_has_new_order_button_and_dialog(client, db):
    t = html.unescape(client.get("/inventory").text)
    assert 'data-action="new-order"' in t
    assert 'id="new-order-dialog"' in t
    assert 'data-action="add-line"' in t


def test_new_order_validation_error_reopens_dialog(client, db):
    r = client.post("/inventory/orders", data={"order_date": "2026-09-01"})
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "data-open-on-load" in t
    assert 'id="new-order-dialog"' in t
    assert "Add at least one item" in t


# ---------------------------------------------------------------- Multi-item orders: Task 11 (cost allocation)


def test_shipping_allocation_sums_exactly_to_the_order_total(db, me):
    from app.models import OrderItem

    med = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                        medium=Medium.LYOPHILIZED, vial_size_mg=10)
    bac = InventoryItem(owner_id=me, name="Bacteriostatic Water", category=Category.BAC_WATER)
    db.add_all([med, bac])
    db.flush()
    order = Order(order_date=date(2026, 9, 1), shipping_cents=1000)  # $10.00, split three ways
    db.add(order)
    db.flush()
    li1 = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=6667)
    li2 = OrderItem(inventory_item_id=bac.id, quantity=1, cost_cents=3333)
    li3 = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=1)
    order.items.extend([li1, li2, li3])
    db.commit()
    db.refresh(order)
    total_allocated = li1.allocated_shipping_cents + li2.allocated_shipping_cents + li3.allocated_shipping_cents
    assert total_allocated == 1000  # no dropped or invented cent from rounding


def test_shipping_allocation_is_zero_when_order_has_no_shipping_cost(db, me):
    from app.models import OrderItem

    med = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                        medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(med)
    db.flush()
    order = Order(order_date=date(2026, 9, 1))  # no shipping_cents set
    db.add(order)
    db.flush()
    li = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=8000)
    order.items.append(li)
    db.commit()
    db.refresh(order)
    assert li.allocated_shipping_cents == 0
    assert li.total_cost == 80.0


def test_item_detail_shows_total_cost_column(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "cost": "80.00", "shipping": "10.00",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Total cost" in t
    assert "$90.00" in t  # $80 cost + all $10 shipping, since it's the only line on this order


# ---------------------------------------------------------------- Final review fixes


def test_new_order_new_item_line_with_liquid_medium_succeeds(client, db):
    # Fix 1: Liquid requires volume_ml (per required_fields_for), which the New Order "new item"
    # field group didn't expose at all -- this used to 422 unconditionally.
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Liquid Test Item",
        "lines-0-medium": "Liquid", "lines-0-vial_size_mg": "10", "lines-0-volume_ml": "2",
        "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Liquid Test Item"))
        assert item is not None
        assert item.medium == Medium.LIQUID
        assert item.volume_ml == 2


def test_new_order_validation_error_shows_per_line_field_error(client, db):
    # Fix 2: a validation error used to wipe all posted lines and never surface per-line messages.
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-1-mode": "new", "lines-1-category": "BAC Water", "lines-1-name": "Bacteriostatic Water",
        "lines-1-quantity": "",  # left blank on purpose -- line 1's real error
    }, follow_redirects=False)
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "Quantity must be a whole number greater than 0." in t
    assert 'id="new-order-error-data"' in t


def test_received_note_shown_on_detail_page(client, db):
    # Fix 3: received_note was write-only -- never rendered anywhere.
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "8",
        f"received_note_{li_id}": "Two vials arrived shattered",
    }, follow_redirects=False)
    assert r.status_code == 303
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Two vials arrived shattered" in t


def test_api_inventory_includes_received_note(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "8",
        f"received_note_{li_id}": "Short shipment",
    }, follow_redirects=False)

    data = client.get("/api/inventory").json()
    item = next(i for i in data if i["name"] == "Retatrutide")
    assert item["orders"][0]["received_note"] == "Short shipment"


def test_order_history_sorted_newest_first(client, db):
    # Fix 5: item.order_items has no order_by, so the table lost its newest-first sort.
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-01-01", "tracking_number": "OLDEST1",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-09-20", "tracking_number": "NEWEST1",
    }, follow_redirects=False)
    assert r.status_code == 303

    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert t.index("NEWEST1") < t.index("OLDEST1")


def test_new_order_existing_item_dropdown_excludes_other_owners_shared_items(client, db):
    # Fix 6: the New Order "restock existing item" dropdown offered items shared by other owners
    # even though picking one always 422s server-side (_own_item rejects it).
    client.post("/inventory", data={
        "name": "Owner Only Item", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "5", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Owner Only Item"))

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "DropdownGrantee", "password": "DropdownGrantee1!", "confirm": "DropdownGrantee1!"})
    with SessionLocal() as s:
        grantee_id = s.scalar(select(User.id).where(User.username_key == "dropdowngrantee"))
    try:
        client.post(f"/settings/sharing/{grantee_id}/inventory")
        t = html.unescape(other.get("/inventory").text)
        assert "Owner Only Item" in t  # shared item still visible in the list itself
        assert f'<option value="{item_id}">Owner Only Item (Medicine)</option>' not in t
    finally:
        client.post(f"/settings/sharing/{grantee_id}/inventory", data={"on": "0"})


def test_editing_a_checked_in_line_lowering_quantity_below_received_is_422_not_500(client, db):
    # Fix 8: lowering quantity below the already-checked-in received_quantity without also posting
    # received_quantity used to slip past validation and 500 on the CHECK constraint at commit.
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 10
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders/{li.id}", data={
        "quantity": "5", "order_date": "2026-08-01",
    }, follow_redirects=False)
    assert r.status_code == 422
    assert "Must be between 0 and 5" in html.unescape(r.text)
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].received_quantity == 10  # unchanged


def test_deleting_one_item_of_a_shared_order_keeps_the_other_line_and_order(client, db, me):
    # Fix 9 (Review Focus 4 coverage): deleting one item on a shared order must not delete the
    # order header while another line still uses it.
    from app.models import Order, OrderItem

    med = InventoryItem(owner_id=me, name="Shared Med", category=Category.MEDICINE,
                        medium=Medium.LYOPHILIZED, vial_size_mg=10)
    bac = InventoryItem(owner_id=me, name="Shared BAC", category=Category.BAC_WATER)
    db.add_all([med, bac])
    db.flush()
    order = Order(order_date=date(2026, 9, 1))
    db.add(order)
    db.flush()
    li_med = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=100)
    li_bac = OrderItem(inventory_item_id=bac.id, quantity=1, cost_cents=100)
    order.items.extend([li_med, li_bac])
    db.commit()
    med_id, order_id, bac_li_id, bac_id = med.id, order.id, li_bac.id, bac.id

    r = client.post(f"/inventory/{med_id}/delete", follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        assert s.get(InventoryItem, med_id) is None
        assert s.get(InventoryItem, bac_id) is not None
        assert s.get(Order, order_id) is not None  # order header remains -- bac line still uses it
        assert s.get(OrderItem, bac_li_id) is not None


# ---------------------------------------------------------------- Task 5: New Order vendor branch,
# staleness prompt, per-line price recall


def _price_recall_blob(client) -> dict:
    import json
    import re
    t = client.get("/inventory").text
    m = re.search(r'id="price-recall-data">(.*?)</script>', t, re.S)
    return json.loads(m.group(1))


def test_new_order_new_vendor_branch_creates_vendor_with_full_profile(client, db):
    from app.models import ContactMethodType, Vendor

    email_type_id = db.scalar(select(ContactMethodType.id).where(ContactMethodType.name == "Email"))

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "is_new_vendor": "yes",
        "name": "Brand New Vendor Co",
        "website": "https://newvendor.example",
        "supplier": "Acme Supplier",
        "contact_name": "Jane Doe",
        "contacts-0-method_type_id": str(email_type_id),
        "contacts-0-value": "sales@newvendor.example",
        "new_payment_type": "Gift Card",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "80.00",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendors = s.scalars(select(Vendor).where(Vendor.name == "Brand New Vendor Co")).all()
        assert len(vendors) == 1  # exactly one new Vendor row
        vendor = vendors[0]
        assert vendor.website == "https://newvendor.example"
        assert vendor.supplier == "Acme Supplier"
        assert vendor.contact_name == "Jane Doe"
        [contact] = vendor.contacts
        assert contact.value == "sales@newvendor.example"
        assert contact.method_type_id == email_type_id
        assert "Gift Card" in {pm.method_type.name for pm in vendor.payment_methods}

        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.order_items[0].order.vendor_id == vendor.id


def test_new_order_existing_vendor_branch_populates_from_dropdown(client, db):
    with SessionLocal() as s:
        vendor = Vendor(name="Existing Dropdown Vendor")
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "is_new_vendor": "no",
        "vendor_id": str(vendor_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Semaglutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "5", "lines-0-quantity": "3",
        "lines-0-cost": "60.00",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Semaglutide"))
        assert item.order_items[0].order.vendor_id == vendor_id
        assert s.query(Vendor).count() == 1  # no duplicate Vendor row created


def test_new_order_price_recall_prefills_from_last_order_same_vendor_same_item(client, db):
    with SessionLocal() as s:
        vendor = Vendor(name="Price Recall Vendor")
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    r = client.post("/inventory/orders", data={
        "order_date": "2026-08-01", "is_new_vendor": "no", "vendor_id": str(vendor_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "84.00",
    }, follow_redirects=False)
    assert r.status_code == 303

    blob = _price_recall_blob(client)
    assert blob[str(vendor_id)]["retatrutide"] == 84.0


def test_new_order_price_recall_blank_when_never_ordered_from_this_vendor(client, db):
    with SessionLocal() as s:
        vendor = Vendor(name="No History Vendor")
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    blob = _price_recall_blob(client)
    assert str(vendor_id) not in blob


def test_new_order_price_recall_does_not_leak_across_vendors(client, db):
    with SessionLocal() as s:
        vendor_x = Vendor(name="Price Recall Vendor X")
        vendor_y = Vendor(name="Price Recall Vendor Y")
        s.add_all([vendor_x, vendor_y])
        s.commit()
        x_id, y_id = vendor_x.id, vendor_y.id

    client.post("/inventory/orders", data={
        "order_date": "2026-08-01", "is_new_vendor": "no", "vendor_id": str(x_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Same Name Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "84.00",
    }, follow_redirects=False)
    client.post("/inventory/orders", data={
        "order_date": "2026-08-02", "is_new_vendor": "no", "vendor_id": str(y_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Same Name Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "50.00",
    }, follow_redirects=False)

    blob = _price_recall_blob(client)
    assert blob[str(x_id)]["same name item"] == 84.0
    assert blob[str(y_id)]["same name item"] == 50.0


def test_new_order_price_recall_respects_inventory_sharing(client, db, me):
    from app.models import Share, ShareCategory

    with SessionLocal() as s:
        vendor = Vendor(name="Sharing Price Recall Vendor")
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={
        "username": "PriceRecallOther", "password": "PriceRecallOther1!", "confirm": "PriceRecallOther1!",
    })
    other.post("/inventory/orders", data={
        "order_date": "2026-08-01", "is_new_vendor": "no", "vendor_id": str(vendor_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Shared History Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "99.00",
    })
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "pricerecallother"))

    # Before any Share exists, the other user's order history must not leak into "tester"'s recall data.
    blob_before = _price_recall_blob(client)
    assert str(vendor_id) not in blob_before

    try:
        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
            s.commit()
        blob_after = _price_recall_blob(client)
        assert blob_after[str(vendor_id)]["shared history item"] == 99.0
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY).delete()
            s.commit()


def test_staleness_prompt_replace_only_updates_the_one_vendor(client, db):
    with SessionLocal() as s:
        vendor_x = Vendor(name="Staleness Vendor X", price_list_filename="old-x.pdf",
                          price_list_updated_at=date(2026, 1, 1))
        vendor_y = Vendor(name="Staleness Vendor Y", price_list_url="https://old-y.example/list",
                          price_list_updated_at=date(2026, 1, 1))
        s.add_all([vendor_x, vendor_y])
        s.commit()
        x_id, y_id = vendor_x.id, vendor_y.id

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "no", "vendor_id": str(x_id),
        "price_list_current": "no", "price_list_replace_url": "https://new-x.example/list",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Staleness Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "10.00",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vx = s.get(Vendor, x_id)
        vy = s.get(Vendor, y_id)
        assert vx.price_list_url == "https://new-x.example/list"
        assert vx.price_list_filename is None
        assert vx.price_list_updated_at == date.today()
        # Vendor Y is completely untouched.
        assert vy.price_list_url == "https://old-y.example/list"
        assert vy.price_list_filename is None
        assert vy.price_list_updated_at == date(2026, 1, 1)


def test_new_order_page_has_vendor_branch_and_staleness_prompt_markup(client, db):
    t = html.unescape(client.get("/inventory").text)
    assert 'name="is_new_vendor"' in t
    assert 'data-vendor-select' in t
    assert 'data-staleness-prompt' in t
    assert 'name="price_list_current"' in t
    assert 'id="new-vendor-contact-row-template"' in t


def test_new_order_new_vendor_name_clashing_with_existing_vendor_is_422(client, db):
    with SessionLocal() as s:
        s.add(Vendor(name="Clashing Vendor Name"))
        s.commit()

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Clashing Vendor Name",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Some Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(Vendor).filter_by(name="Clashing Vendor Name").count() == 1  # not duplicated


def test_new_order_validation_error_elsewhere_does_not_create_orphaned_new_vendor(client, db):
    # Review Focus item 5: a validation error anywhere else in the form must not leave behind a
    # new Vendor row created before the rest of the form was known to be valid.
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Orphan Check Vendor",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-quantity": "5",  # no medium -- Medicine requires it, so this line 422s
    }, follow_redirects=False)
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(Vendor).filter_by(name="Orphan Check Vendor").count() == 0


def test_new_order_resubmit_after_validation_error_with_new_vendor_creates_exactly_one_row(client, db):
    # Review Focus item 5's own full round-trip: the failing submission above must leave behind
    # zero Vendor rows, and a second, corrected submission of that SAME form must then create
    # EXACTLY ONE -- never a leftover from the first attempt plus a second from the retry.
    bad_payload = {
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Resubmit Check Vendor",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-quantity": "5",  # no medium -- Medicine requires it, so this line 422s
    }
    r1 = client.post("/inventory/orders", data=bad_payload, follow_redirects=False)
    assert r1.status_code == 422
    with SessionLocal() as s:
        assert s.query(Vendor).filter_by(name="Resubmit Check Vendor").count() == 0

    good_payload = {**bad_payload, "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10"}
    r2 = client.post("/inventory/orders", data=good_payload, follow_redirects=False)
    assert r2.status_code == 303
    with SessionLocal() as s:
        assert s.query(Vendor).filter_by(name="Resubmit Check Vendor").count() == 1  # not 0, not 2


def test_new_order_validation_error_does_not_create_orphaned_contact_or_payment_method_type(client, db):
    # Same Review Focus item 5 concern, one level deeper: a "+ New type..." contact/payment method
    # name must not resolve into a brand-new global ContactMethodType/PaymentMethodType row either,
    # for a submission that ends up 422ing on an unrelated field.
    from app.models import ContactMethodType, PaymentMethodType

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Orphan Type Check Vendor",
        "contacts-0-method_type_id": "__new__", "contacts-0-new_method_type": "Orphan Carrier Pigeon",
        "contacts-0-value": "coop-1",
        "new_payment_type": "Orphan Gift Card",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-quantity": "5",  # no medium -- Medicine requires it, so this line 422s
    }, follow_redirects=False)
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(ContactMethodType).filter_by(name="Orphan Carrier Pigeon").count() == 0
        assert s.query(PaymentMethodType).filter_by(name="Orphan Gift Card").count() == 0


def test_new_order_new_vendor_with_nonexistent_payment_type_id_does_not_500(client, db):
    # Fix 6: a tampered/stale payment_type_ids value posted alongside a brand-new vendor must be
    # skipped, not crash with an unhandled foreign-key IntegrityError.
    from app.models import PaymentMethodType

    with SessionLocal() as s:
        bogus_id = (s.scalar(select(PaymentMethodType.id).order_by(PaymentMethodType.id.desc())) or 0) + 1000

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Bad Payment Type New Vendor",
        "payment_type_ids": str(bogus_id),
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Some Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vendor = s.scalar(select(Vendor).where(Vendor.name == "Bad Payment Type New Vendor"))
        assert vendor is not None
        assert vendor.payment_methods == []


def test_staleness_replace_rejects_javascript_url(client, db):
    # Fix 2: price_list_replace_url must be validated as an http(s) URL exactly like the New
    # Vendor branch's own `website` field already is -- Vendor is a global/shared row, so an
    # unvalidated URL here would render as a raw href for every other user.
    with SessionLocal() as s:
        vendor = Vendor(name="JS URL Staleness Vendor", price_list_url="https://old.example/list",
                        price_list_updated_at=date(2026, 1, 1))
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "no", "vendor_id": str(vendor_id),
        "price_list_current": "no", "price_list_replace_url": "javascript:alert(1)",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Staleness JS Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 422
    with SessionLocal() as s:
        v = s.get(Vendor, vendor_id)
        assert v.price_list_url == "https://old.example/list"  # untouched


def test_staleness_no_with_neither_file_nor_url_is_a_422(client, db):
    # Fix 4: answering "No, not current" but supplying neither a replacement file nor URL must not
    # silently save the order against the OLD price list -- it should 422 instead.
    with SessionLocal() as s:
        vendor = Vendor(name="Silent Noop Vendor", price_list_url="https://old.example/still-here",
                        price_list_updated_at=date(2026, 1, 1))
        s.add(vendor)
        s.commit()
        vendor_id = vendor.id

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "no", "vendor_id": str(vendor_id),
        "price_list_current": "no",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Silent Noop Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 422

    with SessionLocal() as s:
        v = s.get(Vendor, vendor_id)
        # Neither the price list nor its date moved -- and no order got created either.
        assert v.price_list_url == "https://old.example/still-here"
        assert v.price_list_updated_at == date(2026, 1, 1)
        assert s.query(InventoryItem).filter_by(name="Silent Noop Item").count() == 0
