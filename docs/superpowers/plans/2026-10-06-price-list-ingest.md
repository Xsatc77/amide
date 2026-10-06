# Price List Ingest (Part A: the engine inside Amide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A token-protected intake in Amide that takes price lists posted in chat groups (PDF, scans, photos, spreadsheets, typed text), works out vendor, warehouse and date from context, imports automatically when confident or queues for review, with an inbox, undo and two dashboard alerts.

**Architecture:** Four new tables (`ingest_tokens`, `ingest_sources`, `ingest_items`, `dashboard_dismissals`). A token-authenticated JSON/multipart API (`/api/ingest/...`) only stores items. A background worker (and the same function, called directly in tests) groups items, reads them with readers that all produce the existing `PriceListData`, infers context, applies a confidence rule and either imports through the existing `import_for_vendor` or leaves the item for review. An administrator-only inbox page manages tokens, sources and items. Alerts are derived from the tables.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic (SQLite), Jinja2, openpyxl (new), RapidOCR (existing), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-06-price-list-ingest-design.md`

## Global Constraints

- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. Suite stays green after every task (baseline 1675). The machine can be slow; run long suites with `run_in_background`.
- **No real vendor, group or price-list name anywhere in tracked files, tests, plans or commit messages.** Use invented names only (Acme, Zephyr, Borealis, Zorvex, Quillamine). Before every push run `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` and read the **whole** output (the only accepted hit is the old harmless phrase in `docs/superpowers/specs/2026-09-29-library-redesign-design.md`).
- All ingest times are naive UTC, from `app.models.naive_utcnow()` (tests inject a clock, never sleep).
- Tokens: only the SHA-256 hash is stored; the secret is shown once; compared by hash lookup; never logged. Token requests: 401 with a fixed message for missing, malformed, unknown or revoked tokens; 429 beyond 60 requests a minute per token.
- Only the administrator can create tokens, see the inbox or sources, or download an original; everyone else gets 404 on those pages.
- Files are identified by their bytes, stored under random names in `config.INGEST_DIR`, never served statically, never executed. Caps: 25 MB per file, 10 files and 8,000 characters of text per message, 20 images per list, 50 megapixels per image, spreadsheets read-only with at most 10 sheets, 5,000 rows and 40 columns each.
- A failure in a reader, the OCR engine or the importer never raises a server error: the item becomes `failed` with a short reason.
- The background worker is disabled in tests (`AMIDE_INGEST_WORKER=0`, set in `tests/conftest.py` before the app imports).
- Write Python and templates with the Write tool, not shell heredocs (backslashes are mangled). Match surrounding style.

## Review Focus

1. A forged or replayed request: another token's secret, a reused message id with a different file, a zero-byte or huge file, a zip that is not a spreadsheet, a PDF that crashes the reader, an image bomb. (Tasks 2, 3)
2. A group mapped to the wrong vendor, and a list that parses to nonsense: the rules send it to the inbox, never into the data. (Task 4)
3. The same list posted twice, or split over several photos with a slow last one. (Tasks 2, 4)
4. Two lists for different warehouses of one vendor on one day. (Task 4)
5. A group going away while its lists are current, and coming back. (Task 6)
6. The worker and a request touching the same item: no double import. (Task 4)

## File Structure

- Create `app/ingest/__init__.py`, `tokens.py`, `detect.py`, `store.py`, `readers.py`, `infer.py`, `decide.py`, `process.py`, `worker.py`, `alerts.py`.
- Create `app/routers/ingest_api.py` (token API) and `app/routers/ingest_admin.py` (inbox pages and actions).
- Create `app/templates/settings/ingest.html`, `app/templates/settings/ingest_item.html`, `app/static/js/ingest.js` (small).
- Create `migrations/versions/0039_ingest.py`.
- Edit `app/models.py`, `app/config.py`, `requirements.txt`, `tests/conftest.py`, `app/auth/gate.py`, `app/main.py`, `app/library/price_lists/ocr.py`, `app/library/price_lists/reader.py`, `app/routers/dashboard.py`, `app/templates/dashboard/index.html`, `app/templates/settings/settings.html`, `app/backup/sections.py`, `app/static/css/app.css`, `docs/ROADMAP.md`.
- Tests: `tests/ingest_helpers.py`, `tests/test_ingest_models.py`, `tests/test_ingest_api.py`, `tests/test_ingest_readers.py`, `tests/test_ingest_process.py`, `tests/test_ingest_admin.py`, `tests/test_ingest_alerts.py`, `tests/test_ingest_backup.py`.

---

### Task 1: Schema, config and dependency

**Files:**
- Create: `migrations/versions/0039_ingest.py`, `tests/test_ingest_models.py`
- Modify: `app/models.py`, `app/config.py`, `requirements.txt`, `tests/conftest.py`

**Interfaces:**
- Produces: models `IngestToken`, `IngestSource`, `IngestItem`, `DashboardDismissal`; `naive_utcnow() -> datetime` in `app.models`; constants `INGEST_KINDS = ("pdf", "image", "xlsx", "text")`, `INGEST_STATUSES = ("received", "imported", "needs_review", "ignored", "duplicate", "failed", "undone", "rejected")`; config `INGEST_DIR`, `INGEST_WORKER_ENABLED`, `INGEST_SETTLE_SECONDS = 60`, `INGEST_CLUSTER_MINUTES = 5`, `INGEST_MAX_FILE_BYTES = 25 * 1024 * 1024`, `INGEST_MAX_FILES = 10`, `INGEST_MAX_TEXT = 8000`, `INGEST_MAX_IMAGES = 20`, `INGEST_RATE_PER_MINUTE = 60`, `INGEST_NEW_LIST_DAYS = 7`.
- `IngestItem` also has `group_key` (text, indexed): items of one list share it.

- [ ] **Step 1: Write the failing test** (`tests/test_ingest_models.py`)

```python
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
```

- [ ] **Step 2: Run to verify it fails** (`ImportError`, models missing).

- [ ] **Step 3: Implement**

`requirements.txt`: append `openpyxl==3.1.5` and install it (`.venv/Scripts/python.exe -m pip install openpyxl==3.1.5`).

`app/config.py`: after the photo settings add
```python
INGEST_DIR = UPLOAD_DIR / "ingest"
INGEST_WORKER_ENABLED = os.environ.get("AMIDE_INGEST_WORKER", "1") != "0"
INGEST_SETTLE_SECONDS = 60
INGEST_CLUSTER_MINUTES = 5
INGEST_MAX_FILE_BYTES = 25 * 1024 * 1024
INGEST_MAX_FILES = 10
INGEST_MAX_TEXT = 8000
INGEST_MAX_IMAGES = 20
INGEST_RATE_PER_MINUTE = 60
INGEST_NEW_LIST_DAYS = 7
```
and in `ensure_dirs()` add `INGEST_DIR.mkdir(parents=True, exist_ok=True)`.

`tests/conftest.py`: add `os.environ["AMIDE_INGEST_WORKER"] = "0"` next to the `AMIDE_DATA_DIR` line (before app imports); import `DashboardDismissal, IngestItem, IngestSource, IngestToken` and in `clean()` add deletes (items, sources, tokens, dismissals, in that order) before `s.commit()`, plus
```python
    for f in config.INGEST_DIR.glob("*"):
        f.unlink()
