import re
from datetime import date

from app import config
from app.models import IngestItem, IngestSource, IngestToken, PriceList, Vendor
from ingest_helpers import make_source
from photo_helpers import other_client
from test_ingest_process import LINES, NOW, run, text_item


def vendor_row(db, name="Acme Labs"):
    v = Vendor(name=name)
    db.add(v)
    db.commit()
    return v


def test_only_the_administrator_sees_the_inbox(client, db):
    assert client.get("/settings/ingest").status_code == 200
    with other_client() as member:
        for path in ("/settings/ingest", "/settings/ingest/items/1/original"):
            assert member.get(path).status_code == 404
        for path in ("/settings/ingest/tokens", "/settings/ingest/tokens/1/revoke", "/settings/ingest/sources/1", "/settings/ingest/items/1/approve",
                     "/settings/ingest/items/1/reject", "/settings/ingest/items/1/undo"):
            assert member.post(path, data={}).status_code == 404


def test_a_token_secret_is_shown_once_and_can_be_revoked(client, db):
    page = client.post("/settings/ingest/tokens", data={"label": "Home PC"})
    assert page.status_code == 200
    secret = re.search(r"amide_ing_[A-Za-z0-9_-]{30,}", page.text).group(0)
    again = client.get("/settings/ingest")
    assert secret not in again.text and "Home PC" in again.text
    token = db.query(IngestToken).one()
    assert secret != token.token_hash
    client.post(f"/settings/ingest/tokens/{token.id}/revoke", data={})
    db.expire_all()
    assert db.get(IngestToken, token.id).revoked_at is not None


def test_a_source_can_be_mapped_enabled_and_given_a_default_warehouse(client, db):
    vendor = vendor_row(db)
    source = make_source(db, vendor=None, enabled=False)
    client.post(f"/settings/ingest/sources/{source.id}", data={"vendor_id": str(vendor.id), "default_warehouse": "us", "enabled": "on"})
    db.expire_all()
    db.refresh(source)
    assert (source.vendor_id, source.default_warehouse, source.enabled) == (vendor.id, "us", True)
    client.post(f"/settings/ingest/sources/{source.id}", data={"vendor_id": "", "default_warehouse": ""})
    db.expire_all()
    db.refresh(source)
    assert (source.vendor_id, source.default_warehouse, source.enabled) == (None, None, False)


def test_a_vendor_can_be_created_from_the_group_title(client, db):
    source = make_source(db, vendor=None, enabled=False, title="Zephyr Peptides")
    client.post(f"/settings/ingest/sources/{source.id}", data={"new_vendor": "1"})
    db.expire_all()
    db.refresh(source)
    assert db.get(Vendor, source.vendor_id).name == "Zephyr Peptides"


def test_the_inbox_lists_items_with_their_reason_and_escapes_message_text(client, db):
    source = make_source(db, vendor=None)
    text_item(db, source, caption_extra="<script>alert(1)</script>")
    run()
    page = client.get("/settings/ingest").text
    assert "choose the vendor" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_approve_reject_and_undo_from_the_inbox(client, db):
    vendor = vendor_row(db)
    source = make_source(db, vendor=vendor)
    item = text_item(db, source)
    run()
    r = client.post(f"/settings/ingest/items/{item.id}/approve", data={"vendor_id": str(vendor.id), "warehouse": "us", "list_date": "2026-10-05"},
                    follow_redirects=False)
    assert r.status_code == 303 and db.query(PriceList).count() == 1
    client.post(f"/settings/ingest/items/{item.id}/undo", data={})
    db.expire_all()
    assert db.query(PriceList).count() == 0
    second = text_item(db, source, message_id="2", text="\n".join(LINES), file_hash="2" * 64)
    run()
    client.post(f"/settings/ingest/items/{second.id}/reject", data={})
    db.expire_all()
    assert db.get(IngestItem, second.id).status == "rejected"


def test_a_bad_form_value_is_refused_with_a_message_not_a_server_error(client, db):
    vendor = vendor_row(db)
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    for data in ({"vendor_id": "x", "warehouse": "us", "list_date": "2026-10-05"}, {"vendor_id": str(vendor.id), "warehouse": "mars", "list_date": "2026-10-05"},
                 {"vendor_id": str(vendor.id), "warehouse": "us", "list_date": "nope"}):
        assert client.post(f"/settings/ingest/items/{item.id}/approve", data=data).status_code == 422
    assert db.query(PriceList).count() == 0
    assert client.post("/settings/ingest/items/9999/approve", data={"vendor_id": "1", "warehouse": "us", "list_date": "2026-10-05"}).status_code == 404


def test_the_original_is_downloadable_by_the_administrator_only_while_it_is_kept(client, db):
    source = make_source(db, vendor=vendor_row(db))
    (config.INGEST_DIR / "keepme").write_bytes(b"%PDF-1.4 hello")
    item = IngestItem(source_id=source.id, message_id="1", group_key="g", received_at=NOW, kind="pdf", file_hash="9" * 64, filename="x.pdf",
                      stored_file="keepme", status="needs_review", created_at=NOW)
    db.add(item)
    db.commit()
    r = client.get(f"/settings/ingest/items/{item.id}/original")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 hello" and r.headers["cache-control"] == "no-store"
    assert client.get("/settings/ingest/items/9999/original").status_code == 404
