# Ingest Topics and Skip Words (Part C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner follow only chosen Telegram topics of a forum group (topics named like "US warehouse" or "Price List" are followed automatically), use the topic name as context, and set aside lists that mention skip words such as a region the owner cannot order from.

**Architecture:** One migration adds topics, a per-group "selected topics only" switch, skip words and topic fields on items. Amide's token API carries topics both ways and re-checks every message's topic. Skip words and topic titles are applied in the existing processing step. The inbox gets a Follow control, topic ticks and a skip-words field. The watcher registers topics, reads only the enabled ones with a position per topic, and sends the topic with each message.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic (SQLite), Jinja2, Telethon 1.45 (`GetForumTopicsRequest`, `iter_messages(reply_to=...)`), httpx.

**Spec:** `docs/superpowers/specs/2026-10-06-ingest-topics-design.md`

## Global Constraints

- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (baseline 1838 passing); long runs with `run_in_background`. Watcher tests: `watcher/tests`.
- **No real vendor, group, topic or price-list names anywhere in tracked files or commit messages**; tests use invented names (Acme, Zephyr, Borealis, Quillamine, "US Price List", "Chatter"). Before every push run `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` and read the whole output (only accepted hit: the old phrase in `docs/superpowers/specs/2026-09-29-library-redesign-design.md`). Never write the owner's Telegram credentials anywhere.
- Groups without topics or with `topics_only` off behave exactly as before. The watcher accepts an old Amide answer (a source row with no `topics` key) as "whole group".
- Skip words are plain text, matched as whole words, case-insensitive; each at most 40 characters, at most 20 words.
- Only the administrator edits topic selection and skip words (404 for others, as the rest of the inbox).
- Telethon is imported only in `watcher/telethon_client.py`. Write Python with the Write tool or Edit, not shell heredocs (backslashes are mangled; a `\b` can become a backspace character: after writing any regex, grep for `\x08`).

## Review Focus

1. A message from an unselected topic stored or read; a list from a skipped region imported anyway. (Tasks 2, 3, 5)
2. Forum edge cases: the General topic (id 1), a closed or deleted topic, a group that is not a forum. (Task 5)
3. Two topics of one group sharing a position, or photos of different topics clustered into one list. (Tasks 2, 5)
4. Skip words that match too much ("UK" inside "Duke") or too little (case, punctuation). (Task 3)
5. An old watcher or old Amide on either side of the changed API. (Tasks 2, 5)

## File Structure

- Create `migrations/versions/0041_ingest_topics.py`, `app/ingest/skip.py`, `tests/test_ingest_topics.py`, `watcher/tests/test_topics.py`.
- Modify `app/models.py`, `app/backup/sections.py`, `app/routers/ingest_api.py`, `app/ingest/store.py`, `app/ingest/process.py`, `app/routers/ingest_admin.py`, `app/templates/settings/ingest.html`, `watcher/ports.py`, `watcher/amide_client.py`, `watcher/runner.py`, `watcher/queue.py` (no change needed; Payload defaults handle it), `watcher/telethon_client.py`, `watcher/tests/fakes.py`, `watcher/tests/test_runner.py`, `tests/test_watcher_amide_client.py`, `watcher/README.md`, `docs/ROADMAP.md`.

---

### Task 1: Schema, models and backup

**Files:** Create `migrations/versions/0041_ingest_topics.py`, `tests/test_ingest_topics.py`; Modify `app/models.py`, `app/backup/sections.py`.

**Interfaces:**
- Produces model `IngestTopic(id, source_id, topic_id: str, title: str, enabled: bool=False, created_at)`; columns `IngestSource.topics_only: bool = False`, `IngestSource.skip_words: str | None`, `IngestSource.follow_words: str | None` (default `price, warehouse`; a topic whose name contains one of these words starts ticked); `IngestItem.topic_id: str | None`, `IngestItem.topic_title: str | None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_topics.py`, first block)

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import IngestItem, IngestSource, IngestTopic, naive_utcnow
from ingest_helpers import make_source


