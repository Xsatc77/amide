import html
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Category, ContactMethodType, InventoryItem, Medium, Order, OrderItem, PaymentMethodType, Share,
    ShareCategory, User, Vendor, VendorContact, VendorFavorite, VendorPaymentMethod,
)


def _register_other(username: str, password: str = "Other1!") -> int:
    """Registers a brand-new user (own TestClient, own cookies) and returns their id. Mirrors
    tests/test_dashboard.py's _register_other."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": password, "confirm": password})
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == username.lower()))


def _logged_in_client(username: str, password: str = "Other1!") -> TestClient:
    """A second user's own logged-in TestClient (registering already signs them in), mirroring
    tests/test_inventory.py's test_shared_item_order_history_is_visible_but_not_editable pattern."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": password, "confirm": password})
    return other


def _text(response) -> str:
    return html.unescape(response.text)


def _make_vendor(name: str) -> int:
    with SessionLocal() as s:
        vendor = Vendor(name=name)
        s.add(vendor)
        s.commit()
        return vendor.id


def _order_for_vendor(uid: int, vendor_id: int, order_date: date, item_name: str) -> tuple[int, int]:
    """Creates an owned InventoryItem + Order + OrderItem so the vendor has purchase history."""
    with SessionLocal() as s:
        item = InventoryItem(owner_id=uid, name=item_name, category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10, vendor_id=vendor_id)
        s.add(item)
        s.flush()
        order = Order(order_date=order_date, vendor_id=vendor_id)
        s.add(order)
        s.flush()
        li = OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=1)
        s.add(li)
        s.commit()
        return item.id, order.id


def _contact_method_type_id(name: str) -> int:
    with SessionLocal() as s:
        return s.scalar(select(ContactMethodType.id).where(ContactMethodType.name == name))


# ---------------------------------------------------------------- list page

def test_list_vendors_shows_existing_vendor_by_name(client, db):
    _make_vendor("Peptide Palace")
    t = _text(client.get("/vendors"))
    assert "Peptide Palace" in t


def test_list_vendors_sorted_alphabetically_by_default(client, db):
    _make_vendor("Zulu Chemicals Alpha Sort")
    _make_vendor("Acme Aromatics Alpha Sort")
    t = _text(client.get("/vendors"))
    assert t.index("Acme Aromatics Alpha Sort") < t.index("Zulu Chemicals Alpha Sort")


def test_list_vendors_sort_recent_orders_by_most_recent_order_date(client, db, me):
    older_id = _make_vendor("Alpha Aromatics Recent Sort")
    newer_id = _make_vendor("Zulu Chemicals Recent Sort")
    _order_for_vendor(me, older_id, date(2020, 1, 1), "Old Order Item")
    _order_for_vendor(me, newer_id, date(2026, 1, 1), "New Order Item")

    t_alpha = _text(client.get("/vendors"))
    assert t_alpha.index("Alpha Aromatics Recent Sort") < t_alpha.index("Zulu Chemicals Recent Sort")

    t_recent = _text(client.get("/vendors?sort=recent"))
    assert t_recent.index("Zulu Chemicals Recent Sort") < t_recent.index("Alpha Aromatics Recent Sort")


def test_favoriting_pins_vendor_first_regardless_of_sort_and_not_for_other_users(client, db):
    alpha_id = _make_vendor("Alpha Favorite Sort")
    beta_id = _make_vendor("Beta Favorite Sort")

    r = client.post(f"/vendors/{beta_id}/favorite", follow_redirects=False)
    assert r.status_code == 303
    try:
        t_alpha_sort = _text(client.get("/vendors"))
        assert t_alpha_sort.index("Beta Favorite Sort") < t_alpha_sort.index("Alpha Favorite Sort")

        t_recent_sort = _text(client.get("/vendors?sort=recent"))
        assert t_recent_sort.index("Beta Favorite Sort") < t_recent_sort.index("Alpha Favorite Sort")

        other = _logged_in_client("VendorFavoriteOther")
        t_other = _text(other.get("/vendors"))
        # The other user favorited nothing -- plain alphabetical order for them.
        assert t_other.index("Alpha Favorite Sort") < t_other.index("Beta Favorite Sort")
    finally:
        with SessionLocal() as s:
            s.query(VendorFavorite).filter_by(vendor_id=beta_id).delete()
            s.commit()


# ---------------------------------------------------------------- detail page: contacts

def test_contact_entries_render_links_for_builtin_and_plain_text_for_custom(client, db):
    vendor_id = _make_vendor("Contact Method Vendor")
    email_type_id = _contact_method_type_id("Email")
    with SessionLocal() as s:
        custom_type = ContactMethodType(name="SignalHandle")
        s.add(custom_type)
        s.flush()
        s.add(VendorContact(vendor_id=vendor_id, method_type_id=email_type_id, value="vendor@example.com"))
        s.add(VendorContact(vendor_id=vendor_id, method_type_id=custom_type.id, value="signal-handle-123"))
        s.commit()

    t = client.get(f"/vendors/{vendor_id}").text
    assert 'href="mailto:vendor@example.com"' in t
    # The custom method's value is rendered as plain text -- no href wraps it.
    assert "signal-handle-123" in t
    assert '<a href="signal-handle-123"' not in t
    assert 'href="mailto:signal-handle-123"' not in t


