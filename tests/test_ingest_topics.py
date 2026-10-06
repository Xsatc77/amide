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


# ---------------------------------------------------------------- skip words (pure)

from app.ingest import skip


@pytest.mark.parametrize("raw,expected", [("UK, EU", ["uk", "eu"]), ("uk\nEU,uk", ["uk", "eu"]), (" ,, ", []), (None, [])])
def test_skip_words_are_split_lowered_and_deduplicated(raw, expected):
    assert skip.parse_skip_words(raw) == expected


def test_too_long_or_too_many_skip_words_are_refused():
    with pytest.raises(ValueError):
        skip.parse_skip_words("x" * 41)
    with pytest.raises(ValueError):
        skip.parse_skip_words(",".join(f"w{n}" for n in range(21)))


@pytest.mark.parametrize("text,hit", [("UK stock list", "uk"), ("to uk only", "uk"), ("Duke prices", None), ("Prices (UK)", "uk"),
                                      ("ukulele", None), ("EU-warehouse", "eu"), ("price list 2026", None)])
def test_skip_words_match_whole_words_only_in_any_case(text, hit):
    assert skip.find_skip_word(["uk", "eu"], [text]) == hit


# ---------------------------------------------------------------- the API

from app.models import Vendor
from ingest_helpers import anon_client, bearer, make_token, pdf_bytes

NOW = "2026-10-06T14:30:00+00:00"


def mapped(db, **kw):
    vendor = db.query(Vendor).filter_by(name="Acme Labs").first() or Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    return make_source(db, vendor=vendor, **kw)


def test_the_watcher_registers_topics_ticking_those_named_like_the_follow_words_and_titles_refresh(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        body = {"title": "Acme group", "topics": [{"id": "7", "title": "US warehouse"}, {"id": 8, "title": "Chatter"}, {"id": 9, "title": "UK Price List"}]}
        assert c.put("/api/ingest/sources/-100777", json=body, headers=bearer(secret)).status_code == 200
        body["topics"][0]["title"] = "US Prices"
        c.put("/api/ingest/sources/-100777", json=body, headers=bearer(secret))
    rows = {t.topic_id: (t.title, t.enabled) for t in db.query(IngestTopic)}
    assert rows == {"7": ("US Prices", True), "8": ("Chatter", False), "9": ("UK Price List", True)}   # a later rename never changes a tick
    db.query(IngestTopic).delete()
    src = db.query(IngestSource).one()
    src.skip_words = "uk"
    db.commit()
    body["topics"][0]["title"] = "US warehouse"
    with anon_client() as c:
        c.put("/api/ingest/sources/-100777", json=body, headers=bearer(secret))
    assert {t.topic_id: t.enabled for t in db.query(IngestTopic)} == {"7": True, "8": False, "9": False}   # skip words win


def test_bad_topic_lists_are_refused(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        for topics in ("x", [{"id": "a b", "title": "t"}], [{"id": "1"}], [{"id": str(n), "title": "t"} for n in range(201)]):
            r = c.put("/api/ingest/sources/-1", json={"title": "g", "topics": topics}, headers=bearer(secret))
            assert r.status_code == 422, topics


def test_the_source_list_says_whole_group_or_only_the_enabled_topics(db, me):
    secret = make_token(db, me)
    mapped(db, chat_id="-1", title="Whole")
    t = mapped(db, chat_id="-2", title="Some")
    t.topics_only = True
    db.add_all([IngestTopic(source_id=t.id, topic_id="7", title="a", enabled=True), IngestTopic(source_id=t.id, topic_id="8", title="b", enabled=False)])
    db.commit()
    with anon_client() as c:
        rows = {r["chat_id"]: r["topics"] for r in c.get("/api/ingest/sources", headers=bearer(secret)).json()}
    assert rows == {"-1": None, "-2": ["7"]}


def test_a_message_from_an_unselected_topic_is_ignored_and_not_stored_and_a_selected_one_keeps_its_topic(db, me):
    secret = make_token(db, me)
    s = mapped(db)
    s.topics_only = True
    db.add(IngestTopic(source_id=s.id, topic_id="7", title="US Price List", enabled=True))
    db.commit()
    with anon_client() as c:
        def send(topic):
            return c.post("/api/ingest/messages", data={"chat_id": s.chat_id, "message_id": f"m{topic}", "date": NOW, "topic_id": topic,
                                                       "topic_title": f"Topic {topic}"},
                          files=[("files", ("a.pdf", pdf_bytes() + topic.encode(), "application/pdf"))], headers=bearer(secret))
        wrong = send("8").json()["results"]
        right = send("7").json()["results"]
        no_topic = c.post("/api/ingest/messages", data={"chat_id": s.chat_id, "message_id": "x", "date": NOW},
                          files=[("files", ("b.pdf", pdf_bytes() + b"z", "application/pdf"))], headers=bearer(secret)).json()["results"]
    assert wrong == [{"status": "ignored", "reason": "topic not followed", "item_id": None}]
    assert right[0]["status"] == "received" and no_topic[0]["status"] == "ignored"
    item = db.query(IngestItem).one()
    assert (item.topic_id, item.topic_title) == ("7", "Topic 7")
    assert db.query(IngestTopic).filter_by(topic_id="8").one().enabled is False


def test_a_whole_group_stores_the_topic_of_each_message_and_photos_of_different_topics_never_cluster(db, me):
    from ingest_helpers import png_bytes
    secret = make_token(db, me)
    s = mapped(db)
    with anon_client() as c:
        for n, topic in enumerate(("7", "8"), start=1):
            c.post("/api/ingest/messages", data={"chat_id": s.chat_id, "message_id": str(n), "date": NOW, "topic_id": topic},
                   files=[("files", (f"{n}.png", png_bytes(n), "image/png"))], headers=bearer(secret))
    keys = {i.topic_id: i.group_key for i in db.query(IngestItem)}
    assert keys["7"] != keys["8"]
