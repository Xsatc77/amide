from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.inventory import tracking
from app.models import Category, InventoryItem, Medium, Order, OrderItem, User
from sqlalchemy import select

TODAY = date(2026, 10, 6)


def order(**kw):
    base = dict(order_date=TODAY - timedelta(days=10), shipped_date=None, delivered_date=None, arrival_date=None)
    return SimpleNamespace(**{**base, **kw})


# ---------------------------------------------------------------- the timeline

@pytest.mark.parametrize("fields,status,states", [
    ({}, "waiting", ["current", "todo", "todo", "todo"]),
    ({"shipped_date": TODAY - timedelta(days=8)}, "in_transit", ["done", "current", "todo", "todo"]),
    ({"shipped_date": TODAY - timedelta(days=8), "delivered_date": TODAY - timedelta(days=1)}, "delivered", ["done", "done", "current", "todo"]),
    ({"shipped_date": TODAY - timedelta(days=8), "delivered_date": TODAY - timedelta(days=1), "arrival_date": TODAY}, "checked_in", ["done"] * 4),
])
def test_the_status_and_step_states_follow_the_dates(fields, status, states):
    t = tracking.build_timeline(order(**fields), TODAY)
    assert t["status"] == status and [s["state"] for s in t["steps"]] == states


def test_an_order_checked_in_without_a_delivered_date_counts_as_delivered_on_its_check_in_date():
    t = tracking.build_timeline(order(arrival_date=TODAY - timedelta(days=2)), TODAY)
    assert t["status"] == "checked_in" and t["steps"][2]["date"] == TODAY - timedelta(days=2) and t["steps"][1]["date"] is None


def test_days_count_from_the_last_step_reached():
    assert tracking.build_timeline(order(), TODAY)["days"] == 10
    assert tracking.build_timeline(order(shipped_date=TODAY - timedelta(days=4)), TODAY)["days"] == 4
    assert tracking.build_timeline(order(shipped_date=TODAY - timedelta(days=8), delivered_date=TODAY - timedelta(days=3)), TODAY)["days"] == 3


@pytest.mark.parametrize("shipped_days_ago,late", [(5, None), (16, "warn"), (21, "warn"), (22, "late")])
def test_an_unarrived_order_warns_then_is_late_against_the_delay_setting(shipped_days_ago, late):
    assert tracking.build_timeline(order(shipped_date=TODAY - timedelta(days=shipped_days_ago)), TODAY, 21)["late"] == late


def test_a_delivered_order_is_never_late():
    t = tracking.build_timeline(order(shipped_date=TODAY - timedelta(days=60), delivered_date=TODAY - timedelta(days=2)), TODAY, 21)
    assert t["late"] is None


# ---------------------------------------------------------------- links

@pytest.mark.parametrize("number,carrier", [("1Z999AA10123456784", "UPS"), ("9400111899223344556677", "USPS"), ("EA123456789US", "USPS"),
                                            ("123456789012", "FedEx"), ("1234567890", "DHL"), ("RB123456789CN", "International post"),
                                            ("1z 999aa1 0123456784", "UPS"), ("nonsense", None)])
def test_common_tracking_number_formats_are_recognized(number, carrier):
    found = tracking.detect_carrier(number)
    assert (found[0] if found else None) == carrier


def test_a_site_with_a_number_place_is_filled_in_a_plain_site_asks_to_copy_and_a_bare_number_gets_a_carrier_link():
    filled = tracking.tracking_link("https://track.example.com/?id={number}", "ab 12-3")
    assert (filled.url, filled.copy_first) == ("https://track.example.com/?id=AB123", False)
    plain = tracking.tracking_link("https://track.example.com/", "AB123")
    assert (plain.url, plain.copy_first) == ("https://track.example.com/", True)
    ups = tracking.tracking_link(None, "1Z999AA10123456784")
    assert ups.url == "https://www.ups.com/track?tracknum=1Z999AA10123456784" and ups.label == "Track on UPS"
    unknown = tracking.tracking_link(None, "LP00123456")
    assert "17track" in unknown.url and unknown.label == "Track on 17TRACK"
    assert tracking.tracking_link(None, None) is None and tracking.tracking_link("", "  ") is None


def test_the_number_in_a_link_is_url_safe():
    assert "%26" in tracking.tracking_link("https://x.example/?n={number}", "A&B").url


# ---------------------------------------------------------------- the page and quick actions

def make_order(uid, *, item_name="Test Zorvex", category=Category.MEDICINE, **kw):
    with SessionLocal() as s:
        item = InventoryItem(owner_id=uid, name=item_name, category=category, medium=Medium.LYOPHILIZED if category == Category.MEDICINE else None,
                             vial_size_mg=10 if category == Category.MEDICINE else None)
        s.add(item)
        s.flush()
        o = Order(**{**dict(order_date=date.today() - timedelta(days=5), vendor="Acme Labs"), **kw})
        s.add(o)
        s.flush()
        s.add(OrderItem(order_id=o.id, inventory_item_id=item.id, quantity=3))
        s.commit()
        return item.id, o.id


