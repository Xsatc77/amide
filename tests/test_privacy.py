"""Each user sees only their own inventory and protocols."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, Peptide, Protocol

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(scope="module")
def other():
    """A second person with their own account."""
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    assert c.post("/register", data={"username": "Other", "password": "Oth3r!", "confirm": "Oth3r!"}).status_code == 303
    return c


def peptide_id(name):
    with SessionLocal() as s:
        return s.scalar(select(Peptide.id).where(Peptide.name == name))


@pytest.fixture
def mine(client):
    """An inventory item (with COA) and a protocol owned by the main test user."""
    client.post("/inventory", data={"name": "My BPC vial", "count": "2"}, files={"coa": ("c.png", PNG, "image/png")})
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "My BPC vial"))
    client.post("/protocols", data={
        "name": "My private protocol", "start_date": "2026-09-01", "goal": ["muscle-recovery"],
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-inventory_item_id": str(item.id)})
    with SessionLocal() as s:
        proto = s.scalar(select(Protocol).where(Protocol.name == "My private protocol"))
    return item.id, proto.id


def test_new_rows_are_owned_by_their_creator(client, me, mine):
    item_id, proto_id = mine
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).owner_id == me
        assert s.get(Protocol, proto_id).owner_id == me


def test_other_users_data_is_invisible(client, other, mine):
    item_id, proto_id = mine
    assert "My BPC vial" not in other.get("/inventory").text
    assert "My private protocol" not in other.get("/protocols").text
    assert all(r["id"] != item_id for r in other.get("/api/inventory").json())
    assert all(r["id"] != proto_id for r in other.get("/api/protocols").json())
    for path in (f"/inventory/{item_id}/coa", f"/api/inventory/{item_id}", f"/protocols/{proto_id}/edit",
                 f"/protocols/{proto_id}/repeat", f"/api/protocols/{proto_id}"):
        assert other.get(path).status_code == 404, path

    # The owner still sees everything.
    assert "My BPC vial" in client.get("/inventory").text
    assert "My private protocol" in client.get("/protocols").text


def test_other_user_cannot_change_or_delete(client, other, mine):
    item_id, proto_id = mine
    assert other.post(f"/inventory/{item_id}", data={"name": "Hacked"}).status_code == 404
    assert other.post(f"/inventory/{item_id}/delete").status_code == 404
    for action in ("pause", "resume", "end", "delete"):
        assert other.post(f"/protocols/{proto_id}/{action}").status_code == 404, action
    assert other.post(f"/protocols/{proto_id}", data={"name": "Hacked"}).status_code == 404
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).name == "My BPC vial"
        p = s.get(Protocol, proto_id)
        assert p.name == "My private protocol" and not p.paused and p.ended_on is None


def test_cannot_link_someone_elses_inventory(other, mine):
    item_id, _ = mine
    page = other.get("/protocols/new").text
    data = json.loads(re.search(r'id="builder-data">(.*?)</script>', page, re.S).group(1))
    assert all(i["id"] != item_id for i in data["inventory"])
    r = other.post("/protocols", data={
        "name": "Sneaky", "start_date": "2026-09-01", "goal": ["muscle-recovery"],
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-inventory_item_id": str(item_id)})
    assert r.status_code == 422


def test_library_is_shared_but_used_in_is_private(client, other, mine):
    bpc = peptide_id("BPC-157")
    assert other.get(f"/library/{bpc}").status_code == 200  # library is shared
    assert "My private protocol" not in other.get(f"/library/{bpc}").text
    assert "My private protocol" in client.get(f"/library/{bpc}").text
