import csv
import io
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Category, InventoryItem, Peptide, Protocol


def peptide_id(name):
    with SessionLocal() as s:
        return s.scalar(select(Peptide.id).where(Peptide.name == name))


@pytest.fixture(scope="module")
def other():
    """A second person with their own account, shared across this file's tests (like test_privacy.py)."""
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    c.post("/register", data={"username": "BackupOther", "password": "Back0up!", "confirm": "Back0up!"})
    return c


def make_sample_data(client):
    r = client.post("/inventory", data={"name": "BPC-157", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "quantity": "5", "order_date": "2026-08-01", "vendor": "Acme", "cost": "45.99", "notes": "test note"})
    assert r.status_code in (200, 303), f"Inventory POST failed: {r.status_code}"
    r = client.post("/protocols", data={
        "name": "Heal", "start_date": "2026-09-01", "goal": ["muscle-recovery"], "titration": "1",
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
        "items-0-steps-0-start_week": "1", "items-0-steps-0-end_week": "4", "items-0-steps-0-dose": "125",
        "items-0-steps-1-start_week": "5", "items-0-steps-1-dose": "250",
    })
    assert r.status_code in (200, 303), f"Protocols POST failed: {r.status_code}"


def test_backup_page_has_export_and_import_links(client):
    r = client.get("/backup")
    assert r.status_code == 200
    for link in ("/backup/export.json", "/backup/export/inventory.csv"):
        assert link in r.text
    assert "adds" in r.text.lower() or "additive" in r.text.lower()  # explains it doesn't overwrite


