import pytest
import re
import html
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    ActiveVial, Category, DoseLog, DoseStatus, DoseUnit, Frequency, InventoryItem, Medium, Order,
    OrderItem, Peptide, Protocol, ProtocolItem, Route, Share, ShareCategory, User,
)


def _register_other(username: str, password: str = "Other1!") -> int:
    """Registers a brand-new user (own TestClient, own cookies) and returns their id. Mirrors
    test_active_vials.py's test_shared_active_vial_discard_restricted_to_owner pattern."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": password, "confirm": password})
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == username.lower()))


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


def test_dashboard_shows_water_goal_from_latest_weight(client, db):
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=uid, measured_at=date.today(), weight_lbs=200))
        s.commit()
    try:
        t = client.get("/dashboard").text
        assert 'id="dash-water-heading"' in t
        assert "100" in t  # default water goal: 200/2
    finally:
        with SessionLocal() as s:
            s.query(BodyMeasurement).filter_by(owner_id=uid).delete()
            s.commit()


def test_dashboard_still_tracks_water_with_no_weight_logged(client, db):
    from app.models import WaterLog
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
    t = client.get("/dashboard").text
    assert 'id="dash-water-heading"' in t and "open-water-dialog" in t
    assert "weigh-in" in t                                        # says why there is no daily goal yet
    try:
        client.post("/dashboard/water/log", data={"ounces": "16"})
        t = re.sub(r"<[^>]+>", "", client.get("/dashboard").text)
        assert "16 oz logged today" in re.sub(r"\s+", " ", t)
    finally:
        with SessionLocal() as s:
            s.query(WaterLog).filter_by(owner_id=uid).delete()
            s.commit()


def test_reset_today_zeroes_todays_water_only_and_asks_first(client, db):
    from datetime import timedelta

    from app.models import WaterLog
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add_all([WaterLog(owner_id=uid, logged_at=date.today(), ounces=8), WaterLog(owner_id=uid, logged_at=date.today(), ounces=16),
                   WaterLog(owner_id=uid, logged_at=date.today() - timedelta(days=1), ounces=24)])
        s.commit()
    try:
        page = client.get("/dashboard").text
        assert 'action="/dashboard/water/reset"' in page and "data-confirm" in page
        r = client.post("/dashboard/water/reset", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        with SessionLocal() as s:
            left = s.scalars(select(WaterLog).where(WaterLog.owner_id == uid)).all()
            assert [w.ounces for w in left] == [24]               # yesterday stays
        assert 'action="/dashboard/water/reset"' not in client.get("/dashboard").text      # nothing logged today: nothing to reset
    finally:
        with SessionLocal() as s:
            s.query(WaterLog).filter_by(owner_id=uid).delete()
            s.commit()


def test_dashboard_shows_body_panel_with_weight_chart(client, db):
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=uid, measured_at=date.today(), weight_lbs=180))
        s.commit()
    try:
        t = client.get("/dashboard").text
        assert 'id="body-panel-heading"' in t
        assert "Weight (lbs)" in t
        assert "No weight logged yet." not in t
    finally:
        with SessionLocal() as s:
            s.query(BodyMeasurement).filter_by(owner_id=uid).delete()
            s.commit()


def test_dashboard_body_panel_shows_empty_state_with_no_measurements(client, db):
    t = client.get("/dashboard").text
    assert 'id="body-panel-heading"' in t
    assert "No weight logged yet." in t


def test_dashboard_shows_workout_week_strip_with_seven_days(client, db):
    t = client.get("/dashboard").text
    assert 'id="workout-week-heading"' in t
    assert t.count("workout-day-label") == 7
    for label in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert f">{label}<" in t


def test_dashboard_workout_week_marks_a_logged_day_done(client, db, me):
    from app.models import WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    with SessionLocal() as s:
        plan = WorkoutPlan(owner_id=me, name="Dash Week Plan", source=WorkoutSource.MANUAL,
                           started_on=monday)
        s.add(plan)
        s.flush()
        day = WorkoutPlanDay(plan_id=plan.id, position=0, label="Full body", weekdays="M")
        s.add(day)
        s.flush()
        s.add(WorkoutLog(owner_id=me, plan_day_id=day.id, log_date=monday))
        s.commit()
        plan_id, day_id = plan.id, day.id
    try:
        t = client.get("/dashboard").text
        assert "workout-day-done" in t
    finally:
        with SessionLocal() as s:
            s.query(WorkoutLog).filter_by(plan_day_id=day_id).delete()
            s.query(WorkoutPlanDay).filter_by(plan_id=plan_id).delete()
            s.query(WorkoutPlan).filter_by(id=plan_id).delete()
            s.commit()


def test_log_water_creates_entry_shown_on_next_dashboard_load(client, db):
    from app.models import BodyMeasurement, WaterLog
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=uid, measured_at=date.today(), weight_lbs=200))
        s.commit()
    try:
        r = client.post("/dashboard/water/log", data={"ounces": "16"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        t = client.get("/dashboard").text
        assert "16 oz logged today (16%)" in t  # goal is 100 (200/2); 16/100 = 16%
    finally:
        with SessionLocal() as s:
            s.query(WaterLog).filter_by(owner_id=uid).delete()
            s.query(BodyMeasurement).filter_by(owner_id=uid).delete()
            s.commit()


def test_log_water_ignores_blank_or_invalid_amount(client, db):
    from app.models import WaterLog
    client.post("/dashboard/water/log", data={"ounces": ""}, follow_redirects=False)
    client.post("/dashboard/water/log", data={"ounces": "not-a-number"}, follow_redirects=False)
    client.post("/dashboard/water/log", data={"ounces": "-5"}, follow_redirects=False)
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        assert s.query(WaterLog).filter_by(owner_id=uid).count() == 0


def test_log_water_button_hidden_on_a_shared_dashboard(client, db, me):
    from app.models import BodyMeasurement
    other_id = _register_other("DashWaterA")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
        s.add(BodyMeasurement(owner_id=other_id, measured_at=date.today(), weight_lbs=150))
        s.commit()
    try:
        t = html.unescape(client.get(f"/dashboard?viewer_id={other_id}").text)
        assert 'id="dash-water-heading"' in t
        assert "open-water-dialog" not in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me).delete()
            s.query(BodyMeasurement).filter_by(owner_id=other_id).delete()
            s.commit()


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


def test_viewer_dropdown_lists_users_who_shared_with_me(client, db, me):
    other_id = _register_other("DashViewerA")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.commit()
    try:
        t = html.unescape(client.get("/dashboard").text)
        assert "DashViewerA" in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.INVENTORY).delete()
            s.commit()


def test_switching_viewer_shows_only_that_users_data_not_blended(client, db, me):
    other_id = _register_other("DashViewerB")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.add(InventoryItem(owner_id=other_id, name="OtherLowStock", category=Category.MEDICINE,
                            medium=Medium.LYOPHILIZED, vial_size_mg=10, count=0, low_stock_threshold=0))
        s.commit()
    try:
        t_other = html.unescape(client.get(f"/dashboard?viewer_id={other_id}").text)
        assert "OtherLowStock" in t_other

        t_self = html.unescape(client.get("/dashboard").text)
        assert "OtherLowStock" not in t_self
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.INVENTORY).delete()
            s.query(InventoryItem).filter_by(owner_id=other_id, name="OtherLowStock").delete()
            s.commit()


def test_viewer_without_personal_data_share_hides_schedule_widget(client, db, me):
    other_id = _register_other("DashViewerC")
    with SessionLocal() as s:
        # Inventory-only share -- no PERSONAL_DATA.
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.commit()
    try:
        t = html.unescape(client.get(f"/dashboard?viewer_id={other_id}").text)
        assert "schedule-heading" not in t
        assert "adherence-heading" not in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.INVENTORY).delete()
            s.commit()


def test_revoked_share_removes_viewer_option(client, db, me):
    other_id = _register_other("DashViewerD")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.add(InventoryItem(owner_id=other_id, name="RevokedLowStock", category=Category.MEDICINE,
                            medium=Medium.LYOPHILIZED, vial_size_mg=10, count=0, low_stock_threshold=0))
        s.commit()
    # Revoke immediately -- the share no longer exists by the time we hit the dashboard.
    with SessionLocal() as s:
        s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                 category=ShareCategory.INVENTORY).delete()
        s.commit()
    try:
        # No longer offered as a dropdown option.
        t = html.unescape(client.get("/dashboard").text)
        assert "DashViewerD" not in t

        # Requesting the now-revoked viewer_id falls back to self silently (200, not 403), and
        # does not leak the other user's data.
        r = client.get(f"/dashboard?viewer_id={other_id}")
        assert r.status_code == 200
        assert "RevokedLowStock" not in html.unescape(r.text)
    finally:
        with SessionLocal() as s:
            s.query(InventoryItem).filter_by(owner_id=other_id, name="RevokedLowStock").delete()
            s.commit()


def test_non_numeric_viewer_id_falls_back_to_self_instead_of_500(client, db):
    r = client.get("/dashboard?viewer_id=abc")
    assert r.status_code == 200
    assert "Weight" in html.unescape(r.text)  # placeholder card confirms a normal self-render


def test_oversized_viewer_id_falls_back_to_self_instead_of_500(client, db):
    # Parses fine as a Python int (unbounded), but overflows SQLite's integer column binding.
    r = client.get("/dashboard?viewer_id=99999999999999999999")
    assert r.status_code == 200
    assert "Weight" in html.unescape(r.text)


def _checked_in_order_item(s, uid, item_name, expiration_date, *, arrival_date):
    """A Medicine InventoryItem with one OrderItem line carrying `expiration_date`, on an Order
    whose arrival_date is `arrival_date` (None means still in transit)."""
    item = InventoryItem(owner_id=uid, name=item_name, category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10)
    s.add(item)
    s.flush()
    order = Order(vendor="VendorY", order_date=date.today() - timedelta(days=60), arrival_date=arrival_date)
    s.add(order)
    s.flush()
    li = OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=1,
                  received_quantity=1 if arrival_date else None, expiration_date=expiration_date)
    s.add(li)
    s.commit()
    return item


def test_dashboard_expiration_alert_uses_arrived_order_item_lot_date(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        _checked_in_order_item(s, uid, "ExpiringSoonPeptide", date.today() + timedelta(days=1),
                               arrival_date=date.today() - timedelta(days=5))
    t = html.unescape(client.get("/dashboard").text)
    assert "ExpiringSoonPeptide" in t
    assert "expiring soon" in t


def test_dashboard_expiration_alert_ignores_lot_on_unarrived_order(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        _checked_in_order_item(s, uid, "InTransitPeptide", date.today() + timedelta(days=1),
                               arrival_date=None)
    t = html.unescape(client.get("/dashboard").text)
    # The item appears once: in the Items In Shipment card. It is not a low-stock alert (it is already
    # on order) and not an *expiration* alert, since its only expiration_date lives on a line whose
    # order hasn't arrived.
    assert t.count("InTransitPeptide") == 1
    assert "expiring soon" not in t and "low stock" not in t


def test_adherence_pct_counts_unlogged_missed_doses_in_denominator(client, db):
    # A protocol due every 5th day, starting 5 days ago -- exactly two doses are due in the 30-day
    # window (today and 5 days ago). Only today's is logged, so adherence must read 50%, not 100%.
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date.today() - timedelta(days=5), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.EVERY_N_DAYS, every_n_days=5, route=Route.SUBQ)
        s.add(pitem)
        s.flush()
        s.add(DoseLog(owner_id=uid, protocol_id=protocol.id, protocol_item_id=pitem.id,
                      peptide_id=peptide.id, peptide_name=peptide.name, dose_value=pitem.dose,
                      dose_unit=pitem.dose_unit, route=pitem.route.value, scheduled_date=date.today(),
                      scheduled_time_of_day=pitem.time_of_day, status=DoseStatus.ON_TIME,
                      logged_at=datetime.now(timezone.utc)))
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "50%" in t


def test_cost_snapshot_hidden_for_inventory_only_share(client, db, me):
    other_id = _register_other("DashCostA")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.commit()
    try:
        t = html.unescape(client.get(f"/dashboard?viewer_id={other_id}").text)
        assert "alerts-heading" in t
        assert "cost-heading" not in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.INVENTORY).delete()
            s.commit()


def test_cost_snapshot_shown_for_inventory_and_personal_data_share(client, db, me):
    other_id = _register_other("DashCostB")
    with SessionLocal() as s:
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.INVENTORY))
        s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
        s.commit()
    try:
        t = html.unescape(client.get(f"/dashboard?viewer_id={other_id}").text)
        assert "cost-heading" in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me).delete()
            s.commit()


def test_cost_snapshot_shown_for_own_dashboard_regardless_of_shares(client, db, me):
    # Grant ONLY inventory to someone else viewing *me* -- irrelevant to my own self-view, which
    # must still show my own cost snapshot section (empty or not) via the self-view carve-out.
    other_id = _register_other("DashCostC")
    with SessionLocal() as s:
        s.add(Share(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY))
        s.commit()
    try:
        t = html.unescape(client.get("/dashboard").text)
        assert "cost-heading" in t
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=me, grantee_id=other_id,
                                     category=ShareCategory.INVENTORY).delete()
            s.commit()


# ---------------------------------------------------------------- Items In Shipment card

def _shipment_card(client) -> str:
    t = html.unescape(client.get("/dashboard").text)
    return t[t.index('id="shipment-heading"'):]


def test_shipment_card_lists_unarrived_orders_only(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        _checked_in_order_item(s, uid, "ShippingPeptide", None, arrival_date=None)
        _checked_in_order_item(s, uid, "ArrivedPeptide", None, arrival_date=date.today())
    card = _shipment_card(client)
    assert "ShippingPeptide" in card
    assert "ArrivedPeptide" not in card


def test_shipment_card_names_the_vendor_in_a_tab_on_the_left_edge(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = _checked_in_order_item(s, uid, "TabbedPeptide", None, arrival_date=None)
        order = s.scalar(select(Order).join(OrderItem, OrderItem.order_id == Order.id).where(OrderItem.inventory_item_id == item.id))
        order.vendor = "Acme Labs"
        s.commit()
    card = _shipment_card(client)
    rows = card.split("<tbody>")[1].split("</tr>")[0]
    assert re.search(r'<td class="ship-vendor"><span><a href="/inventory/orders#order-\d+"[^>]*>Acme Labs</a></span></td>\s*<td>', rows)          # the vendor's own cell comes first in the row
    assert "<th class=\"ship-vendor\"></th>" in card.split("</thead>")[0]
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    rule = css.split(".ship-vendor {")[1].split("}")[0]
    assert "writing-mode: vertical-rl" in rule and "var(--ribbon)" in rule and "#000" in rule          # a vertical, yellow tab with black text


def test_shipment_card_shows_empty_state_when_nothing_in_transit(client, db):
    assert "Nothing in transit." in _shipment_card(client)


def test_shipment_card_excludes_supplies(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = _checked_in_order_item(s, uid, "ShippingSyringes", None, arrival_date=None)
        item.category = Category.SUPPLY
        s.commit()
    assert "ShippingSyringes" not in _shipment_card(client)


def test_shipment_card_never_shows_another_users_line_on_the_same_order(client, db):
    other_id = _register_other("shipother")
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        mine = _checked_in_order_item(s, uid, "MyShippingPeptide", None, arrival_date=None)
        theirs = InventoryItem(owner_id=other_id, name="TheirShippingPeptide", category=Category.MEDICINE,
                               medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(theirs)
        s.flush()
        order_id = s.scalar(select(OrderItem.order_id).where(OrderItem.inventory_item_id == mine.id))
        s.add(OrderItem(order_id=order_id, inventory_item_id=theirs.id, quantity=1))
        s.commit()
    card = _shipment_card(client)
    assert "MyShippingPeptide" in card
    assert "TheirShippingPeptide" not in card


def test_dashboard_low_stock_ignores_items_that_were_just_ordered_and_returns_when_they_arrive_short(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = InventoryItem(owner_id=uid, name="Zorvex Justordered", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10, count=0)
        s.add(item)
        s.flush()
        order = Order(vendor="Acme", order_date=date.today() - timedelta(days=2))
        s.add(order)
        s.flush()
        line = OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=1)
        s.add(line)
        s.commit()
        order_id = order.id
    assert "low stock" not in html.unescape(client.get("/dashboard").text)             # on its way: nothing to warn about yet
    with SessionLocal() as s:                                                           # it arrives, but only 1 vial (threshold 5)
        s.get(Order, order_id).arrival_date = date.today()
        s.query(OrderItem).filter_by(order_id=order_id).update({"received_quantity": 1})
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "Zorvex Justordered" in t and "low stock (1 left" in t


def test_dashboard_body_diagram_is_cropped_to_the_figure_and_its_labels(client, db):
    """The empty left margin of the shared 400-wide drawing made the Body card's figure small: the dashboard crops to what is drawn."""
    import re
    from app.routers.measurements import _LABEL_SLOTS, _silhouette_shape
    box = re.search(r'<svg viewBox="([\d. ]+)" class="body-silhouette"', client.get("/dashboard").text)
    x, y, w, h = (float(n) for n in box.group(1).split())
    assert (y, h) == (0, 440) and w < 400
    for sex in ("Male", "Female"):                                    # nothing drawn falls outside the crop
        xs = [float(n) for n in re.findall(r"-?\d+\.?\d*", _silhouette_shape(sex)["body_path"])][0::2]
        assert x <= min(xs) and max(xs) <= x + w
    assert max(slot[0] for slot in _LABEL_SLOTS.values()) <= x + w


