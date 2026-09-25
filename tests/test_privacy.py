"""Each user sees only their own inventory and protocols, unless the owner explicitly shared it."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, Peptide, Protocol, User

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
    client.post("/inventory", data={"name": "My BPC vial", "count": "2", "category": "Supply"}, files={"coa": ("c.png", PNG, "image/png")})
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


from app.models import Share, ShareCategory


def _grant(owner_id: int, grantee_id: int, category: ShareCategory) -> None:
    with SessionLocal() as s:
        s.add(Share(owner_id=owner_id, grantee_id=grantee_id, category=category))
        s.commit()


def _revoke(owner_id: int, grantee_id: int, category: ShareCategory) -> None:
    with SessionLocal() as s:
        s.query(Share).filter_by(owner_id=owner_id, grantee_id=grantee_id, category=category).delete()
        s.commit()


def test_shared_inventory_item_appears_tagged_and_is_read_only(client, other, me):
    # `other` owns a throwaway item first, so the shared item's id doesn't coincidentally equal
    # the owner's user id (1) -- a bug that looks up the owner by item id instead of owner id
    # would otherwise pass by rowid coincidence.
    other.post("/inventory", data={"name": "Other's own vial", "count": "1", "category": "Supply"})
    # Supply items have no Order and thus no COA (COA is per-order, Medicine/BAC Water only, since
    # Task 1/5) -- the upload here is a no-op, and the old item-scoped `/inventory/{id}/coa` route
    # this test used to check no longer exists (COA now lives at `/inventory/{id}/orders/{oid}/coa`).
    client.post("/inventory", data={"name": "My BPC vial", "count": "2", "category": "Supply"}, files={"coa": ("c.png", PNG, "image/png")})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "My BPC vial"))
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    assert item_id != me  # sanity: guards against the exact bug this test targets

    _grant(me, other_id, ShareCategory.INVENTORY)
    try:
        t = other.get("/inventory").text
        assert "My BPC vial" in t
        assert "Shared by Tester" in t  # tagged with the owner's username, not a blank/wrong one
        assert f'action="/inventory/{item_id}"' not in t  # no edit form for a shared row
        assert f'action="/inventory/{item_id}/delete"' not in t

        assert other.post(f"/inventory/{item_id}", data={"name": "Hacked"}).status_code == 404
        assert other.post(f"/inventory/{item_id}/delete").status_code == 404
    finally:
        _revoke(me, other_id, ShareCategory.INVENTORY)


def test_third_party_never_sees_shared_inventory(client, other, mine, me):
    """Sharing with `other` must never leak to a third account that wasn't granted anything."""
    item_id, _ = mine
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    third = TestClient(app, follow_redirects=False)
    third.post("/notice", data={"understand": "1"})
    third.post("/register", data={"username": "ThirdParty", "password": "Third1!aa", "confirm": "Third1!aa"})

    _grant(me, other_id, ShareCategory.INVENTORY)  # shared with `other`, not with `third`
    try:
        assert "My BPC vial" not in third.get("/inventory").text
        assert third.get(f"/inventory/{item_id}/coa").status_code == 404
    finally:
        _revoke(me, other_id, ShareCategory.INVENTORY)


def test_revoking_inventory_share_removes_visibility_immediately(client, other, mine, me):
    item_id, _ = mine
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(me, other_id, ShareCategory.INVENTORY)
    assert "My BPC vial" in other.get("/inventory").text
    _revoke(me, other_id, ShareCategory.INVENTORY)
    assert "My BPC vial" not in other.get("/inventory").text


