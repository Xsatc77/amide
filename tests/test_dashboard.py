import html
from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    ActiveVial, Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide,
    Protocol, ProtocolItem, Route, User,
)


def _setup_protocol_with_vial(client, db, dose=2.0, quantity=2):
    """Mirrors tests/test_dosing.py's _setup_protocol_with_vial: pin `uid` to the actual signed-in
    test user (not a bare select(User.id), which can pick up a different user under full-suite
    test-order pollution), and reuse the seeded "Retatrutide" Peptide row instead of inserting a
    duplicate (Peptide.name is unique and migrations/versions/0003_protocols.py already seeds it)."""
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0,
                          dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5, date_mixed=date.today(),
                          discard_by=date(2099, 1, 1), volume_remaining_ml=2.0)
        s.add(vial)
        protocol = Protocol(name="Fat Loss", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=dose, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item.id)
        s.add(pitem)
        s.commit()
        return protocol.id, pitem.id, vial.id


def test_dashboard_shows_todays_schedule(client, db):
    _setup_protocol_with_vial(client, db)
    t = html.unescape(client.get("/dashboard").text)
    assert "Retatrutide" in t


def test_dashboard_low_stock_alert_respects_explicit_zero(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10, count=0,
                             low_stock_threshold=0)
        s.add(item)
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "Retatrutide" in t  # available_count=0 <= threshold=0 -> alerts


def test_dashboard_shipment_alert_shows_when_over_threshold(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        order = Order(vendor="VendorX", order_date=date.today() - timedelta(days=30),
                    tax_cents=None, shipping_cents=None)
        s.add(order)
        s.flush()
        s.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=1))
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "VendorX" in t


def test_dashboard_placeholder_cards_present(client, db):
    t = html.unescape(client.get("/dashboard").text)
    assert "Weight" in t and "Journal" in t


def test_home_redirects_to_dashboard(client, db):
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