def test_shipment_orders_alternate_two_shades_of_yellow():
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "tbody tr:nth-child(even) td.ship-vendor { background: #ca8a04; }" in css                       # the deeper tab on every second order
    assert ".dash-area-shipment .inv-table tbody tr:nth-child(even) td:not(.ship-vendor)" in css          # and a faint wash across its row


def test_dashboard_body_outline_has_a_golden_trace_running_along_it(client, db):
    t = client.get("/dashboard").text
    assert 'class="body-trace"' in t and 'pathLength="100"' in t
    css = client.get("/static/css/app.css").text
    assert ".body-trace" in css and "body-trace-run" in css


def _week_with_plan(me, weekdays, logged_today):
    from app.models import WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource
    with SessionLocal() as s:
        plan = WorkoutPlan(owner_id=me, name="Trace Plan", source=WorkoutSource.MANUAL, started_on=date.today() - timedelta(days=30))
        s.add(plan); s.flush()
        day = WorkoutPlanDay(plan_id=plan.id, position=0, label="Full body", weekdays=weekdays)
        s.add(day); s.flush()
        if logged_today:
            s.add(WorkoutLog(owner_id=me, plan_day_id=day.id, log_date=date.today()))
        s.commit()
        return plan.id, day.id


def _drop_week(plan_id, day_id):
    from app.models import WorkoutLog, WorkoutPlan, WorkoutPlanDay
    with SessionLocal() as s:
        s.query(WorkoutLog).filter_by(plan_day_id=day_id).delete()
        s.query(WorkoutPlanDay).filter_by(plan_id=plan_id).delete()
        s.query(WorkoutPlan).filter_by(id=plan_id).delete()
        s.commit()