def test_shared_protocol_appears_under_shared_tab_only(client, other, mine, me):
    _, proto_id = mine
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(me, other_id, ShareCategory.PERSONAL_DATA)
    try:
        t = other.get("/protocols").text
        assert "My private protocol" in t
        assert "Tester" in t  # tagged with the owner's username
        assert 'id="tab-shared"' in t
        # Never merged into the grantee's own Active/Saved sections.
        assert t.count("My private protocol") == 1
        assert "Dose not set" in t  # dose/frequency detail is shown, not just the peptide name

        for action in ("pause", "resume", "end", "delete"):
            assert other.post(f"/protocols/{proto_id}/{action}").status_code == 404, action
        assert other.get(f"/protocols/{proto_id}/repeat").status_code == 404
        assert other.get(f"/protocols/{proto_id}/edit").status_code == 404
    finally:
        _revoke(me, other_id, ShareCategory.PERSONAL_DATA)


def test_third_party_never_sees_shared_protocol(client, other, mine, me):
    _, proto_id = mine
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    third = TestClient(app, follow_redirects=False)
    third.post("/notice", data={"understand": "1"})
    third.post("/register", data={"username": "ThirdProto", "password": "Third1!aa", "confirm": "Third1!aa"})

    _grant(me, other_id, ShareCategory.PERSONAL_DATA)
    try:
        assert "My private protocol" not in third.get("/protocols").text
        assert third.get(f"/protocols/{proto_id}/edit").status_code == 404
    finally:
        _revoke(me, other_id, ShareCategory.PERSONAL_DATA)


def test_shared_protocol_not_on_calendar(client, other, mine, me):
    _, proto_id = mine
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(me, other_id, ShareCategory.PERSONAL_DATA)
    try:
        assert "My private protocol" not in other.get("/calendar").text
    finally:
        _revoke(me, other_id, ShareCategory.PERSONAL_DATA)


def test_admin_gets_no_bypass_without_an_explicit_grant(client, other, me):
    """`client` (Tester) is the admin (first-ever account). Admin status must never substitute
    for a Share grant -- the whole point of Sharing is that even the admin can't see what wasn't
    shared with them."""
    other.post("/inventory", data={"name": "Other Private Vial", "count": "1", "category": "Supply"},
              files={"coa": ("c.png", PNG, "image/png")})
    other.post("/protocols", data={
        "name": "Other Private Protocol", "start_date": "2026-09-01", "goal": ["muscle-recovery"],
        "items-0-peptide_id": str(peptide_id("BPC-157"))})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Other Private Vial"))

    assert "Other Private Vial" not in client.get("/inventory").text
    assert "Other Private Protocol" not in client.get("/protocols").text
    assert client.get(f"/inventory/{item_id}/coa").status_code == 404

    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(other_id, me, ShareCategory.INVENTORY)
    try:
        assert "Other Private Vial" in client.get("/inventory").text
    finally:
        _revoke(other_id, me, ShareCategory.INVENTORY)


def test_inventory_share_does_not_leak_protocols(client, other, mine, me):
    """Categories are independent: sharing Inventory must not expose Personal Data."""
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(me, other_id, ShareCategory.INVENTORY)
    try:
        assert "My private protocol" not in other.get("/protocols").text
    finally:
        _revoke(me, other_id, ShareCategory.INVENTORY)


def test_personal_data_share_does_not_leak_inventory(client, other, mine, me):
    """Categories are independent: sharing Personal Data must not expose Inventory."""
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "other"))
    _grant(me, other_id, ShareCategory.PERSONAL_DATA)
    try:
        assert "My BPC vial" not in other.get("/inventory").text
    finally:
        _revoke(me, other_id, ShareCategory.PERSONAL_DATA)


def test_active_vial_not_visible_without_inventory_grant(client, other, me):
    from datetime import date

    from app.models import ActiveVial

    client.post("/inventory", data={"name": "AV Privacy Item", "category": "Medicine", "vial_size_mg": "10",
                                    "medium": "Lyophilized", "quantity": "1", "order_date": "2026-08-01"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Privacy Item"))
        s.get(InventoryItem, item_id).orders[0].arrival_date = date(2026, 8, 10)  # must have arrived to reconstitute
        s.commit()
    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert "AV Privacy Item" not in other.get("/inventory").text