```

`app/models.py`: add `naive_utcnow` near `utcnow`:
```python
def naive_utcnow() -> datetime:
    """UTC without a timezone, the way the app's sessions and ingest times are kept."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```
add constants next to `FOOD_MEALS`:
```python
INGEST_KINDS = ("pdf", "image", "xlsx", "text")
INGEST_STATUSES = ("received", "imported", "needs_review", "ignored", "duplicate", "failed", "undone", "rejected")
```
and the four models (before `class JournalEntry(Base)`):
```python
class IngestToken(Base):
    """A secret the price-list watcher presents. Only the SHA-256 hash is kept; the secret is shown once."""
    __tablename__ = "ingest_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(60))
    prefix: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class IngestSource(Base):
    """A chat group price lists are posted in, mapped to a vendor by the administrator."""
    __tablename__ = "ingest_sources"
    __table_args__ = (
        UniqueConstraint("platform", "chat_id", name="uq_ingest_source_chat"),
        CheckConstraint("default_warehouse IS NULL OR default_warehouse IN ('us', 'china')", name="ck_ingest_source_warehouse"),
        CheckConstraint("state IN ('active', 'gone')", name="ck_ingest_source_state"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")
    chat_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    default_warehouse: Mapped[str | None] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(10), default="active")
    state_reason: Mapped[str | None] = mapped_column(String(200))
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    alert_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)


class IngestItem(Base):
    """One file (or one typed message) received from a source. Items of one list share a `group_key`."""
    __tablename__ = "ingest_items"
    __table_args__ = (
        UniqueConstraint("source_id", "message_id", "file_hash", name="uq_ingest_item_message_file"),
        CheckConstraint("kind IN ('pdf', 'image', 'xlsx', 'text')", name="ck_ingest_item_kind"),
        CheckConstraint("status IN ('received', 'imported', 'needs_review', 'ignored', 'duplicate', 'failed', 'undone', 'rejected')",
                        name="ck_ingest_item_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("ingest_sources.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(64))
    album_id: Mapped[str | None] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(String(160), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    filename: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))
    file_hash: Mapped[str] = mapped_column(String(64))
    stored_file: Mapped[str | None] = mapped_column(String(64))
    caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="received")
    reason: Mapped[str | None] = mapped_column(String(300))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    warehouse: Mapped[str | None] = mapped_column(String(10))
    list_date: Mapped[date | None] = mapped_column(Date)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id", ondelete="SET NULL"))
    rows_found: Mapped[int | None] = mapped_column(Integer)
    rows_matched: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DashboardDismissal(Base):
    """A person dismissed one dashboard alert (identified by a key such as 'newlist:12')."""
    __tablename__ = "dashboard_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "alert_key", name="uq_dashboard_dismissal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alert_key: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
```

`migrations/versions/0039_ingest.py`: revision `0039`, down `0038`; `upgrade()` creates the four tables with the same columns, constraints and indexes (`ix_ingest_tokens_owner_id`, `ix_ingest_items_source_id`, `ix_ingest_items_group_key`, `ix_dashboard_dismissals_user_id`), with `sa.DateTime()` (no timezone) for every time column; `downgrade()` drops them in reverse order. Follow `0038_food.py` for style.

- [ ] **Step 4: Run to verify it passes**: the new file and `tests/test_migrations.py`, then the full suite. Expected: PASS, except the backup registry guard (`test_the_registry_covers_every_table_in_the_schema`), which stays red until Task 7.

- [ ] **Step 5: Commit**
```bash
git add migrations/versions/0039_ingest.py app/models.py app/config.py requirements.txt tests/conftest.py tests/test_ingest_models.py
git commit -m "feat: ingest schema (tokens, chat sources, received items, dashboard dismissals)"
```

---

### Task 2: Tokens and the intake API

**Files:**
- Create: `app/ingest/__init__.py` (empty), `app/ingest/tokens.py`, `app/ingest/detect.py`, `app/ingest/store.py`, `app/routers/ingest_api.py`, `tests/ingest_helpers.py`, `tests/test_ingest_api.py`
- Modify: `app/auth/gate.py`, `app/main.py`

**Interfaces:**
- Produces in `app/ingest/tokens.py`: `PREFIX = "amide_ing_"`; `hash_secret(secret: str) -> str`; `create_token(session, owner_id: int, label: str) -> tuple[IngestToken, str]` (the second value is the secret, available only here); `find_token(session, header: str | None) -> IngestToken | None` (`Bearer <secret>`, not revoked, owner still an administrator); `revoke_token(session, token_id: int) -> bool`; `limiter` with `allow(token_id: int, now: float) -> bool` (60 per minute by `config.INGEST_RATE_PER_MINUTE`).
- Produces in `app/ingest/detect.py`: `detect_kind(data: bytes) -> str | None` (`pdf` | `image` | `xlsx` | None).
- Produces in `app/ingest/store.py`: `ingest_message(session, source, *, message_id: str, album_id: str | None, received_at: datetime, text: str | None, files: list[tuple[str, bytes]]) -> list[dict]` (one result per file and one for non-empty text; each `{"status": "received"|"duplicate"|"ignored"|"rejected", "reason": str|None, "item_id": int|None}`).
- Produces routes: `PUT /api/ingest/sources/{chat_id}`, `GET /api/ingest/sources`, `POST /api/ingest/sources/{chat_id}/state`, `POST /api/ingest/messages`.
- Test helpers in `tests/ingest_helpers.py`: `anon_client()` (a `TestClient` with no cookies, as the watcher would be), `make_token(db, me) -> str` (a valid secret), `bearer(secret) -> dict`, `pdf_bytes()`, `png_bytes()`, `jpeg_bytes()`, `xlsx_bytes(rows)`, `make_source(db, vendor=None, enabled=True, **kw)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_api.py`)

```python
import io
from datetime import datetime, timedelta

import pytest

from app import config
from app.ingest import tokens
from app.models import IngestItem, IngestSource, IngestToken, User, Vendor
from ingest_helpers import anon_client, bearer, jpeg_bytes, make_source, make_token, pdf_bytes, png_bytes, xlsx_bytes

NOW = "2026-10-06T14:30:00+00:00"


def post_message(c, secret, source, files=(), **fields):
    data = {"chat_id": source.chat_id, "message_id": "10", "date": NOW, **fields}
    return c.post("/api/ingest/messages", data=data, headers=bearer(secret),
                  files=[("files", (name, content, "application/octet-stream")) for name, content in files] or None)


# ---------------------------------------------------------------- tokens

def test_a_token_is_shown_once_and_only_its_hash_is_stored(db, me):
    token, secret = tokens.create_token(db, me, "Home PC")
    assert secret.startswith(tokens.PREFIX) and len(secret) > 40
    assert secret not in (token.token_hash, token.prefix) and token.token_hash == tokens.hash_secret(secret)
    assert tokens.find_token(db, f"Bearer {secret}").id == token.id


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer nope", "Basic abc", "Bearer " + "x" * 300])
def test_malformed_headers_find_no_token(db, header):
    assert tokens.find_token(db, header) is None


def test_a_revoked_token_or_one_whose_owner_is_no_longer_an_administrator_stops_working(db, me):
    token, secret = tokens.create_token(db, me, "t")
    assert tokens.revoke_token(db, token.id) is True
    assert tokens.find_token(db, f"Bearer {secret}") is None
    token2, secret2 = tokens.create_token(db, me, "t2")
    db.get(User, me).is_admin = False
    db.commit()
    try:
        assert tokens.find_token(db, f"Bearer {secret2}") is None
    finally:
        db.get(User, me).is_admin = True
        db.commit()


def test_the_rate_limit_allows_60_a_minute_per_token_then_recovers():
    limiter = tokens.RateLimiter(3, 60.0)
    assert [limiter.allow(1, t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert limiter.allow(2, 3) is True                    # another token has its own allowance
    assert limiter.allow(1, 61) is True


# ---------------------------------------------------------------- authentication on the routes

def test_every_ingest_route_refuses_a_missing_or_wrong_token_with_a_fixed_message(db, me):
    source = make_source(db)
    with anon_client() as c:
        for r in (c.get("/api/ingest/sources"), c.put(f"/api/ingest/sources/{source.chat_id}", json={"title": "x"}),
                  c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone"}),
                  c.post("/api/ingest/messages", data={"chat_id": source.chat_id, "message_id": "1", "date": NOW}),
                  c.get("/api/ingest/sources", headers=bearer("amide_ing_wrong"))):
            assert r.status_code == 401 and r.json() == {"detail": "Unauthorized"}


def test_the_session_gate_does_not_apply_to_ingest_paths_but_a_session_alone_is_not_enough(client, db, me):
    source = make_source(db)
    assert client.get("/api/ingest/sources").status_code == 401           # a signed-in browser without a token is refused


def test_requests_beyond_the_rate_limit_get_429(db, me, monkeypatch):
    secret = make_token(db, me)
    monkeypatch.setattr(tokens, "limiter", tokens.RateLimiter(2, 60.0))
    with anon_client() as c:
        codes = [c.get("/api/ingest/sources", headers=bearer(secret)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- sources

def test_the_watcher_registers_a_group_which_starts_disabled_and_unmapped(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        r = c.put("/api/ingest/sources/-100777", json={"title": "Acme Peptides group"}, headers=bearer(secret))
        assert r.status_code == 200 and r.json() == {"chat_id": "-100777", "title": "Acme Peptides group", "enabled": False, "mapped": False}
        assert c.put("/api/ingest/sources/-100777", json={"title": "Renamed"}, headers=bearer(secret)).json()["title"] == "Renamed"
        assert c.get("/api/ingest/sources", headers=bearer(secret)).json() == []             # nothing to watch until enabled and mapped
    assert db.query(IngestSource).count() == 1


def test_only_enabled_and_mapped_groups_are_listed_for_watching(db, me):
    secret = make_token(db, me)
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    make_source(db, vendor=vendor, chat_id="-1", title="Mapped and enabled")
    make_source(db, vendor=vendor, chat_id="-2", title="Disabled", enabled=False)
    make_source(db, vendor=None, chat_id="-3", title="Unmapped")
    with anon_client() as c:
        assert c.get("/api/ingest/sources", headers=bearer(secret)).json() == [{"chat_id": "-1", "title": "Mapped and enabled"}]


def test_the_watcher_reports_a_group_gone_and_active_again(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone", "reason": "removed from the group"}, headers=bearer(secret))
        assert r.status_code == 200
        db.expire_all()
        s = db.get(IngestSource, source.id)
        assert (s.state, s.state_reason) == ("gone", "removed from the group") and s.state_changed_at is not None
        first = s.state_changed_at
        c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone"}, headers=bearer(secret))      # repeating changes nothing
        db.expire_all()
        assert db.get(IngestSource, source.id).state_changed_at == first
        c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "active"}, headers=bearer(secret))
        db.expire_all()
        assert db.get(IngestSource, source.id).state == "active"
        assert c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "lost"}, headers=bearer(secret)).status_code == 422
        assert c.post("/api/ingest/sources/-404/state", json={"state": "gone"}, headers=bearer(secret)).status_code == 404


# ---------------------------------------------------------------- messages

def test_a_pdf_is_stored_as_a_received_item_with_its_context(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = post_message(c, secret, source, files=[("list.pdf", pdf_bytes())], text="New prices, USA warehouse")
    body = r.json()
    assert r.status_code == 200 and body["results"][0]["status"] == "received"
    db.expire_all()
    item = db.query(IngestItem).one()
    assert (item.kind, item.status, item.filename, item.caption, item.message_id) == ("pdf", "received", "list.pdf", "New prices, USA warehouse", "10")
    assert item.received_at == datetime(2026, 10, 6, 14, 30) and (config.INGEST_DIR / item.stored_file).read_bytes()[:4] == b"%PDF"


def test_the_kind_comes_from_the_bytes_not_the_name(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = post_message(c, secret, source, files=[("renamed.txt", pdf_bytes()), ("sheet.pdf", xlsx_bytes([["a", "b"]])), ("photo.bin", png_bytes()),
                                                  ("pic.jpg", jpeg_bytes()), ("note.pdf", b"just words, not a pdf"), ("empty.pdf", b"")])
    statuses = [(x["status"]) for x in r.json()["results"]]
    assert statuses == ["received", "received", "received", "received", "ignored", "ignored"]
    kinds = sorted(i.kind for i in db.query(IngestItem))
    assert kinds == ["image", "image", "pdf", "xlsx"]


def test_typed_text_alone_is_an_item_and_empty_text_is_not(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        assert post_message(c, secret, source, text="Zorvex ZX10 10mg*10vials $50").json()["results"][0]["status"] == "received"
        assert post_message(c, secret, source, text="   ", message_id="11").json()["results"] == []
    assert db.query(IngestItem).filter_by(kind="text").count() == 1


def test_the_same_message_and_file_is_a_duplicate_and_changes_nothing(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        post_message(c, secret, source, files=[("a.pdf", pdf_bytes())])
        again = post_message(c, secret, source, files=[("a.pdf", pdf_bytes())])
        other_message = post_message(c, secret, source, files=[("a.pdf", pdf_bytes())], message_id="11")
    assert again.json()["results"][0]["status"] == "duplicate"
    assert other_message.json()["results"][0]["status"] == "received"               # a re-post in a new message is handled later by the list rules
    assert db.query(IngestItem).count() == 2


def test_limits_on_size_file_count_text_and_unknown_or_disabled_sources(db, me, monkeypatch):
    secret = make_token(db, me)
    source = make_source(db)
    disabled = make_source(db, chat_id="-9", enabled=False)
    monkeypatch.setattr(config, "INGEST_MAX_FILE_BYTES", 100)
    with anon_client() as c:
        big = post_message(c, secret, source, files=[("big.pdf", pdf_bytes() + b"0" * 500)])
        assert big.json()["results"][0] == {"status": "rejected", "reason": "file too large", "item_id": None}
        monkeypatch.setattr(config, "INGEST_MAX_FILE_BYTES", 25 * 1024 * 1024)
        many = post_message(c, secret, source, files=[(f"{i}.pdf", pdf_bytes() + bytes([i])) for i in range(11)], message_id="12")
        assert many.status_code == 422
        assert post_message(c, secret, source, text="x" * 8001, message_id="13").status_code == 422
        unknown = c.post("/api/ingest/messages", data={"chat_id": "-404", "message_id": "1", "date": NOW}, headers=bearer(secret))
        assert unknown.status_code == 404
        assert post_message(c, secret, disabled, files=[("a.pdf", pdf_bytes())]).status_code == 409
        assert post_message(c, secret, source, message_id="14", date="not-a-date").status_code == 422
    assert db.query(IngestItem).count() == 0


def test_photos_of_one_album_share_a_group_key_and_lone_photos_within_five_minutes_cluster(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        post_message(c, secret, source, files=[("1.png", png_bytes(1))], message_id="1", album_id="A")
        post_message(c, secret, source, files=[("2.png", png_bytes(2))], message_id="2", album_id="A")
        post_message(c, secret, source, files=[("3.png", png_bytes(3))], message_id="3")
        post_message(c, secret, source, files=[("4.png", png_bytes(4))], message_id="4")
        post_message(c, secret, source, files=[("5.pdf", pdf_bytes())], message_id="5")
    keys = {i.message_id: i.group_key for i in db.query(IngestItem)}
    assert keys["1"] == keys["2"] and keys["3"] == keys["4"] and keys["1"] != keys["3"] and keys["5"] not in (keys["1"], keys["3"])


def test_the_last_use_of_a_token_is_recorded(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        c.get("/api/ingest/sources", headers=bearer(secret))
    db.expire_all()
    assert db.query(IngestToken).one().last_used_at is not None
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`tests/ingest_helpers.py` (write with the Write tool): `anon_client()` returns `TestClient(app, follow_redirects=False)` entered as a context manager (so the lifespan runs); `make_token(db, me)` calls `tokens.create_token(db, me, "test")` and returns the secret; `bearer(secret)` returns `{"Authorization": f"Bearer {secret}"}`; `make_source(db, vendor=None, enabled=True, chat_id="-100123", title="Acme group", **kw)` creates an `IngestSource` (mapped to `vendor` when given) and returns it; byte builders: `pdf_bytes()` returns a small valid-looking PDF (`b"%PDF-1.4\n" + body + b"\n%%EOF"` with a text line), `png_bytes(seed=0)` and `jpeg_bytes(seed=0)` build tiny distinct images with Pillow, `xlsx_bytes(rows)` builds a workbook with `openpyxl.Workbook()` writing `rows` and returns the saved bytes.

`app/ingest/tokens.py`:
```python
"""Tokens for the price-list watcher: created once, stored only as a hash, rate limited."""

import hashlib
import secrets
import threading
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import IngestToken, User, naive_utcnow

PREFIX = "amide_ing_"
_MAX_SECRET = 200


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_token(session: Session, owner_id: int, label: str) -> tuple[IngestToken, str]:
    secret = PREFIX + secrets.token_urlsafe(32)
    token = IngestToken(owner_id=owner_id, label=(label or "").strip()[:60] or "Watcher", prefix=secret[:len(PREFIX) + 6],
                        token_hash=hash_secret(secret))
    session.add(token)
    session.commit()
    return token, secret


def find_token(session: Session, header: str | None) -> IngestToken | None:
    """The live token for an Authorization header of the form `Bearer <secret>`, or None."""
    if not header or not header.startswith("Bearer "):
        return None
    secret = header[7:].strip()
    if not secret.startswith(PREFIX) or len(secret) > _MAX_SECRET:
        return None
    token = session.scalar(select(IngestToken).where(IngestToken.token_hash == hash_secret(secret), IngestToken.revoked_at.is_(None)))
    if token is None:
        return None
    owner = session.get(User, token.owner_id)
    return token if owner is not None and owner.is_admin else None


def revoke_token(session: Session, token_id: int) -> bool:
    token = session.get(IngestToken, token_id)
    if token is None or token.revoked_at is not None:
        return False
    token.revoked_at = naive_utcnow()
    session.commit()
    return True


class RateLimiter:
    """At most `limit` calls per `window` seconds for each key (a sliding window, in memory)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, now: float) -> bool:
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