@pytest.mark.parametrize("scheduled,logged,trace", [("MTWRFSU", False, "wk-trace-go"), ("MTWRFSU", True, "wk-trace-done"), ("", False, "wk-trace-rest")])
def test_only_today_on_the_workout_card_has_a_light_trace_and_its_colour_follows_the_day(client, db, me, scheduled, logged, trace):
    plan_id, day_id = _week_with_plan(me, scheduled, logged)
    try:
        t = client.get("/dashboard").text
        from app import clock
        missed = clock.today().weekday() if scheduled else 0            # earlier days of the week that were due and not done
        assert t.count('class="wk-trace ') == 1 + missed and f"wk-trace {trace}" in t   # today's light, plus a white ring on each missed day
        assert t.count('class="wk-x"') == (7 if not scheduled else missed)   # every rest day is a bold red X, every missed day a black one
    finally:
        _drop_week(plan_id, day_id)


def test_water_pace_is_what_it_takes_from_the_current_level():
    from app.measurements.calculations import water_pace_left
    p = water_pace_left(100, 40, 14.0)               # 60 oz still to drink, 8 waking hours left (until 10 PM)
    assert p["hours_left"] == 8 and p["oz_per_hour"] == pytest.approx(7.5) and p["cups_per_hour"] == pytest.approx(7.5 / 8)
    assert not p["done"]
    assert water_pace_left(100, 100, 14.0)["done"] and water_pace_left(100, 130, 14.0)["done"]
    assert water_pace_left(100, 0, 23.5)["hours_left"] == 1          # late at night the figure stays sane
    assert water_pace_left(100, 0, 3.0)["hours_left"] == 16


