"""Check-in goes through every line of the order, each with its own lot, expiration, medium,
received and damaged counts."""
import html
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import InventoryItem, Medium, Order, OrderItem


def _two_line_order(db):
    with SessionLocal() as s:
        a = InventoryItem(name="Alpha Test", medium=Medium.LYOPHILIZED, vial_size_mg=5.0, owner_id=1)
        b = InventoryItem(name="Beta Test", medium=Medium.LYOPHILIZED, vial_size_mg=10.0, owner_id=1)
        o = Order(order_date=date(2026, 8, 1))
        s.add_all([a, b, o])
        s.flush()
        la = OrderItem(order_id=o.id, inventory_item_id=a.id, quantity=10)
        lb = OrderItem(order_id=o.id, inventory_item_id=b.id, quantity=5)
        s.add_all([la, lb])
        s.commit()
        return a.id, b.id, o.id, la.id, lb.id


def _uid(client):
    from app.models import User
    from tests.conftest import TEST_USER
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == TEST_USER.lower()))


def test_dialog_has_a_row_for_every_line_with_the_new_fields(client, db):
    a, b, o, la, lb = _two_line_order(db)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = _uid(client)
        s.commit()
    t = html.unescape(client.get(f"/inventory/{a}").text)
    assert "Beta Test" in t and "checkin-data" in t
    js = client.get("/static/js/inventory.js").text
    for name in ("\"received_quantity\"", "\"damaged_quantity\"", "\"lot_number\"", "\"expiration_date\"", "\"medium\""):
        assert name in js


def test_check_in_saves_each_lines_own_details(client, db):
    a, b, o, la, lb = _two_line_order(db)
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10",
        f"received_quantity_{la}": "10", f"damaged_quantity_{la}": "2",
        f"lot_number_{la}": "LOT-A1", f"expiration_date_{la}": "2028-01-31",
        f"received_quantity_{lb}": "5", f"damaged_quantity_{lb}": "",
        f"lot_number_{lb}": "LOT-B9", f"expiration_date_{lb}": "2028-06-30",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        x, y = s.get(OrderItem, la), s.get(OrderItem, lb)
        assert (x.received_quantity, x.damaged_quantity) == (8, 2)        # stock counts only the usable
        assert (x.lot_number, x.expiration_date) == ("LOT-A1", date(2028, 1, 31))
        assert (y.received_quantity, y.damaged_quantity) == (5, 0)
        assert (y.lot_number, y.expiration_date) == ("LOT-B9", date(2028, 6, 30))
        assert s.get(InventoryItem, a).available_count == 8


def test_more_damaged_than_arrived_is_refused(client, db):
    a, b, o, la, lb = _two_line_order(db)
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{la}": "3", f"damaged_quantity_{la}": "4"})
    assert r.status_code == 422
    assert "More damaged than arrived" in html.unescape(r.text)
    with SessionLocal() as s:
        assert s.get(Order, o).arrival_date is None


def _mine(client, a, b):
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()


def test_a_different_medium_at_check_in_changes_the_item_when_it_holds_only_that_line(client, db):
    a, b, o, la, lb = _two_line_order(db)
    _mine(client, a, b)
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10", f"medium_{la}": "Liquid"})
    assert r.status_code == 422                       # a liquid needs a volume the item does not have yet
    with SessionLocal() as s:
        s.get(InventoryItem, a).volume_ml = 10.0
        s.commit()
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10", f"medium_{la}": "Liquid"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, a).medium == Medium.LIQUID
        assert s.get(InventoryItem, b).medium == Medium.LYOPHILIZED


