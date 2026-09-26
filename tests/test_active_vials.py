import html
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, InventoryItem, User


def text(r) -> str:
    return html.unescape(r.text)


def _create_item(name: str, category: str = "Medicine", medium: str = None, vial_size_mg: float = None,
                 vial_size_unit: str = "mg", quantity: int = 1, order_date: str = "2026-08-01",
                 arrival_date: date = None, uid: int = None) -> int:
    """Create an inventory item directly in the database (bypassing the broken POST endpoint).
    Returns the item_id."""
    from app.models import Category, Medium, DoseUnit, Order, OrderItem, InventoryItem
    with SessionLocal() as s:
        if uid is None:
            uid = s.scalar(select(User.id))

        cat = Category(category)
        med = Medium(medium) if medium else None
        unit = DoseUnit(vial_size_unit)

        # For Medicine/BAC Water, count is vestigial and stays at 0; for Supply it's the actual count
        item_count = 1 if cat == Category.SUPPLY else 0

        item = InventoryItem(
            name=name, category=cat, medium=med, vial_size_mg=vial_size_mg,
            vial_size_unit=unit, owner_id=uid, count=item_count
        )
        s.add(item)
        s.flush()  # to get the item.id

        # Create an order for non-Supply items (only if quantity > 0, since DB constraint requires it)
        if cat != Category.SUPPLY and quantity > 0:
            order = Order(order_date=date.fromisoformat(order_date), arrival_date=arrival_date)
            s.add(order)
            s.flush()
            order.items.append(OrderItem(
                inventory_item_id=item.id, quantity=quantity,
                received_quantity=quantity if arrival_date else None,
            ))
        s.commit()
        return item.id


@pytest.fixture
def lyo_item(db, me):
    """A Lyophilized inventory item this test user owns, with 2 in stock."""
    return _create_item("AV Test Peptide", category="Medicine", medium="Lyophilized",
                        vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)


@pytest.fixture
def mcg_item(db, me):
    """A Lyophilized inventory item labeled in mcg, not mg -- Reconstitute must not treat its
    vial_size_mg number as milligrams (that would be a 1000x concentration error)."""
    return _create_item("AV Mcg Peptide", category="Medicine", medium="Lyophilized",
                        vial_size_mg=500, vial_size_unit="mcg", quantity=2,
                        arrival_date=date(2026, 8, 10), uid=me)


def test_calculator_inventory_select_uses_item_id_as_value(client, db, lyo_item):
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}" data-vial-mg="10">AV Test Peptide (10 mg)</option>' in t


def test_calculator_ignores_vial_mg_override_when_item_selected(client, db, lyo_item):
    """A bookmarked/forged ?vial_mg= must never win over the selected item's real amount --
    otherwise the confirmation modal (built from the live field) can save a different
    concentration than what the item's own vial size implies."""
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}&vial_mg=999"))
    assert 'id="calc-vial"' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]
    assert 'value="999"' not in t


def test_calculator_prefills_from_inventory_item_id_query_param(client, db, lyo_item):
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}"))
    assert f'<option value="{lyo_item}" data-vial-mg="10" selected>' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]  # the vial-amount field is prefilled


def test_reconstitute_commit_creates_vial_and_increments_reconstituted_count(client, db, lyo_item, me):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"

    with SessionLocal() as s:
        item = s.get(InventoryItem, lyo_item)
        assert item.reconstituted_count == 1  # incremented from 0
        assert item.count == 0  # untouched -- vestigial for Medicine
        assert item.available_count == 1  # 2 arrived - 1 reconstituted
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == lyo_item))
        assert vial is not None and vial.owner_id == me
        assert vial.concentration_mg_ml == pytest.approx(5.0)  # 10mg / 2mL
        assert vial.doses_total == 40  # 10mg // (250mcg = 0.25mg) == 40
        assert vial.discarded_at is None


def test_reconstitute_commit_refuses_when_count_is_zero(client, db, me):
    item_id = _create_item("Empty Stock", category="Medicine", medium="Lyophilized",
                           vial_size_mg=10, quantity=0, arrival_date=date(2026, 8, 10), uid=me)

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/calculator?inventory_item_id={item_id}")
    assert "reconstitute_error=" in r.headers["location"]
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0

    t = text(client.get(r.headers["location"]))
    assert "none left in stock" in t


