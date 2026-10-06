from datetime import timedelta

from app.ingest import alerts
from app.models import DashboardDismissal, IngestItem, IngestSource, User, Vendor, naive_utcnow
from ingest_helpers import make_source
from test_ingest_process import NOW, run, text_item


def vendor_row(db):
    v = Vendor(name="Acme Labs")
    db.add(v)
    db.commit()
    return v


def imported(db, vendor, **kw):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, **kw)
    run()
    return source


def test_an_ingest_import_raises_a_new_list_alert_for_everyone(client, db, me):
    vendor = vendor_row(db)
    imported(db, vendor)
    items = alerts.new_list_alerts(db, me, NOW)
    assert [a["text"] for a in items] == ["Acme Labs released new price list."] and items[0]["vendor_id"] == vendor.id
    for item in db.query(IngestItem):
        item.decided_at = naive_utcnow()                 # the page judges "this week" by the real clock
    db.commit()
    page = client.get("/dashboard").text
    assert ">Acme Labs</a> released new price list." in page


def test_an_old_import_or_an_undone_one_raises_nothing(db, me):
    vendor = vendor_row(db)
    imported(db, vendor)
    assert alerts.new_list_alerts(db, me, NOW + timedelta(days=8)) == []
    for item in db.query(IngestItem):
        item.status = "undone"
        item.price_list_id = None
    db.commit()
    assert alerts.new_list_alerts(db, me, NOW) == []


def test_each_person_dismisses_their_own(client, db, me):
    imported(db, vendor_row(db))
    key = alerts.new_list_alerts(db, me, NOW)[0]["key"]
    client.post("/dashboard/alerts/dismiss", data={"key": key})
    assert alerts.new_list_alerts(db, me, NOW) == [] and db.query(DashboardDismissal).count() == 1
    other = User(username="second", username_key="second", password_hash="x")
    db.add(other)
    db.commit()
    try:
        assert len(alerts.new_list_alerts(db, other.id, NOW)) == 1
    finally:
        db.query(DashboardDismissal).delete()
        db.delete(other)
        db.commit()


def test_a_group_going_away_alerts_the_administrator_only_until_acknowledged(client, db, me):
    source = make_source(db, vendor=vendor_row(db), title="Acme chat")
    admin = db.get(User, me)
    assert alerts.group_gone_alerts(db, admin) == []
    source.state, source.state_changed_at = "gone", naive_utcnow() - timedelta(hours=1)
    db.commit()
    shown = alerts.group_gone_alerts(db, admin)
    assert [a["text"] for a in shown] == ["Acme Labs Telegram group is no longer active."]
    assert alerts.group_gone_alerts(db, User(username="x", is_admin=False)) == []
    assert "Acme Labs Telegram group is no longer active." in client.get("/dashboard").text
    client.post("/dashboard/alerts/dismiss", data={"key": shown[0]["key"]})
    db.expire_all()
    assert alerts.group_gone_alerts(db, db.get(User, me)) == []
    source = db.get(IngestSource, source.id)
    source.state_changed_at = naive_utcnow() + timedelta(days=1)               # it goes away again later: the alert returns
    db.commit()
    assert len(alerts.group_gone_alerts(db, db.get(User, me))) == 1
    source.state = "active"
    db.commit()
    assert alerts.group_gone_alerts(db, db.get(User, me)) == []


def test_an_unmapped_group_uses_its_title_and_a_bad_key_is_refused(client, db, me):
    source = make_source(db, vendor=None, title="Quillamine chat")
    source.state, source.state_changed_at = "gone", naive_utcnow()
    db.commit()
    assert alerts.group_gone_alerts(db, db.get(User, me))[0]["text"] == "Quillamine chat Telegram group is no longer active."
    assert client.post("/dashboard/alerts/dismiss", data={"key": "bogus"}).status_code == 422
    assert client.post("/dashboard/alerts/dismiss", data={"key": "newlist:abc"}).status_code == 422