def test_a_second_order_for_an_item_becomes_its_own_item(client, db):
    a, b, o, la, lb = _two_line_order(db)
    _mine(client, a, b)
    r = client.post(f"/inventory/{a}/orders", data={"quantity": "4", "order_date": "2026-08-05"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        items = s.scalars(select(InventoryItem).where(InventoryItem.name == "Alpha Test")).all()
        assert len(items) == 2 and all(len(i.order_items) == 1 for i in items)
        new = next(i for i in items if i.id != a)
        assert r.headers["location"] == f"/inventory/{new.id}"
        assert new.vial_size_mg == 5.0 and new.medium == Medium.LYOPHILIZED and not new.is_active


def test_the_list_has_one_row_per_item_even_with_the_same_name_and_medium(client, db):
    a, b, o, la, lb = _two_line_order(db)
    _mine(client, a, b)
    client.post(f"/inventory/{a}/orders", data={"quantity": "4", "order_date": "2026-08-05"})
    t = html.unescape(client.get("/inventory").text)
    assert t.count("Alpha Test</a>") >= 2


def test_taken_so_far_can_be_corrected_on_a_drops_item(client, db):
    uid = _uid(client)
    with SessionLocal() as s:
        it = InventoryItem(name="Drop Test", medium=Medium.DROPS, vial_size_mg=5.0, owner_id=uid, dosed_units=22.0)
        s.add(it); s.flush()
        o = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 2)); s.add(o); s.flush()
        s.add(OrderItem(order_id=o.id, inventory_item_id=it.id, quantity=10, received_quantity=10)); s.commit()
        iid = it.id
        assert it.available_count == -12
    page = html.unescape(client.get(f"/inventory/{iid}").text)
    assert 'name="dosed_units"' in page and "<dt>Taken</dt>" in page
    r = client.post(f"/inventory/{iid}", data={"name": "Drop Test", "category": "Medicine", "medium": "Drops", "vial_size_mg": "5",
                                               "vial_size_unit": "mg", "dosed_units": "0"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, iid).dosed_units == 0 and s.get(InventoryItem, iid).available_count == 10


def test_a_drops_item_can_be_started_from_its_page(client, db):
    uid = _uid(client)
    with SessionLocal() as s:
        it = InventoryItem(name="Drop Start", medium=Medium.DROPS, vial_size_mg=5.0, owner_id=uid)
        s.add(it); s.commit(); iid = it.id
    page = html.unescape(client.get(f"/inventory/{iid}").text)
    assert "data-active-toggle" in page and ">Start using<" in page
    assert client.post(f"/inventory/{iid}/active", data={"active": "1"}, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, iid).is_active
    assert ">Stop using<" in html.unescape(client.get(f"/inventory/{iid}").text)


def test_switch_button_on_the_inventory_card_names_the_medicine_not_the_protocol(client, db):
    from app.models import Peptide, Protocol, ProtocolItem
    uid = _uid(client)
    with SessionLocal() as s:
        drops = InventoryItem(name="Vit Test", medium=Medium.DROPS, vial_size_mg=5.0, owner_id=uid, is_active=True)
        liq = InventoryItem(name="Vit Test", medium=Medium.LIQUID, vial_size_mg=10.0, volume_ml=1.0, owner_id=uid)
        pep = Peptide(name="Vit Test", owner_id=uid) if "owner_id" in Peptide.__table__.c else Peptide(name="Vit Test")
        s.add_all([drops, liq, pep]); s.flush()
        p = Protocol(name="Zzz Stack Name", owner_id=uid, start_date=date(2026, 1, 1))
        s.add(p); s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, inventory_item_id=drops.id, dose=1, dose_unit="mg"))
        s.commit()
    t = html.unescape(client.get("/inventory").text)
    assert "Switch Vit Test to Liquid" in t and "Switch Zzz Stack Name" not in t


def test_dropdowns_tell_two_items_of_one_name_apart_by_lot(client, db):
    uid = _uid(client)
    with SessionLocal() as s:
        a = InventoryItem(name="Lot Pick", medium=Medium.DROPS, vial_size_mg=5.0, owner_id=uid)
        s.add(a); s.flush()
        o = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 3)); s.add(o); s.flush()
        s.add(OrderItem(order_id=o.id, inventory_item_id=a.id, quantity=1, received_quantity=1, lot_number="LOT-XY7")); s.commit()
        assert a.lot_label == "LOT-XY7"
    from app.protocols import switching
    with SessionLocal() as s:
        items = [i for i in s.scalars(select(InventoryItem).where(InventoryItem.name == "Lot Pick"))]
        assert "lot LOT-XY7" in switching._label(items[0])
    assert "lot" in client.get("/static/js/protocol-builder.js").text.lower()