def test_calculator_page_shows_count_0_and_excludes_from_dropdown_data(client, db, me):
    # Create an item with no arrived orders (available_count == 0)
    item_id = _create_item("Empty Stock 2", category="Medicine", medium="Lyophilized",
                           vial_size_mg=10, quantity=1, arrival_date=None, uid=me)  # no arrival_date = not arrived
    t = text(client.get("/calculator"))
    # Items with available_count <= 0 should not appear in the dropdown data
    assert f'<option value="{item_id}"' not in t


def test_reconstitute_commit_requires_ownership(client, db, lyo_item):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVOther", "password": "AVOther1!", "confirm": "AVOther1!"})
    r = other.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404


def test_calculator_page_excludes_non_mg_inventory_items(client, db, lyo_item, mcg_item):
    """vial_size_mg is only ever milligrams to the reconstitution math; an mcg- or IU-labeled
    item would silently compute a wildly wrong concentration if offered here."""
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}"' in t
    assert f'<option value="{mcg_item}"' not in t


def test_reconstitute_commit_rejects_non_mg_item(client, db, mcg_item):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(mcg_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 404
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=mcg_item).count() == 0
        item = s.get(InventoryItem, mcg_item)
        assert item.reconstituted_count == 0  # untouched
        assert item.count == 0  # untouched -- vestigial for Medicine


def test_reconstitute_dropdown_excludes_non_mg_items(client, db, mcg_item):
    """Items with vial_size_unit != mg are excluded from the calculator dropdown (1000x concentration error prevention)."""
    # Test the calculator dropdown - mcg items should not appear there
    t = text(client.get("/calculator"))
    assert f'<option value="{mcg_item}"' not in t


def test_reconstitute_dropdown_excludes_zero_stock_items(client, db, lyo_item, me):
    """Items with available_count == 0 (no arrived orders) are excluded from the calculator dropdown."""
    zero_id = _create_item("Zero Stock", category="Medicine", medium="Lyophilized",
                           vial_size_mg=5, quantity=0, arrival_date=date(2026, 8, 10), uid=me)
    # Test the calculator dropdown - items with 0 available_count should not appear
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}"' in t  # lyo_item has available_count > 0
    assert f'<option value="{zero_id}"' not in t  # zero_id has available_count == 0


def test_reconstitute_creates_vial_for_correct_item_only(client, db, lyo_item, me):
    """Reconstitute creates an ActiveVial only for the exact item specified, not others with same name."""
    other_item_id = _create_item("AV Test Peptide Two", category="Medicine", medium="Lyophilized",
                                 vial_size_mg=10, quantity=1, arrival_date=date(2026, 8, 10), uid=me)

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        # Verify that an ActiveVial was created for lyo_item
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == lyo_item))
        assert vial is not None
        # And no vial was created for other_item_id
        assert s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == other_item_id)) is None


from datetime import datetime, timedelta


def _reconstitute(client, item_id, discard_by="2026-12-31"):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": discard_by,
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"
    with SessionLocal() as s:
        return s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == item_id))


def test_reconstitute_vial_stores_correct_data(client, db, lyo_item):
    """Reconstitute correctly computes and stores concentration and doses_total in the ActiveVial."""
    vial_id = _reconstitute(client, lyo_item)
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        item = s.get(InventoryItem, lyo_item)
        assert vial is not None
        assert vial.inventory_item_id == lyo_item
        assert vial.concentration_mg_ml == pytest.approx(5.0)  # 10mg / 2mL
        assert vial.doses_total == 40  # 10mg // (250mcg = 0.25mg) == 40
        assert item.name == "AV Test Peptide"


