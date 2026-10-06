from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app import config
from app.models import DashboardDismissal, IngestItem, IngestSource, IngestToken, Vendor, naive_utcnow


def source(db, **kw):
    s = IngestSource(**{**dict(platform="telegram", chat_id="-100123", title="Acme group"), **kw})
    db.add(s)
    db.commit()
    return s


def item(db, src, **kw):
    row = dict(source_id=src.id, message_id="1", group_key=f"{src.id}:m:1", received_at=naive_utcnow(), kind="pdf", file_hash="a" * 64,
               status="received")
    i = IngestItem(**{**row, **kw})
    db.add(i)
    db.commit()
    return i


def test_a_source_defaults_to_active_disabled_and_unmapped(db):
    s = source(db)
    assert (s.enabled, s.state, s.vendor_id, s.default_warehouse) == (False, "active", None, None)


def test_a_chat_is_one_source_per_platform(db):
    source(db)
    db.add(IngestSource(platform="telegram", chat_id="-100123", title="again"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_an_item_round_trips_and_the_same_message_and_file_cannot_repeat(db):
    s = source(db)
    i = item(db, s, filename="list.pdf", caption="new prices")
    assert (i.status, i.rows_found, i.price_list_id, i.created_at is not None) == ("received", None, None, True)
    db.add(IngestItem(source_id=s.id, message_id="1", group_key="x", received_at=naive_utcnow(), kind="pdf", file_hash="a" * 64, status="received"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    item(db, s, file_hash="b" * 64)                                           # same message, different file: fine


def test_the_database_refuses_an_unknown_kind_status_warehouse_or_state(db):
    s = source(db)
    for bad in (dict(kind="docx"), dict(status="maybe")):
        db.add(IngestItem(source_id=s.id, message_id="9", group_key="g", received_at=naive_utcnow(), file_hash="c" * 64,
                          **{**dict(kind="pdf", status="received"), **bad}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    for bad in (dict(default_warehouse="mars"), dict(state="lost")):
        db.add(IngestSource(platform="telegram", chat_id="-100999", title="x", **bad))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_deleting_a_vendor_unmaps_the_source_and_deleting_a_source_removes_its_items(db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    s = source(db, vendor_id=vendor.id)
    item(db, s)
    db.delete(vendor)
    db.commit()
    db.refresh(s)
    assert s.vendor_id is None
    db.delete(s)
    db.commit()
    assert db.query(IngestItem).count() == 0


def test_a_token_hash_is_unique_and_a_dismissal_is_once_per_person_and_alert(db, me):
    db.add(IngestToken(owner_id=me, label="Watcher", prefix="amide_ing_ab", token_hash="h" * 64))
    db.commit()
    db.add(IngestToken(owner_id=me, label="Other", prefix="amide_ing_cd", token_hash="h" * 64))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(DashboardDismissal(user_id=me, alert_key="newlist:1"))
    db.commit()
    db.add(DashboardDismissal(user_id=me, alert_key="newlist:1"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_the_ingest_folder_exists_and_the_worker_is_off_under_test():
    config.ensure_dirs()
    assert config.INGEST_DIR.is_dir() and config.INGEST_WORKER_ENABLED is False
