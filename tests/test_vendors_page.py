import html
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select

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


def test_sort_recent_ignores_another_users_unshared_order(client, db):
    # Privacy fix: Order/OrderItem are private-by-default activity logs, so `?sort=recent`'s
    # recency aggregate must be scoped exactly like Purchase History (own orders + Inventory-shared
    # orders only) -- a vendor must not visibly jump up the sort just because a DIFFERENT,
    # unrelated user ordered from it recently, with no Share established.
    quiet_id = _make_vendor("Quiet Vendor No Visible Orders")
    loud_id = _make_vendor("Zzz Vendor Other Users Recent Order")
    other_id = _register_other("VendorSortPrivacyOther")
    # Another user's very recent order with this vendor -- no Share exists between them and "tester".
    _order_for_vendor(other_id, loud_id, date(2026, 1, 1), "Other User's Private Order Item")

    t_recent = _text(client.get("/vendors?sort=recent"))
    # Neither vendor has any order visible to the signed-in user, so this falls back to the
    # no-orders-yet tie-break (alphabetical) rather than the other user's order date winning.
    assert t_recent.index("Quiet Vendor No Visible Orders") < t_recent.index("Zzz Vendor Other Users Recent Order")
    # And the item itself must never leak into the (unrelated) Vendors list page at all.
    assert "Other User's Private Order Item" not in t_recent


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


def test_unfavorite_nonexistent_vendor_404s(client, db):
    # Consistency fix: favorite_vendor already 404s for a nonexistent vendor_id; unfavorite_vendor
    # must match instead of silently no-op'ing a delete against nothing.
    with SessionLocal() as s:
        bogus_id = (s.scalar(select(Vendor.id).order_by(Vendor.id.desc())) or 0) + 1000
    r = client.post(f"/vendors/{bogus_id}/unfavorite", follow_redirects=False)
    assert r.status_code == 404


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

# ---------------------------------------------------------------- price list file serving

def test_get_price_list_serves_uploaded_file(client, db):
    vendor_id = _make_vendor("Price List File Vendor")
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        vendor.price_list_filename = "test-price-list.pdf"
        s.commit()
    from app import config
    config.ensure_dirs()
    pdf_bytes = b"%PDF-1.7\n" + b"\x00" * 32
    (config.PRICE_LIST_DIR / "test-price-list.pdf").write_bytes(pdf_bytes)
    try:
        r = client.get(f"/vendors/{vendor_id}/price-list")
        assert r.status_code == 200
        assert r.content == pdf_bytes
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["x-content-type-options"] == "nosniff"
    finally:
        (config.PRICE_LIST_DIR / "test-price-list.pdf").unlink(missing_ok=True)


def test_get_price_list_404s_when_no_file_on_vendor(client, db):
    vendor_id = _make_vendor("Price List No File Vendor")
    r = client.get(f"/vendors/{vendor_id}/price-list")
    assert r.status_code == 404


def test_get_price_list_404s_when_file_missing_from_disk(client, db):
    vendor_id = _make_vendor("Price List Missing Vendor")
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        vendor.price_list_filename = "does-not-exist-on-disk.pdf"
        s.commit()
    r = client.get(f"/vendors/{vendor_id}/price-list")
    assert r.status_code == 404


def test_get_price_list_404s_for_nonexistent_vendor(client, db):
    with SessionLocal() as s:
        bogus_id = (s.scalar(select(Vendor.id).order_by(Vendor.id.desc())) or 0) + 1000
    r = client.get(f"/vendors/{bogus_id}/price-list")
    assert r.status_code == 404


def test_vendor_detail_links_to_price_list_download_route(client, db):
    vendor_id = _make_vendor("Price List Link Vendor")
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        vendor.price_list_filename = "linked-price-list.pdf"
        s.commit()
    t = _text(client.get(f"/vendors/{vendor_id}"))
    assert f'href="/vendors/{vendor_id}/price-list"' in t


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


def test_update_vendor_with_nonexistent_payment_type_id_does_not_500(client, db):
    # Fix 6: a tampered/stale payment_type_ids value must be skipped, never crash the request with
    # an unhandled foreign-key IntegrityError.
    vendor_id = _make_vendor("Bad Payment Type Vendor")
    with SessionLocal() as s:
        bogus_id = (s.scalar(select(PaymentMethodType.id).order_by(PaymentMethodType.id.desc())) or 0) + 1000

    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "Bad Payment Type Vendor",
        "payment_type_ids": str(bogus_id),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.payment_methods == []


# ---------------------------------------------------------------- Fix 1: setting a vendor's FIRST price list


def test_set_first_price_list_via_url_through_edit_form(client, db):
    vendor_id = _make_vendor("First Price List Via URL Vendor")
    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "First Price List Via URL Vendor",
        "price_list_url": "https://vendor.example/prices",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.price_list_url == "https://vendor.example/prices"
        assert vendor.price_list_filename is None
        assert vendor.price_list_updated_at == date.today()

    t = _text(client.get(f"/vendors/{vendor_id}"))
    assert "https://vendor.example/prices" in t
    assert date.today().strftime("%Y-%m-%d") in t or "Updated" in t


