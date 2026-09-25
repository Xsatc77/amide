import html

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, InventoryItem, User


def text(r) -> str:
    return html.unescape(r.text)


@pytest.fixture
def lyo_item(client):
    """A Lyophilized inventory item this test user owns, with 2 in stock."""
    client.post("/inventory", data={"name": "AV Test Peptide", "count": "2", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        return s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Test Peptide"))


def test_calculator_inventory_select_uses_item_id_as_value(client, db, lyo_item):
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}" data-vial-mg="10">AV Test Peptide (10 mg)</option>' in t


def test_calculator_prefills_from_inventory_item_id_query_param(client, db, lyo_item):
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}"))
    assert f'<option value="{lyo_item}" data-vial-mg="10" selected>' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]  # the vial-amount field is prefilled


def test_reconstitute_commit_creates_vial_and_decrements_count(client, db, lyo_item, me):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"

    with SessionLocal() as s:
        item = s.get(InventoryItem, lyo_item)
        assert item.count == 1  # started at 2
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == lyo_item))
        assert vial is not None and vial.owner_id == me
        assert vial.concentration_mg_ml == pytest.approx(5.0)  # 10mg / 2mL
        assert vial.doses_total == 40  # 10mg // (250mcg = 0.25mg) == 40
        assert vial.discarded_at is None


def test_reconstitute_commit_refuses_when_count_is_zero(client, db, me):
    client.post("/inventory", data={"name": "Empty Stock", "count": "0", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Empty Stock"))

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0


def test_reconstitute_commit_requires_ownership(client, db, lyo_item):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVOther", "password": "AVOther1!", "confirm": "AVOther1!"})
    r = other.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404