def test_a_source_defaults_to_the_whole_group_with_no_skip_words(db):
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
```
Plus (backup) in the same file:
```python
def test_topics_and_skip_words_survive_a_backup_and_load(client, db, me):
    from app.backup import load as loader
    from app.backup.archive import read_archive
    from app.backup.export import build_archive
    s = make_source(db, title="Acme chat")
    s.topics_only, s.skip_words = True, "UK, EU"
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
    assert (source.topics_only, source.skip_words, topic.topic_id, topic.title, topic.enabled, topic.source_id) == (
        True, "UK, EU", "7", "US Price List", True, source.id)
```

- [ ] **Step 2: Run to verify it fails** (`ImportError: IngestTopic`).

- [ ] **Step 3: Implement.** `app/models.py`: add to `IngestSource` `topics_only: Mapped[bool] = mapped_column(Boolean, default=False)`, `skip_words: Mapped[str | None] = mapped_column(String(1200))` and `follow_words: Mapped[str | None] = mapped_column(String(1200), default="price, warehouse")`; to `IngestItem` `topic_id: Mapped[str | None] = mapped_column(String(32))` and `topic_title: Mapped[str | None] = mapped_column(String(200))`; new class after `IngestSource`:
```python
class IngestTopic(Base):
    """A topic (named thread) of a forum group. New topics are off until the administrator ticks them."""
    __tablename__ = "ingest_topics"
    __table_args__ = (UniqueConstraint("source_id", "topic_id", name="uq_ingest_topic"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("ingest_sources.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
```
`migrations/versions/0041_ingest_topics.py` (revision `0041`, down `0040`): create `ingest_topics` (same columns, `ix_ingest_topics_source_id`, unique constraint `uq_ingest_topic`); `op.add_column('ingest_sources', sa.Column('topics_only', sa.Boolean(), nullable=False, server_default=sa.false()))`, `skip_words` String(1200) nullable, `follow_words` String(1200) nullable with `server_default='price, warehouse'` (existing groups get the default too); `op.add_column('ingest_items', ...)` for `topic_id` String(32) and `topic_title` String(200); `downgrade` drops them (use `op.batch_alter_table` for the drops). `app/backup/sections.py`: in the `ingest` section tuple add `Tbl("ingest_topics")` after `Tbl("ingest_sources")` (parents before children is handled by `ordered`).

- [ ] **Step 4: Run to verify it passes** (new file, `tests/test_migrations.py`, `tests/test_backup_export.py`, `tests/test_ingest_backup.py`), then the full suite.

- [ ] **Step 5: Commit** `feat: ingest topics, selected-topics switch and skip words schema`.

---

### Task 2: API carries topics both ways and re-checks every message

**Files:** Modify `app/routers/ingest_api.py`, `app/ingest/store.py`; extend `tests/test_ingest_topics.py`; Modify `tests/test_ingest_api.py` only if an existing assertion on the sources list shape needs the new `topics` key.

**Interfaces:**
- `PUT /api/ingest/sources/{chat_id}` body `{title, topics?: [{id, title}]}` (at most 200; `id` digits as text up to 32 characters) → `{chat_id, title, enabled, mapped}` as before.
- `GET /api/ingest/sources` → `[{chat_id, title, topics: null | ["<id>", ...]}]`.
- `POST /api/ingest/messages` accepts optional `topic_id`, `topic_title`.
- `store.ingest_message(session, source, *, message_id, album_id, received_at, text, files, topic_id=None, topic_title=None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ingest_topics.py`)

```python
from datetime import datetime

from app.models import Vendor
from ingest_helpers import anon_client, bearer, make_token, pdf_bytes

NOW = "2026-10-06T14:30:00+00:00"


def mapped(db, **kw):
    vendor = Vendor(name="Acme Labs")
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
    assert rows == {"7": ("US Prices", True), "8": ("Chatter", False), "9": ("UK Price List", True)}   # titles refresh; a later rename never changes a tick
    db.query(IngestTopic).delete()
    src = db.query(IngestSource).one()
    src.skip_words = "uk"
    db.commit()
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
    s = mapped(db, chat_id="-1", title="Whole")
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
    assert right[0]["status"] == "received" and no_topic[0]["status"] == "ignored"       # a group that follows topics ignores topic-less messages
    item = db.query(IngestItem).one()
    assert (item.topic_id, item.topic_title) == ("7", "Topic 7")
    assert db.query(IngestTopic).filter_by(topic_id="8").one().enabled is False           # an unknown topic is remembered, ticked only if its name matches the follow words


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
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** `ingest_api.py`: a helper `_topics_from(body)` returns `[(id, title)]` or raises `HTTPException(422)` (list of dicts with `title` non-empty, `id` int or str matching `^[0-9]{1,32}$` after `str()`, at most 200). In `register_source` after the title upsert, for each `(topic_id, title)` upsert `IngestTopic` (existing: refresh the title only, never the tick; new: `enabled = skip.find_skip_word(follow_words, [title]) is not None and skip.find_skip_word(skip_words, [title]) is None`, using `skip.parse_skip_words` on the source's `follow_words` and `skip_words`). `list_sources` builds each row `{"chat_id", "title", "topics": None if not s.topics_only else [t.topic_id for t in enabled topics ordered by id]}` (one query for all enabled topics grouped by source). `receive_message` reads `topic_id` (strip, at most 32) and `topic_title` (at most 200); if `source.topics_only`: when `topic_id` is empty or the topic row is missing or not enabled, create a missing topic row off (if `topic_id` given and `topic_title`), commit, and return `{"results": [{"status": "ignored", "reason": "topic not followed", "item_id": None}]}`; otherwise pass `topic_id`/`topic_title` to `store.ingest_message`. When the group is a whole group and `topic_id` is given and unknown, also create the topic row (off) so the owner can later select it, and refresh the title of a known one. `store.ingest_message`: new kwargs stored on each `IngestItem`; `_group_key` photo clustering query adds `IngestItem.topic_id == topic_id` (use `.is_(None)` when `topic_id` is None) so different topics never merge.

- [ ] **Step 4: Run to verify it passes**, then `tests/test_ingest_api.py`, then the full suite.

- [ ] **Step 5: Commit** `feat: ingest API carries topics, follows only selected topics, re-checks every message`.

---

### Task 3: Skip words and topic names in processing

**Files:** Create `app/ingest/skip.py`; Modify `app/ingest/process.py`; extend `tests/test_ingest_topics.py`.

**Interfaces:**
- `skip.parse_skip_words(raw: str | None) -> list[str]` (split on commas and newlines, trim, lower-case, de-duplicate, drop empties; raises `ValueError` for a word over 40 characters or more than 20 words); `skip.find_skip_word(words: list[str], texts: list[str | None]) -> str | None` (the first word found as a whole word, case-insensitive, in any text; letters and digits on either side mean "not whole").

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.ingest import skip
from test_ingest_process import LINES, NOW as PNOW, run, text_item


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


def test_a_list_that_mentions_a_skipped_word_is_set_aside_unread(db):
    s = mapped(db, default_warehouse="us")
    s.skip_words = "UK"
    db.commit()
    item = text_item(db, s, caption_extra="UK warehouse prices")
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "ignored" and item.reason == "skipped: uk" and item.caption is None
    assert db.query(__import__("app.models", fromlist=["PriceList"]).PriceList).count() == 0


def test_a_skipped_word_in_the_topic_name_or_the_filename_also_counts(db):
    s = mapped(db, default_warehouse="us")
    s.skip_words = "UK"
    db.commit()
    a = text_item(db, s, message_id="1", topic_title="UK Price List")
    b = text_item(db, s, message_id="2", filename="uk_list.txt")
    run()
    db.expire_all()
    assert {db.get(IngestItem, a.id).status, db.get(IngestItem, b.id).status} == {"ignored"}


def test_the_topic_name_is_a_warehouse_clue(db):
    s = mapped(db)
    item = text_item(db, s, topic_title="US Price List")
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "imported" and item.warehouse == "us"
```
(`text_item` already accepts extra model keyword arguments such as `topic_title` and `filename`.)

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** `app/ingest/skip.py`:
```python
"""Skip words: plain words that set a list aside (a region the owner cannot order from). Whole-word, case-insensitive."""

import re

MAX_WORDS, MAX_LENGTH = 20, 40


def parse_skip_words(raw: str | None) -> list[str]:
    words: list[str] = []
    for piece in re.split(r"[,\n]", raw or ""):
        word = piece.strip().lower()
        if not word or word in words:
            continue
        if len(word) > MAX_LENGTH:
            raise ValueError(f"A skip word can be at most {MAX_LENGTH} characters.")
        words.append(word)
    if len(words) > MAX_WORDS:
        raise ValueError(f"At most {MAX_WORDS} skip words.")
    return words


def find_skip_word(words: list[str], texts) -> str | None:
    for text in texts:
        for word in words:
            if text and re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text.lower()):
                return word
    return None
```
`process._handle`: before `_read`, `words = skip.parse_skip_words(source.skip_words)` (catch `ValueError` as no words), `texts = [i.caption for i in items] + [i.filename for i in items] + [i.topic_title for i in items]`; if `find_skip_word` hits, `_set(items, now, status="ignored", reason=f"skipped: {word}", caption=None)`, `_drop_files(items)`, return. After reading, check again against `[data.shipping_note] + [r.name for r in data.rows][:200]` the same way. In `_context`, the caption used for the warehouse words and the date includes the distinct non-empty `topic_title` values (append them to the joined caption text).

- [ ] **Step 4: Run to verify it passes**, then `tests/test_ingest_process.py`, then the full suite.

- [ ] **Step 5: Commit** `feat: skip words and topic names in ingest processing`.

---

### Task 4: Inbox controls

**Files:** Modify `app/routers/ingest_admin.py`, `app/templates/settings/ingest.html`; extend `tests/test_ingest_topics.py` (admin tests use `client`, `other_client` from `photo_helpers`).

**Interfaces:** `POST /settings/ingest/sources/{id}` additionally accepts `topics_only` (checkbox), `skip_words` (text), `follow_words` (text, same limits as skip words, "auto-follow words"), `topics` (repeated, values are `IngestTopic.id` of the ticked topics). A non-administrator still gets 404.

- [ ] **Step 1: Write the failing tests**

```python
from photo_helpers import other_client


def test_follow_selection_topic_ticks_and_skip_words_are_saved(client, db):
    s = mapped(db)
    a = IngestTopic(source_id=s.id, topic_id="7", title="US Price List")
    b = IngestTopic(source_id=s.id, topic_id="8", title="Chatter")
    db.add_all([a, b])
    db.commit()
    r = client.post(f"/settings/ingest/sources/{s.id}", data={"vendor_id": str(s.vendor_id), "enabled": "on", "topics_only": "on",
                                                              "skip_words": " UK , EU\nuk ", "topics": [str(a.id)]}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    db.refresh(s)
    assert (s.topics_only, s.skip_words) == (True, "uk, eu")
    assert {t.topic_id: t.enabled for t in db.query(IngestTopic)} == {"7": True, "8": False}
    client.post(f"/settings/ingest/sources/{s.id}", data={"vendor_id": str(s.vendor_id), "enabled": "on", "skip_words": ""})
    db.expire_all()
    db.refresh(s)
    assert (s.topics_only, s.skip_words) == (False, None) and not any(t.enabled for t in db.query(IngestTopic))


def test_follow_words_are_saved_and_the_groups_can_be_searched(client, db):
    s = mapped(db, title="Acme Peptides")
    mapped(db, chat_id="-2", title="Zephyr Labs")
    client.post(f"/settings/ingest/sources/{s.id}", data={"vendor_id": str(s.vendor_id), "follow_words": "Price, ,warehouse, PRICE"})
    db.expire_all()
    db.refresh(s)
    assert s.follow_words == "price, warehouse"
    page = client.get("/settings/ingest?q=zeph").text
    assert "Zephyr Labs" in page and "Acme Peptides" not in page


def test_a_bad_skip_word_is_refused_and_another_groups_topic_cannot_be_ticked(client, db):
    s = mapped(db, chat_id="-1")
    other = mapped(db, chat_id="-2")
    foreign = IngestTopic(source_id=other.id, topic_id="9", title="x")
    db.add(foreign)
    db.commit()
    assert client.post(f"/settings/ingest/sources/{s.id}", data={"skip_words": "x" * 41}).status_code == 422
    client.post(f"/settings/ingest/sources/{s.id}", data={"topics_only": "on", "topics": [str(foreign.id)]})
    db.expire_all()
    assert db.get(IngestTopic, foreign.id).enabled is False


def test_the_inbox_shows_topics_skip_words_and_an_items_topic_escaped(client, db):
    s = mapped(db)
    db.add(IngestTopic(source_id=s.id, topic_id="7", title="<b>US</b> Price List"))
    s.skip_words = "uk"
    db.commit()
    text_item(db, s, topic_title="<i>x</i>")
    page = client.get("/settings/ingest").text
    assert "US Price List" in page and "&lt;b&gt;US&lt;/b&gt;" in page and "<b>US</b>" not in page and 'value="uk"' in page


def test_only_the_administrator_can_change_topics_and_skip_words(client, db):
    s = mapped(db)
    with other_client() as member:
        assert member.post(f"/settings/ingest/sources/{s.id}", data={"skip_words": "uk"}).status_code == 404
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** `update_source`: `source.topics_only = bool(form.get("topics_only"))`; `skip.parse_skip_words(form.get("skip_words"))` (catch `ValueError` → 422 with the message) and store `", ".join(words) or None`; ticked ids = ints from `form.getlist("topics")` (ignore non-digits); for each `IngestTopic` of this source set `enabled = (topic.id in ticked) and source.topics_only`; ids of other sources are ignored by construction. `follow_words` is parsed with the same `skip.parse_skip_words` and stored joined (`None` when empty). The page accepts `?q=` and shows only groups whose title contains it (case-insensitive); the template adds a small search box above the Groups table (a GET form posting `q`) and an **Auto-follow words** input per group next to Skip words. The page context adds `topics_by_source` (dict source id → list of topics ordered by title). Template: in the Groups row add two cells: a **Follow** checkbox labelled "Only selected topics" (`name="topics_only"`, shown for every group) with, below it when topics exist, a checklist of `<label><input type="checkbox" name="topics" value="{{ t.id }}" form="src{{ s.id }}" {{ 'checked' if t.enabled }}> {{ t.title }}</label>`, and a **Skip words** text input (`name="skip_words"`, placeholder `UK, EU`) all using `form="src{{ s.id }}"`. Items show `· {{ i.topic_title }}` after the filename when present.

- [ ] **Step 4: Run to verify it passes**, then the full suite; open the inbox in the browser pane once to check it renders.

- [ ] **Step 5: Commit** `feat: inbox controls for following topics and skip words`.

---

### Task 5: The watcher reads only selected topics

**Files:** Modify `watcher/ports.py`, `watcher/amide_client.py`, `watcher/runner.py`, `watcher/telethon_client.py`, `watcher/tests/fakes.py`, `watcher/tests/test_runner.py`, `tests/test_watcher_amide_client.py`; Create `watcher/tests/test_topics.py`.

**Interfaces:**
- `ports.Topic(topic_id: str, title: str)`; `Group.topics: list[Topic] = field(default_factory=list)`; `TgMessage.topic_id: str | None = None`; `Payload.topic_id: str | None = None`, `Payload.topic_title: str | None = None` (defaults keep old queue items readable); `TelegramPort.messages_since(chat_id, min_id, since, topic_id: str | None = None)`.
- `amide_client.Source(chat_id: str, topics: list[str] | None)`; `AmideClient.list_sources() -> list[Source]` (a row without `topics`, or an old plain id, means `None`); `AmideClient.register(chat_id, title, topics: list[Topic] | None = None)`; `send` also posts `topic_id` and `topic_title` when set.
- Runner: positions for topic reads are stored under the state key `f"{chat_id}#{topic_id}"`; registration signature includes the topics so a new or renamed topic triggers a register.

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_topics.py`; reuse `build`, `msg`, `poll`, `NOW` from `test_runner.py` by importing them, and extend `msg` with a `topic` argument)

```python
from datetime import timedelta

from watcher.amide_client import Source
from watcher.ports import Group, Topic
from watcher.tests.fakes import FakeAmide, FakeTelegram
from watcher.tests.test_runner import NOW, build, msg, poll


def tmsg(chat, mid, text, topic, minutes_ago=30):
    m = msg(chat, mid, text, minutes_ago)
    m.topic_id = topic
    return m


def forum(tmp_path, watch, messages):
    tg = FakeTelegram(groups=[Group("-100", "Acme group", [Topic("7", "US Price List"), Topic("8", "Chatter")])], messages={"-100": messages})
    return build(tmp_path, tg=tg, amide=FakeAmide(watch=watch))


def test_topics_are_registered_with_the_group_once_and_again_when_they_change(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [], [])
    poll(runner)
    poll(runner)
    assert amide.registered == [("-100", "Acme group", [("7", "US Price List"), ("8", "Chatter")])]
    tg.groups[0].topics.append(Topic("9", "UK"))
    poll(runner)
    assert len(amide.registered) == 2 and ("9", "UK") in amide.registered[1][2]


def test_only_the_selected_topics_are_read_and_each_message_carries_its_topic(tmp_path):
    runner, tg, amide, state, *_ = forum(tmp_path, [Source("-100", ["7"])], [tmsg("-100", 1, "list here", "7"), tmsg("-100", 2, "chat", "8")])
    poll(runner)
    assert [(p.message_id, p.topic_id, p.topic_title) for p in amide.sent] == [("1", "7", "US Price List")]
    assert not [c for c in tg.calls if c[:3] == ("messages_since", "-100", "8")]                  # the chatter topic is never fetched


def test_each_topic_has_its_own_position(tmp_path):
    runner, tg, amide, state, *_ = forum(tmp_path, [Source("-100", ["7", "8"])],
                                         [tmsg("-100", 10, "a", "7"), tmsg("-100", 3, "b", "8")])
    poll(runner)
    assert state.chat("-100#7").last_id == 10 and state.chat("-100#8").last_id == 3
    tg.messages["-100"].append(tmsg("-100", 11, "c", "8"))
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["10", "3", "11"]                                 # 3 was not skipped by 10


def test_a_group_followed_whole_is_read_without_a_topic_and_an_empty_selection_reads_nothing(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [Source("-100", None)], [tmsg("-100", 1, "x", "7")])
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["1"]
    runner2, tg2, amide2, *_ = forum(tmp_path / "b", [Source("-100", [])], [tmsg("-100", 1, "x", "7")])
    poll(runner2)
    assert amide2.sent == [] and not [c for c in tg2.calls if c[0] == "messages_since"]


def test_the_general_topic_and_a_group_that_is_not_a_forum_work(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [Source("-100", ["1"])], [tmsg("-100", 5, "general list", "1")])
    poll(runner)
    assert [p.topic_id for p in amide.sent] == ["1"]
    plain = FakeTelegram(groups=[Group("-200", "Plain group")], messages={"-200": [msg("-200", 1, "hello")]})
    runner2, tg2, amide2, *_ = build(tmp_path / "p", tg=plain, amide=FakeAmide(watch=["-200"]))
    poll(runner2)
    assert [(p.topic_id, p.topic_title) for p in amide2.sent] == [(None, None)]
```
Update `watcher/tests/fakes.py`: `FakeTelegram.messages_since(chat_id, min_id, since, topic_id=None)` filters rows by `m.topic_id == topic_id` when a topic is requested and records `("messages_since", chat_id, topic_id)` in `calls` when a topic is given (the existing one-element-tuple call record stays for `topic_id is None`); `FakeAmide.watch` entries may be strings (becoming `Source(chat, None)`) or `Source` objects; `FakeAmide.register(chat_id, title, topics=None)` records `(chat_id, title, [(t.topic_id, t.title) ...])` when topics are given and `(chat_id, title)` otherwise (existing assertions keep passing). Existing `build`, `msg` need no change besides `msg` setting `topic_id=None` through the new default. Add to `tests/test_watcher_amide_client.py`: registering topics against the real API stores them off; `list_sources` returns `Source` objects with `topics` null for a whole group and the enabled ids otherwise; an old-style answer (a mock transport returning `[{"chat_id": "-1", "title": "x"}]`) gives `topics=None`; `send` carries `topic_id`/`topic_title` into stored items.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.**
  - `ports.py` additions as in Interfaces; `Payload` new optional fields go last with defaults.
  - `amide_client.py`: `Source` dataclass; `register` sends `{"title": ..., "topics": [{"id": t.topic_id, "title": t.title} ...]}` only when topics are given; `list_sources` returns `[Source(row["chat_id"], row.get("topics"))]` (convert ids to `str`); `send` adds `topic_id`/`topic_title` fields when set.
  - `runner.py`: `_register_and_list` computes `signature = title + "|" + ",".join(f"{t.topic_id}:{t.title}" for t in sorted(group.topics, key=id))` and compares it with `st.title` (the stored value now holds the signature); it passes `group.topics or None` to `register` and keeps `self._topic_titles[(chat_id, topic_id)] = title` from the groups seen this poll; it returns `list[Source]`. `poll_once` loops over sources: for `source.topics is None` read `(chat_id, None)`; otherwise read `(chat_id, topic)` for each id (an empty list reads nothing); the gone check runs once per distinct chat id. `_read_group(chat_id, topic_id=None)` uses `key = chat_id if topic_id is None else f"{chat_id}#{topic_id}"` for `self.state.chat(key)`, calls `self.tg.messages_since(chat_id, last_id, since, topic_id)`, and `_build` fills `topic_id` (from the first message, `None` for non-forum or whole-group reads of messages without a topic) and `topic_title` from `self._topic_titles`.
  - `telethon_client.py`: in `list_groups`, for a dialog whose entity has `forum` true, call `await self._client(GetForumTopicsRequest(channel=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))` inside a try (any error means no topics) and map `topics` that have a `title` to `Topic(str(t.id), t.title)`; `messages_since(..., topic_id=None)` passes `reply_to=int(topic_id)` to `iter_messages` when a topic is given, and sets `TgMessage.topic_id` from `topic_id` (or, for whole-group reads of a forum, from `m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id` when `m.reply_to.forum_topic`, else `None`). Import `GetForumTopicsRequest` lazily inside the method.

- [ ] **Step 4: Run to verify it passes**, then `watcher/tests`, `tests/test_watcher_amide_client.py`, the full suite.

- [ ] **Step 5: Commit** `feat: watcher registers topics and reads only the selected ones`.

---

### Task 6: Docs, review, scan and push

- [ ] **Step 1:** Update `watcher/README.md` (topics: how to pick them in the inbox; skip words; the manual check on one forum group) and add a ROADMAP note after the part B entry ("Ingest topics and skip words, part C"). No real names.
- [ ] **Step 2:** Full suite in the background (`Expected: all passed`); vendor-name scan, read the whole output (`Expected: only the one accepted old-spec hit`); `git diff origin/main..HEAD | grep -nE "amide_ing_[A-Za-z0-9_-]{20,}|[0-9a-f]{32}"` (`Expected: nothing`).
- [ ] **Step 3:** One independent opus review of the branch with the Review Focus verbatim and the rulings listed; one fix pass, each fix with a failing test first; minors to the ledger.
- [ ] **Step 4:** Push (plain push) after the scan is clean; delete the ledger directory.

---

## Self-review against the spec

- Topic registration (off by default, refreshed titles, cap 200), the source list shape (`null` or enabled ids), server re-check, item topic fields: Tasks 1 and 2.
- Topic name as context and skip words (whole word, any case, caption/filename/topic/list text, deleted file, reason shown, kept text cleared): Task 3.
- Inbox Follow control, topic ticks, skip-words field, topic shown on items, admin-only: Task 4.
- Watcher reads only selected topics with independent positions, sends topic id and title, tolerates an old Amide, unchanged for non-forum groups, General topic: Task 5.
- Photo clustering never crosses topics: Task 2. Backup round trip of topics and the new columns: Task 1.
- Type consistency: `Source`, `Topic`, `Payload.topic_id/topic_title`, the state key `chat#topic`, `IngestTopic.topic_id` as text are used identically in Tasks 1 to 5.