def test_set_first_price_list_via_file_upload_through_edit_form(client, db):
    # Also proves the .docx support this feature added is actually reachable through a real route.
    vendor_id = _make_vendor("First Price List Via File Vendor")
    docx_bytes = b"PK\x03\x04" + b"\x00" * 32
    r = client.post(f"/vendors/{vendor_id}", data={"name": "First Price List Via File Vendor"},
                    files={"price_list_file": ("prices.docx", docx_bytes,
                                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                    follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.price_list_filename is not None
        assert vendor.price_list_url is None
        assert vendor.price_list_updated_at == date.today()

    r2 = client.get(f"/vendors/{vendor_id}/price-list")
    assert r2.status_code == 200
    assert r2.content == docx_bytes


def test_remove_price_list_via_edit_form(client, db):
    vendor_id = _make_vendor("Remove Price List Vendor")
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        vendor.price_list_url = "https://vendor.example/old-prices"
        vendor.price_list_updated_at = date(2026, 1, 1)
        s.commit()

    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "Remove Price List Vendor",
        "remove_price_list": "1",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.price_list_url is None
        assert vendor.price_list_filename is None


def test_leaving_price_list_fields_blank_does_not_touch_existing_price_list(client, db):
    vendor_id = _make_vendor("Leave Price List Alone Vendor")
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        vendor.price_list_url = "https://vendor.example/still-here"
        vendor.price_list_updated_at = date(2026, 1, 1)
        s.commit()

    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "Leave Price List Alone Vendor",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.price_list_url == "https://vendor.example/still-here"
        assert vendor.price_list_updated_at == date(2026, 1, 1)  # untouched, not bumped to today


def test_edit_form_rejects_javascript_url_for_price_list(client, db):
    # Fix 2: price_list_url must be validated as an http(s) URL exactly like website already is,
    # since Vendor is a global/shared row every user's browser would render this href for.
    vendor_id = _make_vendor("JS URL Price List Vendor")
    r = client.post(f"/vendors/{vendor_id}", data={
        "name": "JS URL Price List Vendor",
        "price_list_url": "javascript:alert(1)",
    }, follow_redirects=False)
    assert r.status_code == 422
    with SessionLocal() as s:
        vendor = s.get(Vendor, vendor_id)
        assert vendor.price_list_url is None


def test_cross_task_new_vendor_then_edit_price_list_then_staleness_prompt_appears(client, db):
    # End-to-end reachability check for Fix 1: create a vendor through the New Order new-vendor
    # branch (which never sets a price list), set its price list through the vendor edit form, then
    # confirm a second New Order for the same vendor now shows the staleness prompt -- closing the
    # exact gap the final review found.
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "is_new_vendor": "yes", "name": "Cross Task Vendor",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Cross Task Item",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        vendor = s.scalar(select(Vendor).where(Vendor.name == "Cross Task Vendor"))
        assert vendor.price_list_filename is None and vendor.price_list_url is None
        vendor_id = vendor.id

    r2 = client.post(f"/vendors/{vendor_id}", data={
        "name": "Cross Task Vendor",
        "price_list_url": "https://cross-task.example/prices",
    }, follow_redirects=False)
    assert r2.status_code == 303

    t = client.get("/inventory").text
    assert f'value="{vendor_id}"' in t
    assert f'data-has-price-list="1"' in t or 'data-has-price-list' in t
    # The specific <option> for this vendor must carry the has-price-list flag now.
    import re
    options = re.findall(rf'<option value="{vendor_id}"[^>]*>', t)         # other selects may also have an option with this number
    assert any('data-has-price-list="1"' in o for o in options)


# ---------------------------------------------------------------- add / delete vendor

def test_add_vendor_records_creator_and_redirects_to_detail(client, me, db):
    r = client.post("/vendors", data={"name": "Brand New Vendor"}, follow_redirects=False)
    with SessionLocal() as s:
        vendor = s.scalar(select(Vendor).where(Vendor.name == "Brand New Vendor"))
    assert vendor is not None and vendor.created_by_id == me
    assert r.status_code == 303 and r.headers["location"] == f"/vendors/{vendor.id}"


def test_add_vendor_ignores_blank_and_duplicate_names(client, db):
    _make_vendor("Existing Vendor")
    client.post("/vendors", data={"name": "   "}, follow_redirects=False)
    client.post("/vendors", data={"name": "existing vendor"}, follow_redirects=False)
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Vendor)) == 1


def test_delete_vendor_removes_it_and_keeps_order_history(client, me, db):
    vendor_id = _make_vendor("Doomed Vendor")
    _, order_id = _order_for_vendor(me, vendor_id, date(2026, 1, 5), "KeptItem")
    r = client.post(f"/vendors/{vendor_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(Vendor, vendor_id) is None
        assert s.get(Order, order_id) is not None


def test_delete_nonexistent_vendor_404s(client, db):
    assert client.post("/vendors/999999/delete", follow_redirects=False).status_code == 404


def test_non_admin_cannot_delete_a_vendor_and_gets_no_delete_button(client, db):
    vendor_id = _make_vendor("Protected Vendor")
    other = _logged_in_client("vendornonadmin")
    assert other.post(f"/vendors/{vendor_id}/delete", follow_redirects=False).status_code == 404
    with SessionLocal() as s:
        assert s.get(Vendor, vendor_id) is not None
    assert "Delete this vendor?" not in other.get("/vendors").text
    assert "Delete this vendor?" in client.get("/vendors").text
