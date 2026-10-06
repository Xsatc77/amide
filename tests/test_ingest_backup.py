import pytest

from app import config
from app.backup import load as loader
from app.backup import sections
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import IngestItem, IngestSource, IngestToken, DashboardDismissal, Vendor, naive_utcnow
from ingest_helpers import make_source

KEPT = b"%PDF-1.4 kept for review"


def export(db, me, kind="backup"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=["ingest"]), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def seed(db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    source = make_source(db, vendor=vendor, default_warehouse="us", title="Acme chat")
    (config.INGEST_DIR / "kept-file").write_bytes(KEPT)
    db.add(IngestItem(source_id=source.id, message_id="7", group_key="g7", received_at=naive_utcnow(), kind="pdf", file_hash="7" * 64,
                      filename="list.pdf", stored_file="kept-file", status="needs_review", reason="the warehouse was not stated",
                      vendor_id=vendor.id, warehouse="china", rows_found=12, created_at=naive_utcnow()))
    db.commit()
    return vendor, source


def test_the_registry_knows_every_ingest_table_and_declares_what_it_leaves_out():
    assert {"ingest_sources", "ingest_items"} <= {t.name for s in sections.SECTIONS.values() for t in s.tables}
    assert {"ingest_tokens", "dashboard_dismissals"} <= sections.NOT_BACKED_UP
    assert sections.SECTIONS["ingest"].shareable is False


def test_the_inbox_cannot_be_put_in_a_share_file(client, db, me):
    seed(db)
    with pytest.raises(BackupError):
        export(db, me, kind="share")


def test_sources_items_and_kept_files_survive_a_backup_and_load(client, db, me):
    vendor, source = seed(db)
    archive = export(db, me)
    assert any(name.startswith("files/ingest/") for name in archive.names())
    for f in config.INGEST_DIR.glob("*"):
        f.unlink()
    db.query(IngestItem).delete()
    db.query(IngestSource).delete()
    db.commit()
    run(db, me, archive, {"ingest": loader.ADD})
    db.expire_all()
    restored = db.query(IngestSource).one()
    assert (restored.title, restored.default_warehouse, restored.vendor_id, restored.chat_id) == ("Acme chat", "us", vendor.id, "-100123")
    item = db.query(IngestItem).one()
    assert (item.status, item.reason, item.vendor_id, item.source_id) == ("needs_review", "the warehouse was not stated", vendor.id, restored.id)
    assert (config.INGEST_DIR / item.stored_file).read_bytes() == KEPT


def test_tokens_and_dismissals_are_never_in_a_backup(client, db, me):
    seed(db)
    db.add(IngestToken(owner_id=me, label="Watcher", prefix="amide_ing_ab", token_hash="t" * 64))
    db.add(DashboardDismissal(user_id=me, alert_key="newlist:1"))
    db.commit()
    names = " ".join(export(db, me).names())
    assert "ingest_tokens" not in names and "dashboard_dismissals" not in names