def test_the_water_card_shows_fish_only_at_one_hundred_percent_and_the_pace_from_here(client, db):
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=uid, measured_at=date.today(), weight_lbs=200)); s.commit()
    try:
        t = client.get("/dashboard").text
        assert "water-fish" not in t.split("</style>")[-1] and "to reach the goal from here" in t
        client.post("/dashboard/water/log", data={"ounces": "100"})
        t = client.get("/dashboard").text
        assert 'class="water-fish water-fish-a"' in t and "Goal reached for today" in t
    finally:
        with SessionLocal() as s:
            from app.models import WaterLog
            s.query(WaterLog).filter_by(owner_id=uid).delete()
            s.query(BodyMeasurement).filter_by(owner_id=uid).delete()
            s.commit()


def test_the_alert_triangle_and_expiry_words_flash_and_the_charts_carry_a_trace(client, db):
    t = client.get("/dashboard").text
    assert "alert-flash-icon" in t
    assert 'src="/static/js/chart-trace.js' in t or "chart-trace.js" in t
    css = client.get("/static/css/app.css").text
    for name in ("alert-flash-soon", "alert-flash-expired", "node-energize", "fish-swim-right", ".water-fish-a svg { transform: scaleX(-1)"):
        assert name in css
    js = client.get("/static/js/chart-trace.js").text
    assert '"direction"' in js and "GREEN" in js and "RED" in js and "BLUE" in js
    assert 'mode === "blue") { const c' not in js and '"--pulse"' in js        # the Weight chart's points pulse too, in the light's colour