def test_edit_form_adds_custom_contact_method_reusable_on_second_vendor(client, db):
    first_id = _make_vendor("Custom Method Vendor One")
    second_id = _make_vendor("Custom Method Vendor Two")

    r = client.post(f"/vendors/{first_id}", data={
        "name": "Custom Method Vendor One",
        "contacts-0-method_type_id": "__new__",
        "contacts-0-new_method_type": "Carrier Pigeon",
        "contacts-0-value": "coop-42",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        types = s.scalars(select(ContactMethodType).where(ContactMethodType.name == "Carrier Pigeon")).all()
        assert len(types) == 1  # created exactly once, reused (not retyped) below
        new_type_id = types[0].id

    # Second vendor's edit picks the same type from the dropdown (by id), not by retyping its name.
    r2 = client.post(f"/vendors/{second_id}", data={
        "name": "Custom Method Vendor Two",
        "contacts-0-method_type_id": str(new_type_id),
        "contacts-0-value": "coop-99",
    }, follow_redirects=False)
    assert r2.status_code == 303

    with SessionLocal() as s:
        # Still only one ContactMethodType row -- reused, not duplicated.
        assert s.scalar(select(ContactMethodType).where(ContactMethodType.name == "Carrier Pigeon")) is not None
        count = len(s.scalars(select(ContactMethodType).where(ContactMethodType.name == "Carrier Pigeon")).all())
        assert count == 1

    t2 = client.get(f"/vendors/{second_id}").text
    assert "Carrier Pigeon" in t2
    assert "coop-99" in t2


# ---------------------------------------------------------------- detail page: Purchase History

def test_purchase_history_shows_own_orders(client, db, me):
    vendor_id = _make_vendor("Purchase History Vendor Own")
    _order_for_vendor(me, vendor_id, date(2026, 1, 1), "My Purchased Item")
    t = _text(client.get(f"/vendors/{vendor_id}"))
    assert "My Purchased Item" in t


def test_purchase_history_hidden_without_share_visible_with_inventory_share(client, db, me):
    vendor_id = _make_vendor("Purchase History Vendor Shared")
    other_id = _register_other("VendorHistoryGrantor")
    _order_for_vendor(other_id, vendor_id, date(2026, 1, 1), "Other Users Purchased Item")

    try:
        # No Share yet -- the signed-in test user must not see the other user's order.
        t_before = _text(client.get(f"/vendors/{vendor_id}"))
        assert "Other Users Purchased Item" not in t_before

        # Grant the share the same way test_inventory.py's shared-item test does, from the actual
        # signed-in user's id (the `me` fixture), never a hardcoded id.
        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
            s.commit()

        t_after = _text(client.get(f"/vendors/{vendor_id}"))
        assert "Other Users Purchased Item" in t_after
        assert "VendorHistoryGrantor" in t_after  # tagged with the owner's name
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY).delete()
            s.commit()


# ---------------------------------------------------------------- recommend toggle

def test_recommend_toggle_round_trips_via_edit_form(client, db):
    vendor_id = _make_vendor("Recommend Toggle Vendor")

    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "Recommend Toggle Vendor", "recommended": "yes",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(Vendor, vendor_id).recommended is True
    t = _text(client.get(f"/vendors/{vendor_id}"))
    assert "Recommended" in t

    r2 = client.post(f"/vendors/{vendor_id}", data={
        "name": "Recommend Toggle Vendor", "recommended": "no",
    }, follow_redirects=False)
    assert r2.status_code == 303
    with SessionLocal() as s:
        assert s.get(Vendor, vendor_id).recommended is False
    t2 = _text(client.get(f"/vendors/{vendor_id}"))
    assert "Don't recommend" in t2

    r3 = client.post(f"/vendors/{vendor_id}", data={
        "name": "Recommend Toggle Vendor", "recommended": "",
    }, follow_redirects=False)
    assert r3.status_code == 303
    with SessionLocal() as s:
        assert s.get(Vendor, vendor_id).recommended is None


# ---------------------------------------------------------------- payment methods

def test_payment_method_checkboxes_and_new_payment_type_persist(client, db):
    vendor_id = _make_vendor("Payment Method Vendor")
    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "Payment Method Vendor",
        "new_payment_type": "Gift Card",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        names = {pm.method_type.name for pm in vendor.payment_methods}
        assert "Gift Card" in names

    t = client.get(f"/vendors/{vendor_id}").text
    assert "Gift Card" in t
