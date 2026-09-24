import csv
import io
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, Peptide, Protocol


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
    client.post("/inventory", data={"name": "BPC-157", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "vendor": "Acme", "cost": "45.99", "notes": "test note"})
    client.post("/protocols", data={
        "name": "Heal", "start_date": "2026-09-01", "goal": ["muscle-recovery"], "titration": "1",
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
        "items-0-steps-0-start_week": "1", "items-0-steps-0-end_week": "4", "items-0-steps-0-dose": "125",
        "items-0-steps-1-start_week": "5", "items-0-steps-1-dose": "250",
    })


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
    assert item["vendor"] == "Acme" and item["cost"] == 45.99 and item["notes"] == "test note"

    [proto] = data["protocols"]
    assert proto["name"] == "Heal" and proto["goals"] == ["muscle-recovery"] and proto["titration_enabled"]
    [pitem] = proto["items"]
    assert pitem["peptide"] == "BPC-157" and pitem["dose"] == 250 and pitem["dose_unit"] == "mcg"
    assert [(s["start_week"], s["end_week"], s["dose"]) for s in pitem["steps"]] == [(1, 4, 125.0), (5, None, 250.0)]


def test_csv_export_headers_and_row(client, db):
    make_sample_data(client)
    r = client.get("/backup/export/inventory.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows[0]["Name"] == "BPC-157" and rows[0]["Vendor"] == "Acme" and rows[0]["Cost"] == "45.99"


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

    other.post("/inventory", data={"name": "Other's own item", "medium": "Lyophilized", "vial_size_mg": "1"})
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