def test_json_export_shape_and_content(client, db):
    make_sample_data(client)
    r = client.get("/backup/export.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    data = json.loads(r.content)

    [item] = data["inventory"]
    assert item["name"] == "BPC-157" and item["medium"] == "Lyophilized" and item["vial_size_mg"] == 10
    assert item["notes"] == "test note"
    # vendor and cost are now on the Order for Medicine items
    [order] = item["orders"]
    assert order["vendor"] == "Acme" and order["cost"] == 45.99 and order["quantity"] == 5

    [proto] = data["protocols"]
    assert proto["name"] == "Heal" and proto["goals"] == ["muscle-recovery"] and proto["titration_enabled"]
    [pitem] = proto["items"]
    assert pitem["peptide"] == "BPC-157" and pitem["dose"] == 250 and pitem["dose_unit"] == "mcg"
    assert [(s["start_week"], s["end_week"], s["dose"]) for s in pitem["steps"]] == [(1, 4, 125.0), (5, None, 250.0)]


def test_csv_export_headers_and_row(client, db):
    make_sample_data(client)
    r = client.get("/backup/export/inventory.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    lines = r.text.split('\n')
    # First section is items, second section is orders (separated by blank line)
    split_idx = next(i for i, line in enumerate(lines) if line.strip() == '')
    item_lines = lines[:split_idx]
    order_lines = lines[split_idx + 1:]  # skip blank row

    item_rows = list(csv.DictReader(io.StringIO('\n'.join(item_lines))))
    assert item_rows[0]["Name"] == "BPC-157"

    order_rows = list(csv.DictReader(io.StringIO('\n'.join(order_lines))))
    assert order_rows[0]["Item"] == "BPC-157" and order_rows[0]["Vendor"] == "Acme" and order_rows[0]["Cost"] == "45.99"


def test_export_excludes_other_users_data(client, db, other):
    make_sample_data(client)
    other.post("/inventory", data={"name": "Not Mine", "medium": "Lyophilized", "vial_size_mg": "1"})
    data = json.loads(client.get("/backup/export.json").content)
    assert all(i["name"] != "Not Mine" for i in data["inventory"])


def test_import_creates_owned_rows_for_current_user(client, db, other):
    make_sample_data(client)
    exported = client.get("/backup/export.json").content
    r = other.post("/backup/import", files={"file": ("backup.json", exported, "application/json")},
                   follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        other_user_id = s.scalar(select(Peptide.id).where(Peptide.name == "BPC-157"))  # sanity peptide exists
        assert other_user_id is not None
        items = list(other.get("/api/inventory").json())
        assert any(i["name"] == "BPC-157" for i in items)
        protos = other.get("/api/protocols").json()
        assert any(p["name"] == "Heal" and p["goals"] == ["muscle-recovery"] for p in protos)


def test_import_is_additive_only_never_touches_existing_rows(client, db, other):
    make_sample_data(client)
    with SessionLocal() as s:
        before_inv = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "BPC-157"))
        before_proto = s.scalar(select(Protocol.id).where(Protocol.name == "Heal"))
    exported = client.get("/backup/export.json").content

    # Import into the SAME account: existing rows keep their ids/values; a second copy is added.
    client.post("/backup/import", files={"file": ("backup.json", exported, "application/json")})
    with SessionLocal() as s:
        assert s.get(InventoryItem, before_inv).name == "BPC-157"  # untouched
        assert s.get(Protocol, before_proto).name == "Heal"  # untouched
        assert s.query(InventoryItem).filter_by(name="BPC-157").count() == 2
        assert s.query(Protocol).filter_by(name="Heal").count() == 2

    other.post("/inventory", data={"name": "Other's own item", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "1",
                                   "quantity": "1", "order_date": "2026-08-01"})
    other.post("/backup/import", files={"file": ("backup.json", exported, "application/json")})
    assert any(i["name"] == "Other's own item" for i in other.get("/api/inventory").json())


def test_import_skips_unknown_goal_without_failing(client, db):
    payload = {"inventory": [], "protocols": [{"name": "Weird", "start_date": "2026-09-01",
              "end_date": None, "notes": None, "titration_enabled": False, "goals": ["not-a-real-goal"],
              "items": []}]}
    r = client.post("/backup/import", files={"file": ("b.json", json.dumps(payload), "application/json")},
                    follow_redirects=False)
    assert r.status_code == 303
    protos = client.get("/api/protocols").json()
    weird = next(p for p in protos if p["name"] == "Weird")
    assert weird["goals"] == []  # unknown goal dropped, not fatal


def test_import_rejects_malformed_file(client, db):
    r = client.post("/backup/import", files={"file": ("b.json", b"not json", "application/json")})
    assert r.status_code == 422


def test_json_export_includes_orders_and_category(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    payload = client.get("/backup/export.json").json()
    item = next(i for i in payload["inventory"] if i["name"] == "Retatrutide")
    assert item["category"] == "Medicine"
    assert item["orders"][0]["quantity"] == 10 and item["orders"][0]["tracking_number"] == "LY123"


def test_json_import_recreates_item_and_its_orders(client, db):
    payload = {
        "inventory": [{
            "name": "Imported Peptide", "category": "Medicine", "medium": "Lyophilized",
            "vial_size_mg": 10, "vial_size_unit": "mg",
            "orders": [{"quantity": 5, "order_date": "2026-08-01", "arrival_date": "2026-08-10",
                       "tracking_number": "LY999"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Imported Peptide"))
        assert item.category == Category.MEDICINE
        assert item.order_items[0].quantity == 5 and item.order_items[0].order.tracking_number == "LY999"
        assert item.available_count == 5


def test_json_import_tolerates_old_backup_shape_with_no_orders_key(client, db):
    payload = {"inventory": [{"name": "Old Supply", "category": "Supply", "count": 3}]}
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Old Supply"))
        assert item.category == Category.SUPPLY and item.available_count == 3


def test_json_export_import_round_trip_preserves_available_count(client, db):
    # 10 arrived, 6 reconstituted -- available_count should be 4 both before and after a
    # round-trip export/import (reconstituted_count must survive the trip, or it "heals" back to 10).
    client.post("/inventory", data={
        "name": "Semaglutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "arrival_date": "2026-08-05",
    })
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Semaglutide"))
        item.reconstituted_count = 6
        s.commit()
        assert item.available_count == 4

    payload = client.get("/backup/export.json").json()
    exported = next(i for i in payload["inventory"] if i["name"] == "Semaglutide")
    assert exported["reconstituted_count"] == 6 and exported["sold_count"] == 0

    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        imported = s.scalar(select(InventoryItem).where(
            InventoryItem.name == "Semaglutide", InventoryItem.id != item.id))
        assert imported.reconstituted_count == 6
        assert imported.available_count == 4


def test_csv_export_includes_reconstituted_and_sold_columns(client, db):
    client.post("/inventory", data={
        "name": "Tirzepatide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "arrival_date": "2026-08-05",
    })
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Tirzepatide"))
        item.reconstituted_count = 2
        item.sold_count = 1
        s.commit()
    r = client.get("/backup/export/inventory.csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    header = rows[0]
    assert "Reconstituted" in header and "Sold" in header
    row = next(row for row in rows if row and row[0] == "Tirzepatide")
    assert row[header.index("Reconstituted")] == "2"
    assert row[header.index("Sold")] == "1"


def test_json_export_includes_sale_history(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "arrival_date": "2026-08-05",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "3", "price": "150.00"},
               follow_redirects=False)
    payload = client.get("/backup/export.json").json()
    item = next(i for i in payload["inventory"] if i["name"] == "Retatrutide")
    assert item["sales"] == [{"quantity": 3, "sale_date": "2026-09-25", "price": 150.0}]


def test_json_import_recreates_sale_history(client, db):
    payload = {
        "inventory": [{
            "name": "Imported Peptide", "category": "Medicine", "medium": "Lyophilized",
            "vial_size_mg": 10, "vial_size_unit": "mg", "sold_count": 3,
            "orders": [{"quantity": 5, "order_date": "2026-08-01", "arrival_date": "2026-08-10"}],
            "sales": [{"quantity": 3, "sale_date": "2026-09-25", "price": 150.0}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Imported Peptide"))
        assert item.sold_count == 3
        [sale] = item.sales
        assert sale.quantity == 3 and sale.price == 150.0 and sale.sale_date == date(2026, 9, 25)


def test_json_import_tolerates_old_backup_shape_with_no_sales_key(client, db):
    payload = {"inventory": [{"name": "Old Supply", "category": "Supply", "count": 3}]}
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Old Supply"))
        assert item.sales == []


def test_json_export_includes_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 9
        s.commit()

    payload = client.get("/backup/export.json").json()
    item = next(i for i in payload["inventory"] if i["name"] == "Retatrutide")
    assert item["orders"][0]["received_quantity"] == 9
    assert item["orders"][0]["arrival_date"] == "2026-08-10"


def test_json_import_recreates_order_with_received_quantity(client, db):
    payload = {
        "inventory": [{
            "name": "Imported Peptide", "category": "Medicine", "medium": "Lyophilized",
            "vial_size_mg": 10, "vial_size_unit": "mg",
            "orders": [{"quantity": 10, "received_quantity": 8, "order_date": "2026-08-01",
                       "arrival_date": "2026-08-10", "tracking_number": "LY999"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Imported Peptide"))
        li = item.order_items[0]
        assert li.quantity == 10 and li.received_quantity == 8
        assert li.order.arrival_date == date(2026, 8, 10)
        assert item.available_count == 8


def test_json_import_tolerates_backup_with_no_received_quantity_key(client, db):
    payload = {
        "inventory": [{
            "name": "Old Style", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": 10,
            "vial_size_unit": "mg",
            "orders": [{"quantity": 10, "order_date": "2026-08-01", "arrival_date": "2026-08-10"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Old Style"))
        # No received_quantity in the file -- since arrival_date is set, treat it as fully received
        # (matches this order's pre-multi-item-orders meaning: it already counted as available).
        assert item.order_items[0].received_quantity == 10
        assert item.available_count == 10


def test_csv_export_includes_sales_section(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "arrival_date": "2026-08-05",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    client.post(f"/inventory/{item_id}/sales", data={"sale_date": "2026-09-25", "quantity": "3", "price": "150.00"},
               follow_redirects=False)
    r = client.get("/backup/export/inventory.csv")
    # Sections are items, orders, sales -- separated by blank lines.
    blank_indices = [i for i, line in enumerate(r.text.split('\n')) if line.strip() == '']
    lines = r.text.split('\n')
    sale_lines = lines[blank_indices[1] + 1:]
    sale_rows = list(csv.DictReader(io.StringIO('\n'.join(sale_lines))))
    assert sale_rows[0]["Item"] == "Retatrutide" and sale_rows[0]["Quantity"] == "3" and sale_rows[0]["Price"] == "150.0"