def test_the_orders_page_lists_my_orders_with_their_timeline_status_and_tracking(client, me):
    make_order(me, shipped_date=date.today() - timedelta(days=2), tracking_number="1Z999AA10123456784")
    make_order(me, item_name="Test Borealin", arrival_date=date.today(), shipped_date=date.today() - timedelta(days=3))
    page = client.get("/inventory/orders").text
    assert "Test Zorvex" in page and "In transit" in page and "Track on UPS" in page and "1Z999AA10123456784" in page
    assert "Test Borealin" not in page                                       # checked-in orders are under their own tab
    assert "Test Borealin" in client.get("/inventory/orders?status=checked_in").text
    assert "Test Borealin" in client.get("/inventory/orders?status=all").text


def test_the_orders_page_never_shows_another_persons_lines(client, me):
    from photo_helpers import other_client
    with other_client() as member:
        with SessionLocal() as s:
            other = s.scalar(select(User.id).where(User.username_key == "photoother"))
        make_order(other, item_name="Their private order")
        assert "Their private order" not in client.get("/inventory/orders").text
        assert "Their private order" in member.get("/inventory/orders").text
        with SessionLocal() as s2:                                       # the helper user is deleted when the block ends: remove their stock first
            s2.query(InventoryItem).filter_by(owner_id=other).delete()
            s2.query(Order).filter(~Order.items.any()).delete(synchronize_session=False)
            s2.commit()


def test_add_tracking_mark_shipped_and_mark_delivered_from_the_card(client, me):
    _, oid = make_order(me)
    client.post(f"/inventory/orders/{oid}/tracking", data={"tracking_number": " 1Z999AA10123456784 ", "tracking_site": "https://track.example.com/?id={number}"})
    shipped = (date.today() - timedelta(days=3)).isoformat()
    client.post(f"/inventory/orders/{oid}/ship", data={"shipped_date": shipped})
    delivered = date.today().isoformat()
    client.post(f"/inventory/orders/{oid}/deliver", data={"delivered_date": delivered})
    with SessionLocal() as s:
        o = s.get(Order, oid)
        assert (o.tracking_number, o.tracking_site, o.shipped_date.isoformat(), o.delivered_date.isoformat(), o.arrival_date) == (
            "1Z999AA10123456784", "https://track.example.com/?id={number}", shipped, delivered, None)
    page = client.get("/inventory/orders").text
    assert "Delivered, needs check-in" in page and "https://track.example.com/?id=1Z999AA10123456784" in page


@pytest.mark.parametrize("path,data", [("ship", {"shipped_date": "2999-01-01"}), ("ship", {"shipped_date": "2001-01-01"}), ("ship", {"shipped_date": "nope"}),
                                       ("deliver", {"delivered_date": "2999-01-01"}), ("deliver", {"delivered_date": "2001-01-01"}),
                                       ("tracking", {"tracking_site": "javascript:alert(1)"}), ("tracking", {"tracking_number": "x" * 101})])
def test_bad_dates_and_sites_are_refused_with_a_message_and_change_nothing(client, me, path, data):
    _, oid = make_order(me)
    r = client.post(f"/inventory/orders/{oid}/{path}", data=data)
    assert r.status_code == 422
    with SessionLocal() as s:
        o = s.get(Order, oid)
        assert (o.shipped_date, o.delivered_date, o.tracking_site, o.tracking_number) == (None, None, None, None)


def test_a_checked_in_order_cannot_be_moved_back_and_someone_elses_order_is_not_found(client, me):
    from photo_helpers import other_client
    _, oid = make_order(me, arrival_date=date.today(), shipped_date=date.today() - timedelta(days=3))
    assert client.post(f"/inventory/orders/{oid}/deliver", data={"delivered_date": date.today().isoformat()}).status_code == 422
    with other_client() as member:
        assert member.post(f"/inventory/orders/{oid}/tracking", data={"tracking_number": "x"}).status_code == 404
        assert member.post(f"/inventory/orders/{oid}/ship", data={"shipped_date": date.today().isoformat()}).status_code == 404


def test_the_card_links_to_check_in_and_escapes_vendor_names(client, me):
    item_id, oid = make_order(me, vendor="<b>Acme</b>")
    page = client.get("/inventory/orders").text
    assert f'href="/inventory/{item_id}?checkin={oid}"' in page and "<b>Acme</b>" not in page and "&lt;b&gt;Acme" in page
