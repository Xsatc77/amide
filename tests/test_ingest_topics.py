import pytest
from sqlalchemy.exc import IntegrityError

from app.models import IngestItem, IngestSource, IngestTopic, naive_utcnow
from ingest_helpers import make_source


def test_a_source_defaults_to_the_whole_group_with_no_skip_words_and_the_default_follow_words(db):
    s = make_source(db)
    assert (s.topics_only, s.skip_words, s.follow_words) == (False, None, "price, warehouse")


def test_topics_are_unique_per_group_default_off_and_go_with_the_group(db):
    s = make_source(db)
    db.add(IngestTopic(source_id=s.id, topic_id="7", title="US Price List"))
    db.commit()
    assert db.query(IngestTopic).one().enabled is False
    db.add(IngestTopic(source_id=s.id, topic_id="7", title="again"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.delete(s)
    db.commit()
    assert db.query(IngestTopic).count() == 0


def test_an_item_can_carry_its_topic(db):
    s = make_source(db)
    db.add(IngestItem(source_id=s.id, message_id="1", group_key="g", received_at=naive_utcnow(), kind="text", file_hash="1" * 64,
                      status="received", topic_id="7", topic_title="US Price List"))
    db.commit()
    assert db.query(IngestItem).one().topic_title == "US Price List"


def test_topics_and_the_new_group_settings_survive_a_backup_and_load(client, db, me):
    from app.backup import load as loader
    from app.backup.archive import read_archive
    from app.backup.export import build_archive
    s = make_source(db, title="Acme chat")
    s.topics_only, s.skip_words, s.follow_words = True, "uk, eu", "price"
    db.add(IngestTopic(source_id=s.id, topic_id="7", title="US Price List", enabled=True))
    db.commit()
    archive = read_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=["ingest"]), max_bytes=50_000_000)
    db.query(IngestTopic).delete()
    db.query(IngestItem).delete()
    db.query(IngestSource).delete()
    db.commit()
    loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan={"ingest": loader.ADD})
    db.expire_all()
    source = db.query(IngestSource).one()
    topic = db.query(IngestTopic).one()
    assert (source.topics_only, source.skip_words, source.follow_words) == (True, "uk, eu", "price")
    assert (topic.topic_id, topic.title, topic.enabled, topic.source_id) == ("7", "US Price List", True, source.id)