def test_discard_sets_discarded_at_and_leaves_active_list(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item)
    r = client.post(f"/active-vials/{vial_id}/discard", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        assert v is not None and v.discarded_at is not None  # row survives, but marked as discarded


def test_expiry_popup_fires_once_then_throttles_24_hours(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")  # already expired
    with SessionLocal() as s:
        # Before snoozing, last_discard_prompt_at should be None
        assert s.get(ActiveVial, vial_id).last_discard_prompt_at is None

    client.post(f"/active-vials/{vial_id}/snooze-prompt", follow_redirects=False)
    with SessionLocal() as s:
        # After snoozing, last_discard_prompt_at should be set
        assert s.get(ActiveVial, vial_id).last_discard_prompt_at is not None


def test_expiry_popup_does_not_repeat_within_24_hours(client, db, lyo_item):
    from datetime import datetime as dt, timedelta
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = dt.utcnow() - timedelta(hours=1)
        s.commit()

    # Within 24 hours, last_discard_prompt_at should remain recent
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        assert v.last_discard_prompt_at is not None
        assert (dt.utcnow() - v.last_discard_prompt_at).total_seconds() <= 3601  # about 1 hour ago (with some timing slack)

    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = dt.utcnow() - timedelta(hours=25)
        s.commit()

    # After 24 hours, last_discard_prompt_at should be older
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        assert v.last_discard_prompt_at is not None
        assert (dt.utcnow() - v.last_discard_prompt_at).total_seconds() > 86400  # more than 24 hours ago


def test_expiry_prompt_names_each_expired_vial(client, db, lyo_item, me):
    """With more than one expired vial, the popup must say which one it's about -- the JS queues
    them one at a time using this per-card name and the dialog's data-fill="item-name" span."""
    second_id = _create_item("AV Second Peptide", category="Medicine", medium="Lyophilized",
                             vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)
    vial_id_1 = _reconstitute(client, lyo_item, discard_by="2020-01-01")
    vial_id_2 = _reconstitute(client, second_id, discard_by="2020-01-01")

    # Verify both expired vials were created with the correct item associations
    with SessionLocal() as s:
        vial1 = s.get(ActiveVial, vial_id_1)
        vial2 = s.get(ActiveVial, vial_id_2)
        item1 = s.get(InventoryItem, vial1.inventory_item_id)
        item2 = s.get(InventoryItem, vial2.inventory_item_id)
        assert item1.name == "AV Test Peptide"
        assert item2.name == "AV Second Peptide"
        assert vial1.discard_by < date.today()  # expired
        assert vial2.discard_by < date.today()  # expired


def test_shared_active_vial_discard_restricted_to_owner(client, db, lyo_item, me):
    """Grantees cannot discard active vials they don't own; discard endpoint enforces ownership check."""
    from app.models import Share, ShareCategory

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVShared", "password": "AVShared1!", "confirm": "AVShared1!"})
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "avshared"))
        s.add(Share(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY))
        s.commit()

    try:
        vial_id = _reconstitute(client, lyo_item)
        # Verify the vial was created (exists in DB)
        with SessionLocal() as s:
            vial = s.get(ActiveVial, vial_id)
            assert vial is not None
            assert vial.inventory_item_id == lyo_item

        # Attempt to discard as other user (grantee) should fail (they don't own it)
        assert other.post(f"/active-vials/{vial_id}/discard", follow_redirects=False).status_code == 404
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY).delete()
            s.commit()

    assert other.post(f"/active-vials/{vial_id}/discard").status_code == 404


def test_reconstitute_only_counts_arrived_orders_not_in_transit_ones(client, db, me):
    item_id = _create_item("AV Order Test", category="Medicine", medium="Lyophilized",
                           vial_size_mg=10, quantity=10, uid=me)
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).available_count == 0  # first order hasn't arrived

    t = text(client.get("/calculator"))
    assert f'<option value="{item_id}"' not in t  # count 0 -- excluded from the dropdown, same as before

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and "reconstitute_error" in r.headers["location"]

    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0


def test_reconstitute_commit_increments_reconstituted_count_not_raw_count(client, db, me):
    item_id = _create_item("AV Reconstituted Count", category="Medicine", medium="Lyophilized",
                           vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"

    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.reconstituted_count == 1
        assert item.count == 0  # untouched -- vestigial for Medicine
        assert item.available_count == 1  # 2 arrived - 1 reconstituted


def test_bac_water_item_never_reconstitutable(client, db, me):
    item_id = _create_item("AV BAC Water", category="BAC Water", quantity=4,
                           arrival_date=date(2026, 8, 10), uid=me)
    t = text(client.get("/calculator"))
    assert f'<option value="{item_id}"' not in t
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404