limiter = RateLimiter(config.INGEST_RATE_PER_MINUTE, 60.0)
```

`app/ingest/detect.py`:
```python
"""What a received file is, decided from its bytes (never its name)."""

import io
import zipfile


def detect_kind(data: bytes) -> str | None:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == bytes([0x89]) + b"PNG\r\n" + bytes([0x1A, 0x0A]) or data[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return "image"
    if data[:4] == b"PK" + bytes([3, 4]):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    return None
```

`app/ingest/store.py`:
```python
"""Receiving a message: store what is a price-list candidate, skip the rest, and never process the same file twice."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.ingest.detect import detect_kind
from app.models import IngestItem, IngestSource, naive_utcnow


def _group_key(session: Session, source: IngestSource, kind: str, message_id: str, album_id: str | None, digest: str, now: datetime) -> str:
    if kind != "image":
        return f"{source.id}:m:{message_id}:{digest[:8]}"
    if album_id:
        return f"{source.id}:a:{album_id}"
    since = now - timedelta(minutes=config.INGEST_CLUSTER_MINUTES)       # lone photos close together are one list
    recent = session.scalar(select(IngestItem).where(
        IngestItem.source_id == source.id, IngestItem.kind == "image", IngestItem.status == "received", IngestItem.album_id.is_(None),
        IngestItem.created_at >= since).order_by(IngestItem.created_at.desc()))
    return recent.group_key if recent is not None else f"{source.id}:t:{uuid.uuid4().hex[:12]}"


def ingest_message(session: Session, source: IngestSource, *, message_id: str, album_id: str | None, received_at: datetime,
                   text: str | None, files: list[tuple[str, bytes]]) -> list[dict]:
    results: list[dict] = []
    now = naive_utcnow()
    caption = (text or "").strip() or None
    for filename, data in files:
        if len(data) > config.INGEST_MAX_FILE_BYTES:
            results.append({"status": "rejected", "reason": "file too large", "item_id": None})
            continue
        kind = detect_kind(data)
        if kind is None:
            results.append({"status": "ignored", "reason": "not a price-list file type", "item_id": None})
            continue
        digest = hashlib.sha256(data).hexdigest()
        if session.scalar(select(IngestItem.id).where(IngestItem.source_id == source.id, IngestItem.message_id == message_id,
                                                      IngestItem.file_hash == digest)) is not None:
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
            continue
        config.ensure_dirs()
        stored = secrets.token_hex(16)
        (config.INGEST_DIR / stored).write_bytes(data)
        item = IngestItem(source_id=source.id, message_id=message_id, album_id=album_id, received_at=received_at,
                          filename=(filename or "")[:200] or None, kind=kind, file_hash=digest, stored_file=stored, caption=caption,
                          group_key=_group_key(session, source, kind, message_id, album_id, digest, now), status="received", created_at=now)
        session.add(item)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            (config.INGEST_DIR / stored).unlink(missing_ok=True)
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
            continue
        results.append({"status": "received", "reason": None, "item_id": item.id})
    if caption and not files:
        digest = hashlib.sha256(caption.encode("utf-8")).hexdigest()
        item = IngestItem(source_id=source.id, message_id=message_id, album_id=None, received_at=received_at, filename=None, kind="text",
                          file_hash=digest, stored_file=None, caption=caption, group_key=f"{source.id}:m:{message_id}:{digest[:8]}",
                          status="received", created_at=now)
        session.add(item)
        try:
            session.commit()
            results.append({"status": "received", "reason": None, "item_id": item.id})
        except IntegrityError:
            session.rollback()
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
    return results
```
(The `message caption/text` is stored on every file item of the message; a text-only message is its own `text` item.)

`app/routers/ingest_api.py`:
```python
"""The token-protected API the price-list watcher uses. It only stores what it is given; reading and deciding happen later."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import config
from app.db import get_session
from app.ingest import store, tokens
from app.models import IngestItem, IngestSource, IngestToken, naive_utcnow

router = APIRouter(prefix="/api/ingest")


def require_token(request: Request, session: Session = Depends(get_session)) -> IngestToken:
    token = tokens.find_token(session, request.headers.get("authorization"))
    if token is None:
        raise HTTPException(401, "Unauthorized")
    if not tokens.limiter.allow(token.id, time.monotonic()):
        raise HTTPException(429, "Too many requests")
    token.last_used_at = naive_utcnow()
    session.commit()
    return token


def _source(session: Session, chat_id: str) -> IngestSource | None:
    return session.scalar(select(IngestSource).where(IngestSource.platform == "telegram", IngestSource.chat_id == chat_id))


@router.put("/sources/{chat_id}")
async def register_source(chat_id: str, request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    body = await request.json()
    title = str(body.get("title") or "").strip()[:200] if isinstance(body, dict) else ""
    if not title:
        raise HTTPException(422, "title is required")
    source = _source(session, chat_id)
    if source is None:
        source = IngestSource(platform="telegram", chat_id=chat_id[:64], title=title)
        session.add(source)
    else:
        source.title = title
    session.commit()
    return {"chat_id": source.chat_id, "title": source.title, "enabled": source.enabled, "mapped": source.vendor_id is not None}


@router.get("/sources")
def list_sources(session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    rows = session.scalars(select(IngestSource).where(IngestSource.enabled.is_(True), IngestSource.vendor_id.is_not(None))
                           .order_by(IngestSource.id)).all()
    return [{"chat_id": s.chat_id, "title": s.title} for s in rows]


@router.post("/sources/{chat_id}/state")
async def report_state(chat_id: str, request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    body = await request.json()
    state = body.get("state") if isinstance(body, dict) else None
    if state not in ("active", "gone"):
        raise HTTPException(422, "state must be active or gone")
    source = _source(session, chat_id)
    if source is None:
        raise HTTPException(404, "unknown source")
    if source.state != state:
        source.state, source.state_changed_at = state, naive_utcnow()
    source.state_reason = (str(body.get("reason") or "")[:200] or None) if state == "gone" else None
    session.commit()
    return {"chat_id": source.chat_id, "state": source.state}


@router.post("/messages")
async def receive_message(request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    form = await request.form()
    source = _source(session, str(form.get("chat_id") or ""))
    if source is None:
        raise HTTPException(404, "unknown source")
    if not source.enabled or source.vendor_id is None:
        raise HTTPException(409, "source is disabled or has no vendor")
    message_id = str(form.get("message_id") or "").strip()[:64]
    try:
        received_at = datetime.fromisoformat(str(form.get("date") or "").replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "date must be ISO 8601") from None
    if received_at.tzinfo is not None:
        received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(form.get("text") or "")
    uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    if not message_id or len(uploads) > config.INGEST_MAX_FILES or len(text) > config.INGEST_MAX_TEXT:
        raise HTTPException(422, "message_id is required; at most 10 files and 8000 characters of text")
    files = [(f.filename or "", await f.read(config.INGEST_MAX_FILE_BYTES + 1)) for f in uploads]
    album = str(form.get("album_id") or "").strip()[:64] or None
    results = store.ingest_message(session, source, message_id=message_id, album_id=album, received_at=received_at, text=text, files=files)
    return {"results": results}
```
`app/auth/gate.py`: add `TOKEN_PREFIXES = ("/api/ingest/",)` and, right after the cross-site check and before the static-prefix check, `if path.startswith(TOKEN_PREFIXES): return await call_next(request)` (these routes authenticate themselves with a token and never accept a session). `app/main.py`: import `ingest_api` and `app.include_router(ingest_api.router)`.

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add app/ingest/__init__.py app/ingest/tokens.py app/ingest/detect.py app/ingest/store.py app/routers/ingest_api.py app/auth/gate.py app/main.py tests/ingest_helpers.py tests/test_ingest_api.py
git commit -m "feat: token-protected ingest API (sources, state reports, messages) that stores received items"
```

---

### Task 3: Readers and the price-list check

**Files:**
- Create: `app/ingest/readers.py`, `tests/test_ingest_readers.py`
- Modify: `app/library/price_lists/ocr.py`, `app/library/price_lists/reader.py`

**Interfaces:**
- Consumes: `ParsedRow`, `PriceListData`, `rows_from_table`, `rows_from_lines`, `scan_notes`, `unread_spec_lines` from `app.library.price_lists.reader`; `ocr.recognize`, `ocr.table_from_words`, `ocr.lines_from_words`.
- Produces in `app/library/price_lists/ocr.py`: `rows_from_words(words, page_number: int) -> tuple[list[str], list[ParsedRow]]` (the page's text lines and the rows read from recognized words); `read_pdf` and the new image reader both use it.
- Produces in `app/ingest/readers.py`: `read_images(images, recognize=None) -> PriceListData` (PIL images, at most `config.INGEST_MAX_IMAGES`, each at most `config.PHOTO_MAX_PIXELS`); `read_xlsx(data: bytes) -> PriceListData`; `read_text(text: str) -> PriceListData`; `read_pdf_file(path, recognize=None) -> PriceListData` (wraps `read_pdf`); `looks_like_price_list(data: PriceListData, caption: str | None, filename: str | None) -> tuple[bool, str | None]` (`(True, None)` or `(False, reason)`); `priced_fraction(data) -> float`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_readers.py`)

```python
import io

import pytest
from PIL import Image, ImageDraw

from app import config
from app.ingest import readers
from app.library.price_lists.ocr import Word
from app.library.price_lists.rows import Spec
from ingest_helpers import xlsx_bytes


def w(text, cx, cy, width=120, height=24):
    return Word(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2, text)


def scan_words():
    words = [w("name", 150, 130), w("Product name", 450, 130), w("specification", 800, 130), w("prices", 1060, 130)]
    y = 180
    for code, name, spec, price in (("ZX5", "Zorvex", "5mg*10vials", "$50"), ("ZX10", "", "10mg*10vials", "$60"),
                                    ("QU5", "Quillamine", "5mg*10vials", "$55"), ("QU10", "", "10mg*10vials", "$75"),
                                    ("BK10", "Borealin", "10mg*10vials", "$95")):
        words += [w(code, 150, y), w(name, 450, y) if name else None, w(spec, 800, y, 160), w(price, 1060, y, 80)]
        y += 46
    return [x for x in words if x is not None]


def blank(size=(400, 300)):
    return Image.new("RGB", size, "white")


# ---------------------------------------------------------------- photos

def test_photos_are_read_through_the_recognizer_and_several_photos_are_one_list():
    data = readers.read_images([blank(), blank()], recognize=lambda image: scan_words())
    assert len(data.rows) == 10 and {r.name for r in data.rows if r.name} == {"Zorvex", "Quillamine", "Borealin"}
    assert [(r.code, r.spec, r.pack_price) for r in data.rows[:2]] == [("ZX5", Spec(5, "mg", 10), 50.0), ("ZX10", Spec(10, "mg", 10), 60.0)]


def test_a_photo_with_nothing_recognized_gives_no_rows_not_an_error():
    assert readers.read_images([blank()], recognize=lambda image: []).rows == []


def test_too_many_photos_or_a_giant_one_are_refused(monkeypatch):
    with pytest.raises(readers.ReadError, match="too many"):
        readers.read_images([blank((10, 10)) for _ in range(config.INGEST_MAX_IMAGES + 1)], recognize=lambda image: [])
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 1000)
    with pytest.raises(readers.ReadError, match="too large"):
        readers.read_images([blank((100, 100))], recognize=lambda image: [])


# ---------------------------------------------------------------- spreadsheets

def test_a_spreadsheet_with_a_header_row_is_read_with_names_filled_down():
    sheet = [["Code", "Product", "Spec", "Price"], ["ZX5", "Zorvex", "5mg*10vials", 50], ["ZX10", None, "10mg*10vials", 60],
             ["QU5", "Quillamine", "5mg*10vials", 55], ["QU10", None, "10mg*10vials", 75]]
    data = readers.read_xlsx(xlsx_bytes(sheet))
    assert [(r.code, r.spec, r.pack_price) for r in data.rows] == [
        ("ZX5", Spec(5, "mg", 10), 50.0), ("ZX10", Spec(10, "mg", 10), 60.0), ("QU5", Spec(5, "mg", 10), 55.0), ("QU10", Spec(10, "mg", 10), 75.0)]


def test_a_warehouse_note_in_a_spreadsheet_is_a_hint():
    sheet = [["Acme Peptides"], ["US WAREHOUSE A"], ["Code", "Product", "Spec", "Price"], ["ZX5", "Zorvex", "5mg*10vials", 50],
             ["ZX10", "Zorvex", "10mg*10vials", 60], ["QU5", "Quillamine", "5mg*10vials", 55]]
    assert readers.read_xlsx(xlsx_bytes(sheet)).warehouse_hint == "us"


def test_several_sheets_are_all_read_and_a_non_spreadsheet_or_empty_one_is_an_error_or_empty():
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["Code", "Product", "Spec", "Price"])
    book.active.append(["ZX5", "Zorvex", "5mg*10vials", 50])
    book.active.append(["ZX10", "Zorvex", "10mg*10vials", 60])
    second = book.create_sheet("More")
    second.append(["Code", "Product", "Spec", "Price"])
    second.append(["QU5", "Quillamine", "5mg*10vials", 55])
    second.append(["QU10", "Quillamine", "10mg*10vials", 75])
    buffer = io.BytesIO()
    book.save(buffer)
    assert len(readers.read_xlsx(buffer.getvalue()).rows) == 4
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(b"PK" + bytes([3, 4]) + b"not really a workbook")
    assert readers.read_xlsx(xlsx_bytes([[None, None]])).rows == []


def test_a_huge_spreadsheet_is_cut_at_the_caps_without_failing():
    rows = [["Code", "Product", "Spec", "Price"]] + [[f"ZX{i}", f"Zorvex{i}", "10mg*10vials", 50] for i in range(6000)]
    assert len(readers.read_xlsx(xlsx_bytes(rows)).rows) <= 5000


# ---------------------------------------------------------------- typed text

def test_typed_text_lines_become_rows_and_chatter_is_ignored():
    text = "New prices today!\nSemaglutide SM10 10mg*10vials $21\nTirzepatide TR10 10mg*10vials $19\nthanks everyone"
    data = readers.read_text(text)
    assert [(r.code, r.name, r.pack_price) for r in data.rows] == [("SM10", "Semaglutide", 21.0), ("TR10", "Tirzepatide", 19.0)]
    assert readers.read_text("hello world, what is the shipping time?").rows == []


# ---------------------------------------------------------------- is it a price list

def data_with(priced, unpriced=0):
    rows = [readers.ParsedRow(code="ZX", name="Zorvex", spec=Spec(10, "mg", 10), pack_price=50.0) for _ in range(priced)]
    rows += [readers.ParsedRow(code="ZY", name="Quillamine", spec=Spec(10, "mg", 10), pack_price=None) for _ in range(unpriced)]
    return readers.PriceListData(rows=rows)


def test_enough_priced_rows_make_a_price_list():
    assert readers.looks_like_price_list(data_with(3), None, None) == (True, None)
    ok, reason = readers.looks_like_price_list(data_with(2), None, None)
    assert ok is False and "not a price list" in reason


def test_mostly_unpriced_rows_are_not_a_price_list_unless_the_name_says_so():
    assert readers.looks_like_price_list(data_with(2, 3), None, None)[0] is False
    assert readers.looks_like_price_list(data_with(1), "latest price list attached", None) == (True, None)
    assert readers.looks_like_price_list(data_with(1), None, "Acme_pricelist.pdf") == (True, None)
    assert readers.looks_like_price_list(data_with(0), "price list", None)[0] is False
    assert readers.priced_fraction(data_with(3, 1)) == 0.75 and readers.priced_fraction(data_with(0)) == 0.0
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`app/library/price_lists/ocr.py`: add (and keep `read_pdf` behavior identical)
```python
def rows_from_words(words: list[Word], page_number: int):
    """(text lines, rows) read from the words recognized on one picture page: the table rebuilt from their positions."""
    from app.library.price_lists.reader import rows_from_table
    table = table_from_words(words)
    return lines_from_words(words), (rows_from_table(table, page_number) if table else [])
```
and in `app/library/price_lists/reader.py` replace the picture-page branch of `read_pdf` with `page_lines, page_rows = ocr.rows_from_words(words, number)`.

`app/ingest/readers.py`:
```python
"""Reading received files into the importer's own `PriceListData`, whatever they are: photos, spreadsheets, typed text, PDFs."""

import io
import re
from pathlib import Path

from PIL import Image

from app import config
from app.library.price_lists import ocr
from app.library.price_lists.reader import (
    ParsedRow, PriceListData, read_pdf, rows_from_lines, rows_from_table, scan_notes, unread_spec_lines,
)

_MAX_SHEETS, _MAX_ROWS, _MAX_COLS = 10, 5000, 40
_PRICE_WORDS = re.compile(r"price|quote|catalog", re.IGNORECASE)


class ReadError(ValueError):
    """The file cannot be read at all; the message is short and safe to show."""


def _data(rows: list[ParsedRow], lines: list[str]) -> PriceListData:
    note, hint = scan_notes(lines)
    return PriceListData(rows=rows, shipping_note=note, warehouse_hint=hint, unread_spec_lines=unread_spec_lines(lines, rows))


def read_pdf_file(path: Path, recognize=None) -> PriceListData:
    try:
        return read_pdf(path, recognize=recognize)
    except ocr.OcrUnavailable:
        raise ReadError("scanned pages need text recognition, which is not installed") from None
    except Exception as exc:
        raise ReadError(f"the PDF could not be read ({type(exc).__name__})") from None


def read_images(images, recognize=None) -> PriceListData:
    """Photos of a price list, read as the pages of one list."""
    images = list(images)
    if len(images) > config.INGEST_MAX_IMAGES:
        raise ReadError("too many photos for one list")
    rows: list[ParsedRow] = []
    lines: list[str] = []
    for number, image in enumerate(images, 1):
        if image.width * image.height > config.PHOTO_MAX_PIXELS:
            raise ReadError("a photo is too large")
        try:
            words = (recognize or ocr.recognize)(image)
        except ocr.OcrUnavailable:
            raise ReadError("photos need text recognition, which is not installed") from None
        page_lines, page_rows = ocr.rows_from_words(words, number)
        lines.extend(page_lines)
        rows.extend(page_rows or rows_from_lines(page_lines, number))
    return _data(rows, lines)


def read_xlsx(data: bytes) -> PriceListData:
    try:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ReadError("the spreadsheet could not be opened") from None
    rows: list[ParsedRow] = []
    lines: list[str] = []
    try:
        for number, sheet in enumerate(book.worksheets[:_MAX_SHEETS], 1):
            table = []
            for raw in sheet.iter_rows(min_row=1, max_row=_MAX_ROWS, max_col=_MAX_COLS, values_only=True):
                cells = ["" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)).strip() for v in raw]
                if any(cells):
                    table.append(cells)
                    lines.append(" ".join(c for c in cells if c))
            sheet_rows = rows_from_table(table, number) if table else []
            rows.extend(sheet_rows or rows_from_lines([" ".join(c for c in r if c) for r in table], number))
    except Exception:
        raise ReadError("the spreadsheet could not be read") from None
    finally:
        book.close()
    from app.library.price_lists.names import propagate_names
    propagate_names(rows)
    return _data(rows, lines)


def read_text(text: str) -> PriceListData:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _data(rows_from_lines(lines), lines)


def priced_fraction(data: PriceListData) -> float:
    return (sum(1 for r in data.rows if r.pack_price is not None) / len(data.rows)) if data.rows else 0.0


def looks_like_price_list(data: PriceListData, caption: str | None, filename: str | None) -> tuple[bool, str | None]:
    """A price list has at least 3 rows with a price (and most rows priced), or says "price" in its name or caption and
    has at least one priced row. Anything else is chatter, a photo of something else, or a certificate."""
    priced = sum(1 for r in data.rows if r.pack_price is not None)
    named = bool(_PRICE_WORDS.search(caption or "") or _PRICE_WORDS.search(filename or ""))
    if priced >= 3 and priced_fraction(data) >= 0.6:
        return True, None
    if named and priced >= 1:
        return True, None
    return False, "not a price list (too few priced rows)"
```
In the xlsx test helper the openpyxl-saved workbook is a real zip, so `read_only=True` works; the "not really a workbook" bytes fail `load_workbook` and raise `ReadError`.

- [ ] **Step 4: Run to verify it passes**, then the existing OCR tests (`tests/test_price_list_ocr.py`, `tests/test_price_list_reader.py`) and the full suite.

- [ ] **Step 5: Commit**
```bash
git add app/ingest/readers.py app/library/price_lists/ocr.py app/library/price_lists/reader.py tests/test_ingest_readers.py
git commit -m "feat: ingest readers for photos, spreadsheets and typed text, and the is-it-a-price-list check"
```

### Task 4: Inference, decision, processing, worker, approve and undo

**Files:**
- Create: `app/ingest/infer.py`, `app/ingest/decide.py`, `app/ingest/process.py`, `app/ingest/worker.py`, `tests/test_ingest_process.py`
- Modify: `app/main.py` (start the worker in `lifespan`)

**Interfaces:**
- Consumes: Task 1 models and config; Task 3 `read_images`, `read_xlsx`, `read_text`, `read_pdf_file`, `looks_like_price_list`, `priced_fraction`, `ReadError`; `importer.import_for_vendor(session, vendor, data, *, warehouse, list_date) -> ImportReport` (`list_id`); `analysis.current_lists(session) -> list[PriceList]`; `PriceList` (`vendor_id`, `warehouse` enum with `.value` `"us"|"china"`, `list_date`, `imported_at`, `items`), `PriceListItem` (`pack_price`, `pack_size`, `vial_amount`, `vial_unit`, `product_name`).
- Produces in `infer.py`: `infer_warehouse(hint: str | None, caption: str | None, filename: str | None, default: str | None) -> tuple[str, bool]` (warehouse, assumed); `infer_date(texts: list[str], received: date) -> date`.
- Produces in `decide.py`: `Verdict` dataclass `(status: str, reason: str | None)`; `decide(session, *, vendor, enabled, data, warehouse, assumed, list_date, file_dupe: bool) -> Verdict`; `median_price_ratio(session, vendor_id, warehouse, data) -> float | None`.
- Produces in `process.py`: `process_due(session_factory, now: datetime, recognizer=None) -> int` (groups processed); `approve_group(session, item_id: int, *, vendor_id: int, warehouse: str, list_date: date, user_id: int, recognizer=None) -> IngestItem`; `reject_group(session, item_id, user_id) -> IngestItem`; `undo_group(session, item_id, user_id) -> IngestItem`; module `lock` (a `threading.RLock` held by all three and by `process_due`).
- Produces in `worker.py`: `start(app) -> asyncio.Task | None` and `stop(task)`; runs `process_due` every 20 seconds in a thread when `config.INGEST_WORKER_ENABLED`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_process.py`)

```python
from datetime import date, datetime, timedelta

import pytest

from app import config
from app.db import SessionLocal
from app.ingest import decide, infer, process
from app.library.price_lists.ocr import Word
from app.models import IngestItem, PriceList, Vendor, naive_utcnow
from ingest_helpers import make_source, png_bytes
from test_ingest_readers import scan_words

NOW = datetime(2026, 10, 6, 15, 0)
LINES = ["Zorvex ZX5 5mg*10vials $50", "Zorvex ZX10 10mg*10vials $60", "Quillamine QU5 5mg*10vials $55",
         "Quillamine QU10 10mg*10vials $75", "Borealin BK10 10mg*10vials $95", "Borealin BK20 20mg*10vials $150"]


def text_item(db, source, text="\n".join(LINES), *, message_id="1", received=NOW, caption_extra="", **kw):
    row = dict(source_id=source.id, message_id=message_id, group_key=f"{source.id}:m:{message_id}", received_at=received, kind="text",
               file_hash=f"{message_id:0>64}", caption=(caption_extra + "\n" + text).strip(), status="received", created_at=received)
    item = IngestItem(**{**row, **kw})
    db.add(item)
    db.commit()
    return item


def factory():
    return SessionLocal


def run(now=NOW, **kw):
    return process.process_due(factory(), now, **kw)


@pytest.fixture
def vendor(db):
    v = Vendor(name="Acme Labs")
    db.add(v)
    db.commit()
    return v


# ---------------------------------------------------------------- inference

@pytest.mark.parametrize("hint,caption,filename,default,expected", [
    ("us", "china", None, None, ("us", False)),                         # the list's own text wins
    (None, "New USA list", None, None, ("us", False)),
    (None, None, "acme_china_oct.pdf", "us", ("china", False)),         # filename beats the group default
    (None, "prices", "list.pdf", "us", ("us", False)),                  # then the group default
    (None, "prices", "list.pdf", None, ("china", True)),                # otherwise China, flagged as assumed
    (None, "Chinese warehouse restock", None, None, ("china", False)),
    (None, "focus on results", None, None, ("china", True)),            # "us" inside another word is not a hint
])
def test_the_warehouse_comes_from_the_list_then_the_words_then_the_group_then_china(hint, caption, filename, default, expected):
    assert infer.infer_warehouse(hint, caption, filename, default) == expected


@pytest.mark.parametrize("texts,expected", [
    (["prices 2026-10-03"], date(2026, 10, 3)),
    (["updated 10/02/2026"], date(2026, 10, 2)),
    (["Oct 4 prices"], date(2026, 10, 4)),
    (["list 10.05"], date(2026, 10, 5)),
    (["dated 2025-01-01"], date(2026, 10, 6)),                           # more than 45 days away: use the message date
    (["no date here"], date(2026, 10, 6)),
    ([], date(2026, 10, 6)),
])
def test_the_date_is_read_from_the_text_when_close_to_the_message_else_the_message_date(texts, expected):
    assert infer.infer_date(texts, date(2026, 10, 6)) == expected


# ---------------------------------------------------------------- the decision

def test_a_confident_list_from_an_enabled_mapped_group_is_imported_and_logged(db, vendor):
    source = make_source(db, vendor=vendor)
    item = text_item(db, source, caption_extra="USA warehouse prices")
    assert run() == 1
    db.expire_all()
    item = db.get(IngestItem, item.id)
    plist = db.query(PriceList).one()
    assert (item.status, item.vendor_id, item.warehouse, item.list_date, item.price_list_id, item.rows_found) == (
        "imported", vendor.id, "us", date(2026, 10, 6), plist.id, 6)
    assert plist.warehouse.value == "us" and item.decided_by is None and item.decided_at == NOW


def test_an_assumed_warehouse_goes_to_the_inbox_not_the_data(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "needs_review" and "warehouse" in item.reason and item.warehouse == "china"
    assert db.query(PriceList).count() == 0


def test_the_groups_default_warehouse_makes_it_confident(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor, default_warehouse="china"))
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "imported"


def test_a_disabled_or_unmapped_group_never_imports_by_itself(db, vendor):
    disabled = make_source(db, vendor=vendor, enabled=False, chat_id="-2", default_warehouse="us")
    unmapped = make_source(db, vendor=None, chat_id="-3", default_warehouse="us")
    a = text_item(db, disabled, message_id="1")
    b = text_item(db, unmapped, message_id="2")
    run()
    db.expire_all()
    assert db.get(IngestItem, a.id).status == "needs_review" and "enabled" in db.get(IngestItem, a.id).reason
    assert db.get(IngestItem, b.id).status == "needs_review" and "vendor" in db.get(IngestItem, b.id).reason
    assert db.query(PriceList).count() == 0


def test_chatter_is_ignored_and_keeps_no_file(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor), text="Hello all, shipping is delayed this week")
    run()
    db.expire_all()
    item = db.get(IngestItem, item.id)
    assert item.status == "ignored" and "not a price list" in item.reason


def test_a_small_list_is_reviewed_because_it_has_fewer_than_five_rows(db, vendor):
    item = text_item(db, make_source(db, vendor=vendor, default_warehouse="us"), text="\n".join(LINES[:4]) + "\nprice list")
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "needs_review"


def test_a_list_older_than_the_current_one_is_reviewed(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="dated 2026-10-05")
    run()
    older = text_item(db, source, message_id="2", caption_extra="dated 2026-10-01", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    db.expire_all()
    assert db.get(IngestItem, older.id).status == "needs_review" and "older" in db.get(IngestItem, older.id).reason


def test_a_list_whose_prices_are_wildly_different_is_reviewed(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    pricey = [line.rsplit("$", 1)[0] + "$" + str(int(line.rsplit("$", 1)[1]) * 5) for line in LINES]
    item = text_item(db, source, "\n".join(pricey), message_id="2", caption_extra="2026-10-05")
    run()
    db.expire_all()
    assert db.get(IngestItem, item.id).status == "needs_review" and "prices" in db.get(IngestItem, item.id).reason
    assert db.query(PriceList).count() == 1


def test_the_same_content_in_a_new_message_is_a_duplicate(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    first = text_item(db, source, message_id="1")
    run()
    again = text_item(db, source, message_id="2", file_hash=first.file_hash)
    run()
    db.expire_all()
    assert db.get(IngestItem, again.id).status == "duplicate" and db.query(PriceList).count() == 1


def test_a_reader_failure_marks_the_item_failed_and_the_rest_carry_on(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    broken = IngestItem(source_id=source.id, message_id="5", group_key="g5", received_at=NOW, kind="xlsx", file_hash="5" * 64,
                        stored_file="missing-file", status="received", created_at=NOW)
    db.add(broken)
    db.commit()
    good = text_item(db, source, message_id="6")
    assert run() == 2
    db.expire_all()
    assert db.get(IngestItem, broken.id).status == "failed" and db.get(IngestItem, good.id).status == "imported"


# ---------------------------------------------------------------- photos settle before they are read

def photo_item(db, source, n, created, album="A"):
    stored = f"photo{n}"
    (config.INGEST_DIR / stored).write_bytes(png_bytes(n))
    item = IngestItem(source_id=source.id, message_id=str(n), album_id=album, group_key=f"{source.id}:a:{album}", received_at=created,
                      kind="image", file_hash=f"{n:0>64}", stored_file=stored, status="received", created_at=created)
    db.add(item)
    db.commit()
    return item


def test_photos_wait_60_seconds_after_the_last_one_then_are_read_as_one_list(db, vendor):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    a = photo_item(db, source, 1, NOW)
    b = photo_item(db, source, 2, NOW + timedelta(seconds=30))
    recognizer = lambda image: scan_words()
    assert run(NOW + timedelta(seconds=75), recognizer=recognizer) == 0          # only 45 s since the last photo
    assert run(NOW + timedelta(seconds=91), recognizer=recognizer) == 1
    db.expire_all()
    assert {db.get(IngestItem, a.id).status, db.get(IngestItem, b.id).status} == {"imported"}
    assert db.get(IngestItem, a.id).price_list_id == db.get(IngestItem, b.id).price_list_id and db.query(PriceList).count() == 1


# ---------------------------------------------------------------- approve, reject, undo

def test_approving_imports_with_the_edited_values_and_undo_removes_the_list(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    other = Vendor(name="Zephyr Labs")
    db.add(other)
    db.commit()
    done = process.approve_group(db, item.id, vendor_id=other.id, warehouse="us", list_date=date(2026, 10, 4), user_id=me)
    plist = db.query(PriceList).one()
    assert (done.status, done.vendor_id, done.warehouse, done.list_date, done.decided_by, plist.vendor_id) == (
        "imported", other.id, "us", date(2026, 10, 4), me, other.id)
    undone = process.undo_group(db, item.id, me)
    assert undone.status == "undone" and db.query(PriceList).count() == 0


def test_undo_restores_the_previous_current_list(db, vendor, me):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    first = text_item(db, source, message_id="1", caption_extra="2026-10-01")
    run()
    second = text_item(db, source, message_id="2", caption_extra="2026-10-05", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    assert db.query(PriceList).count() == 2
    process.undo_group(db, second.id, me)
    from app.library.price_lists.analysis import current_lists
    assert [p.list_date for p in current_lists(db)] == [date(2026, 10, 1)]


def test_reject_is_final_and_approving_or_undoing_the_wrong_state_is_refused(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    assert process.reject_group(db, item.id, me).status == "rejected"
    with pytest.raises(ValueError):
        process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    with pytest.raises(ValueError):
        process.undo_group(db, item.id, me)


def test_approving_twice_never_imports_twice(db, vendor, me):
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    with pytest.raises(ValueError):
        process.approve_group(db, item.id, vendor_id=vendor.id, warehouse="us", list_date=date(2026, 10, 6), user_id=me)
    assert db.query(PriceList).count() == 1


def test_two_warehouses_of_one_vendor_on_one_day_are_two_lists(db, vendor):
    source = make_source(db, vendor=vendor)
    text_item(db, source, message_id="1", caption_extra="USA warehouse")
    text_item(db, source, message_id="2", caption_extra="China warehouse", text="\n".join(LINES[:5]) + "\nZorvex ZX20 20mg*10vials $99")
    run()
    assert sorted(p.warehouse.value for p in db.query(PriceList)) == ["china", "us"]
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`app/ingest/infer.py`:
```python
"""Working out warehouse and date from what surrounds a price list."""

import re
from datetime import date, timedelta

_US = re.compile(r"\b(usa|us|u\.s\.a?\.?|united states)\b", re.IGNORECASE)
_CHINA = re.compile(r"\b(china|chinese|cn)\b", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
WINDOW_DAYS = 45


def _words_hint(text: str | None) -> str | None:
    us, china = _US.search(text or ""), _CHINA.search(text or "")
    if us and china:
        return "us" if us.start() < china.start() else "china"
    return "us" if us else "china" if china else None


def infer_warehouse(hint: str | None, caption: str | None, filename: str | None, default: str | None) -> tuple[str, bool]:
    """(warehouse, assumed): the list's own text, then words in the caption, then in the filename, then the group's default,
    then China, which is flagged as assumed."""
    if hint in ("us", "china"):
        return hint, False
    for text in (caption, filename):
        found = _words_hint(text)
        if found:
            return found, False
    if default in ("us", "china"):
        return default, False
    return "china", True


def _candidates(text: str, year: int):
    for m in re.finditer(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", text):
        yield int(m[1]), int(m[2]), int(m[3])
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b", text):
        yield int(m[3]), int(m[1]), int(m[2])
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b", text, re.IGNORECASE):
        yield year, _MONTHS[m[1].lower()], int(m[2])
    for m in re.finditer(r"(?<![\d./-])(\d{1,2})\.(\d{1,2})(?![\d./-])", text):
        yield year, int(m[1]), int(m[2])


def infer_date(texts: list[str], received: date) -> date:
    """A date written in the text, if it is within 45 days of the message; otherwise the message's own date."""
    for text in texts:
        for year, month, day in _candidates(text or "", received.year):
            try:
                found = date(year, month, day)
            except ValueError:
                continue
            if abs((found - received).days) <= WINDOW_DAYS:
                return found
    return received
```

`app/ingest/decide.py`:
```python
"""Whether a read list goes straight into the data or waits for the administrator."""

import statistics
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.library.price_lists.analysis import current_lists
from app.library.price_lists.reader import PriceListData
from app.ingest.readers import priced_fraction

MIN_ROWS, MIN_PRICED = 5, 0.8
RATIO_LOW, RATIO_HIGH = 0.5, 2.0


@dataclass
class Verdict:
    status: str                     # "imported" (meaning: import now) or "needs_review" or "duplicate"
    reason: str | None = None


def _per_vial(amount, unit, pack_size, pack_price):
    return (str(unit).lower(), round(float(amount), 3)), (pack_price / pack_size if pack_size and pack_price else None)


def median_price_ratio(session: Session, vendor_id: int, warehouse: str, data: PriceListData) -> float | None:
    """Median of new per-vial price / current per-vial price over the products (code and size) in both lists, or None."""
    current = next((p for p in current_lists(session) if p.vendor_id == vendor_id and p.warehouse.value == warehouse), None)
    if current is None:
        return None
    old = {}
    for i in current.items:
        if i.code and i.pack_price and i.pack_size:
            old[(i.code.upper(), str(i.vial_unit).lower(), round(float(i.vial_amount), 3))] = i.pack_price / i.pack_size
    ratios = []
    for r in data.rows:
        if r.code and r.spec and r.pack_price and r.spec.pack:
            key = (r.code.upper(), str(r.spec.unit).lower(), round(float(r.spec.amount), 3))
            if key in old and old[key] > 0:
                ratios.append((r.pack_price / r.spec.pack) / old[key])
    return statistics.median(ratios) if ratios else None


def decide(session: Session, *, vendor, enabled: bool, data: PriceListData, warehouse: str, assumed: bool, list_date: date,
           file_dupe: bool) -> Verdict:
    if file_dupe:
        return Verdict("duplicate", "the same file was already received")
    if vendor is None:
        return Verdict("needs_review", "choose the vendor for this group")
    if not enabled:
        return Verdict("needs_review", "this group is not enabled for automatic import")
    if len(data.rows) < MIN_ROWS or priced_fraction(data) < MIN_PRICED:
        return Verdict("needs_review", f"only {len(data.rows)} rows read, or too few have a price")
    if assumed:
        return Verdict("needs_review", "the warehouse was not stated, so China was assumed")
    held = next((p for p in current_lists(session) if p.vendor_id == vendor.id and p.warehouse.value == warehouse), None)
    if held is not None and list_date < held.list_date:
        return Verdict("needs_review", f"older than the current list ({held.list_date.isoformat()})")
    ratio = median_price_ratio(session, vendor.id, warehouse, data)
    if ratio is not None and not (RATIO_LOW <= ratio <= RATIO_HIGH):
        return Verdict("needs_review", f"prices differ a lot from the current list (median x{ratio:.2f}); possibly misread")
    return Verdict("imported")
```
(The `Spec` fields used here are `amount`, `unit`, `pack`; before writing, `grep -n "class Spec" -A8 app/library/price_lists/rows.py` and use the real field names. Tests above use `Spec(5, "mg", 10)` so the order is amount, unit, pack.)

`app/ingest/process.py`:
```python
"""Reading, deciding and importing what the intake stored; approve, reject and undo."""

import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.ingest import decide as deciding, infer, readers
from app.library.price_lists.analysis import current_lists
from app.library.price_lists.importer import import_for_vendor
from app.library.price_lists.reader import PriceListData
from app.models import IngestItem, IngestSource, PriceList, Vendor

lock = threading.RLock()                    # the worker, approvals and undo never touch the same item at once


def _group(session: Session, key: str) -> list[IngestItem]:
    return list(session.scalars(select(IngestItem).where(IngestItem.group_key == key).order_by(IngestItem.id)))


def _read(items: list[IngestItem], recognizer) -> PriceListData:
    first = items[0]
    if first.kind == "text":
        return readers.read_text("\n".join(i.caption or "" for i in items))
    path = config.INGEST_DIR / (first.stored_file or "")
    if not path.is_file():
        raise readers.ReadError("the stored file is missing")
    if first.kind == "pdf":
        return readers.read_pdf_file(path, recognize=recognizer)
    if first.kind == "xlsx":
        return readers.read_xlsx(path.read_bytes())
    from PIL import Image
    images = []
    for i in items:
        p = config.INGEST_DIR / (i.stored_file or "")
        if not p.is_file():
            raise readers.ReadError("a stored photo is missing")
        images.append(Image.open(p).convert("RGB"))
    return readers.read_images(images, recognize=recognizer)


def _set(items, now, **fields):
    for i in items:
        for k, v in fields.items():
            setattr(i, k, v)
        i.decided_at = now


def _drop_files(items):
    for i in items:
        if i.stored_file:
            (config.INGEST_DIR / i.stored_file).unlink(missing_ok=True)
            i.stored_file = None


def _context(session, items, source, data):
    first = items[0]
    caption = " ".join(i.caption or "" for i in items if i.kind != "text")
    texts = [caption, first.filename or "", data.shipping_note or ""] + [i.filename or "" for i in items]
    warehouse, assumed = infer.infer_warehouse(data.warehouse_hint, caption or (first.caption if first.kind == "text" else None),
                                               first.filename, source.default_warehouse)
    return warehouse, assumed, infer.infer_date(texts + ([first.caption] if first.kind == "text" else []), first.received_at.date())


def _import(session, items, vendor, data, warehouse, list_date, now, user_id=None):
    report = import_for_vendor(session, vendor, data, warehouse=warehouse, list_date=list_date)
    matched = report.matched
    _set(items, now, status="imported", reason=None, vendor_id=vendor.id, warehouse=warehouse, list_date=list_date,
         price_list_id=report.list_id, rows_found=report.rows, rows_matched=matched, decided_by=user_id)
    _drop_files(items)


def _file_dupe(session, items):
    seen = session.scalar(select(IngestItem.id).where(
        IngestItem.source_id == items[0].source_id, IngestItem.file_hash == items[0].file_hash, IngestItem.group_key != items[0].group_key,
        IngestItem.status.in_(("imported", "needs_review", "duplicate", "undone"))))
    return seen is not None


def _handle(session, items, now, recognizer):
    source = session.get(IngestSource, items[0].source_id)
    vendor = session.get(Vendor, source.vendor_id) if source.vendor_id else None
    try:
        data = _read(items, recognizer)
    except readers.ReadError as exc:
        _set(items, now, status="failed", reason=str(exc)[:300])
        return
    ok, why = readers.looks_like_price_list(data, " ".join(i.caption or "" for i in items), items[0].filename)
    if not ok:
        _set(items, now, status="ignored", reason=why)
        _drop_files(items)
        return
    warehouse, assumed, list_date = _context(session, items, source, data)
    verdict = deciding.decide(session, vendor=vendor, enabled=source.enabled, data=data, warehouse=warehouse, assumed=assumed,
                              list_date=list_date, file_dupe=_file_dupe(session, items))
    common = dict(vendor_id=vendor.id if vendor else None, warehouse=warehouse, list_date=list_date, rows_found=len(data.rows))
    if verdict.status == "imported":
        _import(session, items, vendor, data, warehouse, list_date, now)
    elif verdict.status == "duplicate":
        _set(items, now, status="duplicate", reason=verdict.reason, **common)
        _drop_files(items)
    else:
        _set(items, now, status="needs_review", reason=verdict.reason, **common)


def process_due(session_factory, now: datetime, recognizer=None) -> int:
    """Handle every received group that is ready: single files at once, photos 60 seconds after the last one arrived."""
    handled = 0
    with lock:
        with session_factory() as session:
            waiting = list(session.scalars(select(IngestItem).where(IngestItem.status == "received").order_by(IngestItem.id)))
            keys = list(dict.fromkeys(i.group_key for i in waiting))
            for key in keys:
                items = [i for i in _group(session, key) if i.status == "received"]
                if not items:
                    continue
                if items[0].kind == "image" and max(i.created_at for i in items) > now - timedelta(seconds=config.INGEST_SETTLE_SECONDS):
                    continue
                try:
                    _handle(session, items, now, recognizer)
                except Exception as exc:                               # a bug or a bad file must never stop the others
                    session.rollback()
                    items = [i for i in _group(session, key) if i.status == "received"]
                    _set(items, now, status="failed", reason=f"unexpected error ({type(exc).__name__})")
                session.commit()
                handled += 1
    return handled


def _pending(session, item_id, allowed):
    item = session.get(IngestItem, item_id)
    if item is None:
        raise LookupError("item not found")
    if item.status not in allowed:
        raise ValueError(f"an item that is {item.status} cannot be changed this way")
    return item, _group(session, item.group_key)


def approve_group(session: Session, item_id: int, *, vendor_id: int, warehouse: str, list_date: date, user_id: int,
                  recognizer=None, now: datetime | None = None) -> IngestItem:
    from app.models import naive_utcnow
    now = now or naive_utcnow()
    with lock:
        item, items = _pending(session, item_id, ("needs_review", "failed"))
        vendor = session.get(Vendor, vendor_id)
        if vendor is None or warehouse not in ("us", "china"):
            raise ValueError("choose a vendor and a warehouse")
        stored = [i for i in items if i.kind == "text" or i.stored_file]
        data = _read(stored, recognizer)
        _import(session, items, vendor, data, warehouse, list_date, now, user_id)
        session.commit()
        return item


def reject_group(session: Session, item_id: int, user_id: int, now: datetime | None = None) -> IngestItem:
    from app.models import naive_utcnow
    with lock:
        item, items = _pending(session, item_id, ("needs_review", "failed"))
        _set(items, now or naive_utcnow(), status="rejected", decided_by=user_id)
        _drop_files(items)
        session.commit()
        return item


def undo_group(session: Session, item_id: int, user_id: int, now: datetime | None = None) -> IngestItem:
    from app.models import naive_utcnow
    with lock:
        item, items = _pending(session, item_id, ("imported",))
        if item.price_list_id is not None:
            plist = session.get(PriceList, item.price_list_id)
            if plist is not None:
                session.delete(plist)
        _set(items, now or naive_utcnow(), status="undone", price_list_id=None, decided_by=user_id)
        session.commit()
        return item
```
Ruling to carry: files are deleted after import/reject/ignore/duplicate (originals are kept only while an item awaits review or failed), so "Download original" is available only for those states; `approve_group` therefore re-reads the kept files. Record this in the ledger.

`app/ingest/worker.py`:
```python
"""The background loop that processes settled items every 20 seconds."""

import asyncio

from app import config
from app.db import SessionLocal
from app.ingest import process
from app.models import naive_utcnow

INTERVAL = 20


async def _loop():
    while True:
        try:
            await asyncio.to_thread(process.process_due, SessionLocal, naive_utcnow())
        except Exception:
            pass                                    # the next round tries again; an item that crashed is already marked failed
        await asyncio.sleep(INTERVAL)


def start():
    return asyncio.create_task(_loop()) if config.INGEST_WORKER_ENABLED else None


async def stop(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```
`app/main.py`: in `lifespan`, after `load_starter(session)`: `task = worker.start()`; `yield`; `await worker.stop(task)` (adapt to the existing yield structure).

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add app/ingest/infer.py app/ingest/decide.py app/ingest/process.py app/ingest/worker.py app/main.py tests/test_ingest_process.py
git commit -m "feat: ingest inference, confidence rule, processing worker, approve, reject and undo"
```

---

### Task 5: The Price list inbox page

**Files:**
- Create: `app/routers/ingest_admin.py`, `app/templates/settings/ingest.html`, `tests/test_ingest_admin.py`
- Modify: `app/main.py` (register router), `app/templates/settings/settings.html` (a link in the admin section), `app/static/css/app.css` (small styles)

**Interfaces:**
- Consumes: Task 2 `tokens.create_token/revoke_token`; Task 4 `process.approve_group/reject_group/undo_group`; the admin check `_require_admin` pattern from `app/routers/settings.py` (404 for non-admins); `current_user_id`; `templates` from `app.templating`.
- Produces routes (all administrator-only, 404 otherwise): `GET /settings/ingest`; `POST /settings/ingest/tokens` (form `label`, shows the secret once on the response page); `POST /settings/ingest/tokens/{id}/revoke`; `POST /settings/ingest/sources/{id}` (form `vendor_id` or `new_vendor` = 1 to create a vendor named like the group, `default_warehouse`, `enabled`); `POST /settings/ingest/items/{id}/approve|reject|undo`; `GET /settings/ingest/items/{id}/original`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_admin.py`): use `client` (signed in as the administrator) and the second-user fixture used elsewhere in the suite (`grep -n "def second_client\|def other_client\|def member" tests/conftest.py` and reuse; if none exists, create a plain user with `users.create_user` in the test).

```python
from datetime import date

import pytest

from app import config
from app.models import IngestItem, IngestToken, PriceList, Vendor, naive_utcnow
from ingest_helpers import make_source
from test_ingest_process import LINES, NOW, run, text_item


def test_only_the_administrator_sees_the_inbox(client, member_client, db):
    assert client.get("/settings/ingest").status_code == 200
    for path in ("/settings/ingest", "/settings/ingest/items/1/original"):
        assert member_client.get(path).status_code == 404
    for path in ("/settings/ingest/tokens", "/settings/ingest/tokens/1/revoke", "/settings/ingest/sources/1", "/settings/ingest/items/1/approve",
                 "/settings/ingest/items/1/reject", "/settings/ingest/items/1/undo"):
        assert member_client.post(path, data={}).status_code == 404


def test_a_token_secret_is_shown_once_and_can_be_revoked(client, db):
    page = client.post("/settings/ingest/tokens", data={"label": "Home PC"})
    assert page.status_code == 200 and "amide_ing_" in page.text
    secret = next(w for w in page.text.replace("<", " ").replace(">", " ").split() if w.startswith("amide_ing_") and len(w) > 30)
    again = client.get("/settings/ingest")
    assert secret not in again.text and "Home PC" in again.text
    token = db.query(IngestToken).one()
    assert secret != token.token_hash
    client.post(f"/settings/ingest/tokens/{token.id}/revoke", data={})
    db.expire_all()
    assert db.get(IngestToken, token.id).revoked_at is not None


def test_a_source_can_be_mapped_enabled_and_given_a_default_warehouse(client, db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
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
    assert "needs_review" in page or "Needs review" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_approve_reject_and_undo_from_the_inbox(client, db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    source = make_source(db, vendor=vendor)
    item = text_item(db, source)
    run()
    r = client.post(f"/settings/ingest/items/{item.id}/approve", data={"vendor_id": str(vendor.id), "warehouse": "us", "list_date": "2026-10-05"},
                    follow_redirects=False)
    assert r.status_code == 303 and db.query(PriceList).count() == 1
    client.post(f"/settings/ingest/items/{item.id}/undo", data={})
    assert db.query(PriceList).count() == 0
    second = text_item(db, source, message_id="2", text="\n".join(LINES), file_hash="2" * 64)
    run()
    client.post(f"/settings/ingest/items/{second.id}/reject", data={})
    db.expire_all()
    assert db.get(IngestItem, second.id).status == "rejected"


def test_a_bad_form_value_is_refused_with_a_message_not_a_server_error(client, db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    item = text_item(db, make_source(db, vendor=vendor))
    run()
    for data in ({"vendor_id": "x", "warehouse": "us", "list_date": "2026-10-05"}, {"vendor_id": str(vendor.id), "warehouse": "mars", "list_date": "2026-10-05"},
                 {"vendor_id": str(vendor.id), "warehouse": "us", "list_date": "nope"}):
        assert client.post(f"/settings/ingest/items/{item.id}/approve", data=data).status_code in (303, 422)
    assert db.query(PriceList).count() == 0


def test_the_original_is_downloadable_by_the_administrator_only_while_it_is_kept(client, db):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    source = make_source(db, vendor=vendor)
    stored = "keepme"
    (config.INGEST_DIR / stored).write_bytes(b"%PDF-1.4 hello")
    item = IngestItem(source_id=source.id, message_id="1", group_key="g", received_at=NOW, kind="pdf", file_hash="9" * 64, filename="x.pdf",
                      stored_file=stored, status="needs_review", created_at=NOW)
    db.add(item)
    db.commit()
    r = client.get(f"/settings/ingest/items/{item.id}/original")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 hello" and r.headers["cache-control"] == "no-store"
    assert client.get("/settings/ingest/items/9999/original").status_code == 404
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** `app/routers/ingest_admin.py` follows `app/routers/settings.py`: `router = APIRouter()`, `_require_admin(request, session)` copied as a local helper (404 for non-admins), every handler takes `session: Session = Depends(get_session)` and `request`, forms read with `await request.form()`, successful actions answer `RedirectResponse("/settings/ingest", status_code=303)`, bad input answers 422 with a one-line message (catch `ValueError`/`LookupError` from `process`). Page context: tokens (id, label, prefix, created/last used, revoked), sources with vendor options (`select(Vendor).order_by(Vendor.name)`), the newest 100 items (join-free: load sources into a dict), status filter via `?status=`, and for each item `preview` = rows via `readers` is **not** recomputed; show `rows_found`, `reason`, filename, caption (escaped by Jinja autoescape), warehouse, date and the editable approve form (vendor select, warehouse select, date input) for `needs_review` and `failed`, **Undo** for `imported`. `GET .../original` returns `FileResponse(path, media_type="application/octet-stream", filename=f"item-{id}", headers={"Cache-Control": "no-store"})` and 404 when the file is gone. Template `settings/ingest.html` extends the same base as `settings/settings.html` (copy its first lines), uses the existing card/table classes (see `vendors/list.html`), no inline scripts. Add a link "Price list inbox" (`/settings/ingest`) inside the `{% if me.is_admin %}<section id="admin" ...>` block of `settings.html`. Register the router in `app/main.py`.

- [ ] **Step 4: Run to verify it passes**, then the full suite. Drive the page once in the browser pane (`preview_start`, DOM checks by `javascript_tool`) to see it renders for the administrator.

- [ ] **Step 5: Commit**
```bash
git add app/routers/ingest_admin.py app/templates/settings/ingest.html app/templates/settings/settings.html app/main.py app/static/css/app.css tests/test_ingest_admin.py
git commit -m "feat: price list inbox for the administrator (tokens, groups, review, approve, reject, undo)"
```

---

### Task 6: Dashboard alerts

**Files:**
- Create: `app/ingest/alerts.py`, `tests/test_ingest_alerts.py`
- Modify: `app/routers/dashboard.py`, `app/templates/dashboard/index.html`

**Interfaces:**
- Consumes: Task 1 models; `config.INGEST_NEW_LIST_DAYS`.
- Produces in `alerts.py`: `new_list_alerts(session, user_id: int, now: datetime) -> list[dict]` (`{"key": "newlist:<price_list_id>", "vendor": str, "vendor_id": int | None, "text": "<VENDOR> released new price list."}`, newest first, only ingest-imported lists within 7 days, not dismissed by this user); `group_gone_alerts(session, user: User) -> list[dict]` (administrators only; `{"key": "gone:<source_id>", "source_id", "text": "<VENDOR> Telegram group is no longer active."}`); `dismiss(session, user: User, key: str) -> None` (a `newlist:` key adds a `DashboardDismissal`; a `gone:` key by the administrator sets `alert_acknowledged_at = now`; anything else raises `ValueError`).
- Produces route: `POST /dashboard/alerts/dismiss` (form `key`) → 303 to `/`.
- The dashboard's existing `alerts` dict gains `"new_lists"` and `"groups_gone"`; the template's `has_alerts` includes them.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_alerts.py`)

```python
from datetime import datetime, timedelta

from app.ingest import alerts
from app.models import DashboardDismissal, IngestItem, IngestSource, PriceList, User, Vendor, naive_utcnow
from ingest_helpers import make_source
from test_ingest_process import NOW, run, text_item


def imported(db, vendor, **kw):
    source = make_source(db, vendor=vendor, default_warehouse="us")
    text_item(db, source, **kw)
    run()
    return source


def test_an_ingest_import_raises_a_new_list_alert_for_everyone(client, db, me):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    imported(db, vendor)
    items = alerts.new_list_alerts(db, me, NOW)
    assert [a["text"] for a in items] == ["Acme Labs released new price list."] and items[0]["vendor_id"] == vendor.id
    page = client.get("/")
    assert "Acme Labs released new price list." in page.text


def test_a_manual_upload_and_an_old_import_raise_nothing(db, me):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    imported(db, vendor)
    assert alerts.new_list_alerts(db, me, NOW + timedelta(days=8)) == []
    for item in db.query(IngestItem):
        item.status = "undone"
        item.price_list_id = None
    db.commit()
    assert alerts.new_list_alerts(db, me, NOW) == []


def test_each_person_dismisses_their_own(client, db, me):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    imported(db, vendor)
    key = alerts.new_list_alerts(db, me, NOW)[0]["key"]
    client.post("/dashboard/alerts/dismiss", data={"key": key})
    assert alerts.new_list_alerts(db, me, NOW) == [] and db.query(DashboardDismissal).count() == 1
    other = User(username="second", password_hash="x")
    db.add(other)
    db.commit()
    assert len(alerts.new_list_alerts(db, other.id, NOW)) == 1


def test_a_group_going_away_alerts_the_administrator_only_until_acknowledged(client, db, me):
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    source = make_source(db, vendor=vendor, title="Acme chat")
    assert alerts.group_gone_alerts(db, db.get(User, me)) == []
    source.state, source.state_changed_at = "gone", NOW
    db.commit()
    shown = alerts.group_gone_alerts(db, db.get(User, me))
    assert [a["text"] for a in shown] == ["Acme Labs Telegram group is no longer active."]
    other = User(username="second", password_hash="x", is_admin=False)
    db.add(other)
    db.commit()
    assert alerts.group_gone_alerts(db, other) == []
    client.post("/dashboard/alerts/dismiss", data={"key": shown[0]["key"]})
    assert alerts.group_gone_alerts(db, db.get(User, me)) == []
    source.state, source.state_changed_at = "gone", NOW + timedelta(days=1)         # goes away again later: it returns
    db.commit()
    assert len(alerts.group_gone_alerts(db, db.get(User, me))) == 1
    source.state = "active"
    db.commit()
    assert alerts.group_gone_alerts(db, db.get(User, me)) == []


def test_an_unmapped_group_uses_its_title_and_a_bad_key_is_refused(client, db, me):
    source = make_source(db, vendor=None, title="Quillamine chat")
    source.state, source.state_changed_at = "gone", NOW
    db.commit()
    assert alerts.group_gone_alerts(db, db.get(User, me))[0]["text"] == "Quillamine chat Telegram group is no longer active."
    assert client.post("/dashboard/alerts/dismiss", data={"key": "bogus"}).status_code == 422
    assert client.post("/dashboard/alerts/dismiss", data={"key": "newlist:abc"}).status_code == 422
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** `app/ingest/alerts.py`:
```python
"""Dashboard alerts the ingest raises: a new price list arrived, and a group stopped being watchable."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import DashboardDismissal, IngestItem, IngestSource, PriceList, User, Vendor, naive_utcnow


def _vendor_name(session: Session, vendor_id: int | None, fallback: str) -> str:
    vendor = session.get(Vendor, vendor_id) if vendor_id else None
    return vendor.name if vendor is not None else fallback


def new_list_alerts(session: Session, user_id: int, now: datetime) -> list[dict]:
    since = now - timedelta(days=config.INGEST_NEW_LIST_DAYS)
    dismissed = set(session.scalars(select(DashboardDismissal.alert_key).where(DashboardDismissal.user_id == user_id)))
    seen, out = set(), []
    rows = session.scalars(select(IngestItem).where(IngestItem.status == "imported", IngestItem.price_list_id.is_not(None),
                                                    IngestItem.decided_at >= since).order_by(IngestItem.decided_at.desc(), IngestItem.id.desc()))
    for item in rows:
        key = f"newlist:{item.price_list_id}"
        if key in dismissed or key in seen or session.get(PriceList, item.price_list_id) is None:
            continue
        seen.add(key)
        name = _vendor_name(session, item.vendor_id, "A vendor")
        out.append({"key": key, "vendor": name, "vendor_id": item.vendor_id, "text": f"{name} released new price list."})
    return out


def group_gone_alerts(session: Session, user: User) -> list[dict]:
    if not user.is_admin:
        return []
    out = []
    for s in session.scalars(select(IngestSource).where(IngestSource.state == "gone").order_by(IngestSource.id)):
        if s.alert_acknowledged_at is not None and s.state_changed_at is not None and s.alert_acknowledged_at >= s.state_changed_at:
            continue
        name = _vendor_name(session, s.vendor_id, s.title)
        out.append({"key": f"gone:{s.id}", "source_id": s.id, "text": f"{name} Telegram group is no longer active."})
    return out


def dismiss(session: Session, user: User, key: str) -> None:
    kind, _, ident = key.partition(":")
    if not ident.isdigit():
        raise ValueError("unknown alert")
    if kind == "newlist":
        if session.scalar(select(DashboardDismissal.id).where(DashboardDismissal.user_id == user.id, DashboardDismissal.alert_key == key)) is None:
            session.add(DashboardDismissal(user_id=user.id, alert_key=key))
            session.commit()
    elif kind == "gone" and user.is_admin:
        source = session.get(IngestSource, int(ident))
        if source is not None:
            source.alert_acknowledged_at = naive_utcnow()
            session.commit()
    else:
        raise ValueError("unknown alert")
```
`app/routers/dashboard.py`: next to the existing alert entries add `"new_lists": new_list_alerts(session, uid, naive_utcnow())` and `"groups_gone": group_gone_alerts(session, me)` (use the user object already loaded there), and add `@router.post("/dashboard/alerts/dismiss")` calling `dismiss` (catch `ValueError` → `HTTPException(422)`) then `RedirectResponse("/", status_code=303)`. In `dashboard/index.html` extend `has_alerts` with `alerts.new_lists or alerts.groups_gone` and render two lists in the existing alert-panel style: each item shows its `text` (vendor name linked to `/vendors/{{ a.vendor_id }}` for new lists) with a small **Dismiss** form (`method="post" action="/dashboard/alerts/dismiss"`, hidden `key`). Check how the existing dismiss/ignore buttons are written in the template and match them.

- [ ] **Step 4: Run to verify it passes**, then the full suite; open `/` in the browser pane as the administrator with a seeded item and check the DOM for both lines.

- [ ] **Step 5: Commit**
```bash
git add app/ingest/alerts.py app/routers/dashboard.py app/templates/dashboard/index.html tests/test_ingest_alerts.py
git commit -m "feat: dashboard alerts when a price list is released and when a group is no longer active"
```

---

### Task 7: Backup section and file cleanup

**Files:**
- Modify: `app/backup/sections.py` and, only if the registry tests demand it, `app/backup/export.py`/`load.py`
- Create: `tests/test_ingest_backup.py`

**Interfaces:**
- Consumes: `Section`, `Tbl`, `FILE_DIRS`, `LOAD_ORDER`, `NOT_BACKED_UP` from `app/backup/sections.py`; follow exactly how the body-photos section (installation-level or per-owner with files) and the vendor/price-list sections are registered (`grep -n "body_photos\|price_lists" app/backup/sections.py`).
- Produces: a section `ingest` (title "Price list inbox"), installation-level, not shareable, holding tables `ingest_sources` and `ingest_items` (`file_required=False`, stored files from `config.INGEST_DIR`); `ingest_tokens` and `dashboard_dismissals` added to `NOT_BACKED_UP` with a one-line reason each; load order places `ingest_sources` after `vendors`, `ingest_items` after `ingest_sources` and `price_lists`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ingest_backup.py`). First read `tests/test_backup_*.py` for the round-trip helper used for the food or body-photo sections (`grep -ln "restore_installation\|export_" tests`) and copy its style:

```python
from app.models import IngestItem, IngestSource, IngestToken, DashboardDismissal
from ingest_helpers import make_source
from test_ingest_process import NOW


def test_the_registry_knows_every_ingest_table_and_declares_what_it_leaves_out():
    from app.backup import sections
    assert {"ingest_sources", "ingest_items"} <= {t.name for s in sections.SECTIONS for t in s.tables}
    assert {"ingest_tokens", "dashboard_dismissals"} <= set(sections.NOT_BACKED_UP)


def test_sources_items_and_kept_files_survive_an_export_and_restore(db, me, tmp_path):
    ...  # export the installation, wipe ingest rows and files, restore, then assert the source (title, mapping by vendor name, default
         # warehouse, state), the needs_review item (reason, vendor, kept file bytes under a new stored name) and no token/dismissal rows
```
Write the body using the same export/restore calls the nearest existing backup test uses; assert the stored file's bytes round-trip and that a restored item's `stored_file` points to an existing file in `INGEST_DIR`. Add a second test: restoring a file that lacks the ingest section (an older backup) succeeds and leaves the inbox empty.

- [ ] **Step 2: Run to verify it fails** (the guard test `test_the_registry_covers_every_table_in_the_schema` has been red since Task 1; it must be green after this task).

- [ ] **Step 3: Implement** the section entries exactly as the existing sections do (copy the shape of the photos/foods registration: `Section(key="ingest", title="Price list inbox", ...)`, `Tbl("ingest_sources", ...)`, `Tbl("ingest_items", ..., file="stored_file", file_required=False)`, `FILE_DIRS["ingest_items"] = config.INGEST_DIR`, `MERGE_KEYS["ingest_sources"] = ("platform", "chat_id")`, references from `vendor_id` and `price_list_id` resolved by the existing `REFS` mechanism like `price_list_items.peptide_id`). `NOT_BACKED_UP` additions: `"ingest_tokens": "secrets: create a new one after a restore"`, `"dashboard_dismissals": "per-person alert noise"`.

- [ ] **Step 4: Run to verify it passes**, then the full suite (the guard test must be green again).

- [ ] **Step 5: Commit**
```bash
git add app/backup tests/test_ingest_backup.py
git commit -m "feat: back up the price list inbox (sources, items and kept files); tokens and dismissals stay out"
```

---

### Task 8: Roadmap note, whole-branch review, scan and push

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1:** Add a roadmap note: "Price list ingest, part A (done): token-protected intake, readers for PDF, photos, spreadsheets and typed text, inference, auto-import with undo, inbox, dashboard alerts. Part B (the Telegram watcher program on the owner's computer) is next and has its own spec." No vendor names.
- [ ] **Step 2:** Run the full suite in the background and read the tail (`Expected: all passed`).
- [ ] **Step 3:** Run the vendor-name scan: `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` and read the whole output (`Expected: only the one accepted old-spec hit`). Also run it with the revision range of this branch (`origin/main..HEAD`).
- [ ] **Step 4:** One independent opus review of the whole branch using the review package (`review-package`, plan Review Focus verbatim, ledger rulings pointed out). Re-grade; one fix pass, each fix with a failing test first; minors to the ledger.
- [ ] **Step 5: Commit** (message ends with the attribution line from the session) and, because the owner said commit means push, `git push` to origin main after the scan is clean.

---

## Self-review against the spec

- **Contract and limits** (sources PUT/GET/state, messages, 401/429, caps): Task 2. **Tokens hashed, shown once, admin-only creation:** Tasks 2 and 5.
- **Readers (PDF, photos, xlsx, text), kind by content, price-list test:** Tasks 2 and 3. **Album and time-cluster grouping with 60 s settle:** Tasks 2 and 4.
- **Inference (vendor, warehouse precedence, date window):** Task 4. **Automatic rule at each boundary, duplicates, ratio check:** Task 4 (tests cover rows, warehouse assumed, group default, disabled, unmapped, older, ratio, duplicate).
- **Approve, reject, undo, edit vendor/warehouse/date, download original:** Tasks 4 and 5. **Failure never a server error:** Task 4 (`failed`, worker catch-all).
- **Alerts (both, dismissal, 7-day expiry, admin-only gone alert, returns after second shutdown, clears on active):** Task 6.
- **Backup (section; tokens and dismissals excluded):** Task 7. **Roadmap, scan, review, push:** Task 8.
- **Rulings already made while planning (record in the ledger):** originals are kept only while an item is awaiting review or failed, then deleted (Task 4); `group_key` column added to `ingest_items` (Task 1); the worker is disabled under test by an environment flag (Task 1); the session gate lets `/api/ingest/` through because those routes require their own token and never accept a session (Task 2).
- **Known soft spots for the executor to confirm before coding:** the exact `Spec` field names in `decide.py`; the existing second-user fixture name for the admin tests; the dashboard route's variable names; the base template name in `settings.html`. Each is a one-grep check and noted at its task.
