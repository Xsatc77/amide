import html
from datetime import date, datetime, timedelta, timezone

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


def test_dashboard_hides_water_goal_with_no_weight_logged(client, db):
    t = client.get("/dashboard").text
    assert 'id="dash-water-heading"' not in t


def test_dashboard_shows_body_panel_with_weight_chart(client, db):
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=uid, measured_at=date.today(), weight_lbs=180))
        s.commit()
    try:
        t = client.get("/dashboard").text
        assert 'id="body-panel-heading"' in t
        assert "Weight (Lbs)" in t
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