def test_next_up_names_the_day_and_then_the_weekday(client, db):
    from app import clock
    from app.models import WorkoutPlan, WorkoutPlanDay, WorkoutSource
    today = clock.today()
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        plan = WorkoutPlan(owner_id=uid, name="Nextup Plan", source=WorkoutSource.MANUAL, started_on=today - timedelta(days=30))
        s.add(plan)
        s.flush()
        letters = "MTWRFSU"[(today + timedelta(days=1)).weekday()]         # tomorrow's weekday letter, so it is still to come
        s.add(WorkoutPlanDay(plan_id=plan.id, position=0, label="Legs / Abs", weekdays=letters))
        s.commit()
        plan_id = plan.id
    try:
        t = html.unescape(client.get("/dashboard").text)
        tomorrow = today + timedelta(days=1)
        if tomorrow.isocalendar()[1] == today.isocalendar()[1]:             # a Sunday today puts tomorrow in the next week
            assert f"Next up: <strong>Legs / Abs</strong>, {tomorrow:%A}" in t
    finally:
        with SessionLocal() as s:
            s.query(WorkoutPlanDay).filter_by(plan_id=plan_id).delete()
            s.query(WorkoutPlan).filter_by(id=plan_id).delete()
            s.commit()


def _expired_vial(db, expired=True):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = InventoryItem(owner_id=uid, name="Disposetest", category=Category.MEDICINE, medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0, dose_value=2.0, dose_unit=DoseUnit.MG,
                          doses_total=5, date_mixed=date.today() - timedelta(days=40),
                          discard_by=date.today() - timedelta(days=2) if expired else date.today() + timedelta(days=20), volume_remaining_ml=2.0)
        s.add(vial)
        s.commit()
        return vial.id


def test_an_expired_vial_alert_has_a_dispose_button_with_a_cant_be_undone_question(client, db):
    vid = _expired_vial(db)
    try:
        t = html.unescape(client.get("/dashboard").text)
        assert f'data-dispose="/active-vials/{vid}/discard"' in t and "This Can’t Be Undone" in t and "data-dispose-no" in t
        r = client.post(f"/active-vials/{vid}/discard", data={"next": "/dashboard"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        assert f"/active-vials/{vid}/discard" not in client.get("/dashboard").text
    finally:
        with SessionLocal() as s:
            s.query(ActiveVial).filter_by(id=vid).delete()
            s.query(InventoryItem).filter_by(name="Disposetest").delete()
            s.commit()


def test_a_vial_that_is_not_expired_has_no_dispose_button(client, db):
    vid = _expired_vial(db, expired=False)
    try:
        assert "data-dispose=" not in client.get("/dashboard").text
    finally:
        with SessionLocal() as s:
            s.query(ActiveVial).filter_by(id=vid).delete()
            s.query(InventoryItem).filter_by(name="Disposetest").delete()
            s.commit()
