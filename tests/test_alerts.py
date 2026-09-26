from datetime import date, timedelta

from app.alerts import EXPIRING_SOON_DAYS, expiration_alerts, low_stock_alerts, shipment_alerts


class _Item:
    def __init__(self, id, name, available_count, low_stock_threshold, expiration_date=None):
        self.id = id
        self.name = name
        self.available_count = available_count
        self.low_stock_threshold = low_stock_threshold
        self.expiration_date = expiration_date


class _Vial:
    def __init__(self, id, item_name, discard_by, discarded_at=None):
        self.id = id
        self.item_name = item_name
        self.discard_by = discard_by
        self.discarded_at = discarded_at


class _Order:
    def __init__(self, id, vendor, order_date, shipped_date, arrival_date):
        self.id = id
        self.vendor = vendor
        self.order_date = order_date
        self.shipped_date = shipped_date
        self.arrival_date = arrival_date


def test_low_stock_uses_item_threshold_over_default():
    items = [_Item(1, "A", available_count=2, low_stock_threshold=3)]
    alerts = low_stock_alerts(items, default_threshold=5)
    assert len(alerts) == 1 and alerts[0]["item_id"] == 1


def test_low_stock_explicit_zero_is_not_unset():
    # available_count=0, threshold explicitly 0 -> 0 <= 0 -> still alerts (item genuinely out).
    items = [_Item(1, "A", available_count=0, low_stock_threshold=0)]
    alerts = low_stock_alerts(items, default_threshold=5)
    assert len(alerts) == 1


def test_low_stock_falls_back_to_default_when_threshold_none():
    items = [_Item(1, "A", available_count=4, low_stock_threshold=None)]
    assert low_stock_alerts(items, default_threshold=5) == [
        {"item_id": 1, "name": "A", "available_count": 4, "threshold": 5}
    ]
    assert low_stock_alerts(items, default_threshold=3) == []


def test_expiration_soon_and_expired_vials():
    today = date(2026, 9, 26)
    vials = [
        _Vial(1, "A", discard_by=today - timedelta(days=1)),  # expired
        _Vial(2, "B", discard_by=today + timedelta(days=EXPIRING_SOON_DAYS)),  # soon (boundary)
        _Vial(3, "C", discard_by=today + timedelta(days=EXPIRING_SOON_DAYS + 1)),  # not yet
        _Vial(4, "D", discard_by=today - timedelta(days=5), discarded_at=today),  # already discarded, skip
    ]
    alerts = expiration_alerts(vials=vials, items=[], today=today)
    by_id = {a["id"]: a["severity"] for a in alerts}
    assert by_id == {1: "expired", 2: "soon"}


def test_expiration_covers_sealed_stock_too():
    today = date(2026, 9, 26)
    items = [_Item(1, "A", available_count=1, low_stock_threshold=None,
                   expiration_date=today - timedelta(days=1))]
    alerts = expiration_alerts(vials=[], items=items, today=today)
    assert alerts == [{"kind": "item", "id": 1, "name": "A", "severity": "expired"}]


def test_expiration_ignores_depleted_sealed_stock():
    today = date(2026, 9, 26)
    items = [_Item(1, "A", available_count=0, low_stock_threshold=None,
                   expiration_date=today - timedelta(days=1))]
    assert expiration_alerts(vials=[], items=items, today=today) == []


def test_shipment_over_threshold_uses_shipped_date_when_present():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=30),
                     shipped_date=today - timedelta(days=25), arrival_date=None)]
    alerts = shipment_alerts(orders, today=today, threshold_days=21)
    assert len(alerts) == 1 and alerts[0]["order_id"] == 1


def test_shipment_falls_back_to_order_date_when_unshipped():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=25),
                     shipped_date=None, arrival_date=None)]
    assert len(shipment_alerts(orders, today=today, threshold_days=21)) == 1


def test_shipment_arrived_never_alerts():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=60),
                     shipped_date=today - timedelta(days=55), arrival_date=today - timedelta(days=1))]
    assert shipment_alerts(orders, today=today, threshold_days=21) == []
