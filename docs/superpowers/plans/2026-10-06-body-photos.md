# Body Photos with Optional 2FA Unlock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Private body-photo gallery on Weight & Measurements: server-side blurred until revealed, optional account-2FA unlock for 10 minutes, never shared, carried in the owner's own backup.

**Architecture:** One `body_photos` table plus one JPEG per photo in `data/uploads/body_photos`. The blurred preview is generated per request from the stored JPEG; the sharp file is served only to its owner and, when `users.photo_2fa_required` is set, only while the browser session's `sessions.photo_unlocked_until` is in the future. The unlock reuses the account's TOTP check (`check_code`) with login's one-time-use and lockout rules. Backup integration is one new person-level section that is not shareable.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic (SQLite), Jinja2, Pillow 12 + pillow-heif, pyotp (already present), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-06-body-photos-design.md`

## Global Constraints

- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. Suite must stay green after every task (baseline 1498).
- Test data is invented. No real photos, vendors or personal data in the repo. The vendor-name scan `/c/tmp/amide-scrub/denylist_scan.py` must stay clean.
- Photo responses always send `Cache-Control: private, no-store`. A photo id that is not the signed-in user's is **404 for everyone**, including the administrator and people the owner shares with.
- Unlock lasts exactly 10 minutes (`config.PHOTO_UNLOCK_MINUTES`), is never extended by activity, and ends on logout, Lock now, or expiry.
- Times are naive UTC via `app.auth.sessions.now_utc()`; tests patch `sessions.now_utc` to move the clock.
- Dates in the UI use `shortdate` (MM/DD/YYYY). Match surrounding code style: sparse comments, no emojis.
- Tests that need a second user use the helper `other_client()` added in Task 3 (`tests/photo_helpers.py`).

## Review Focus

1. A guessed photo id that belongs to someone else is 404, never 403 (a 403 would confirm it exists). (Task 3)
2. A corrupted or truncated image, or a non-image renamed `.jpg`, is refused with a plain message and leaves no file behind. (Task 2)
3. A tiny file with huge pixel dimensions is refused before it is decoded to memory. (Task 2)
4. Unlocking in one browser tab and locking in another: the state is per session row, so the next request in either tab sees it. (Task 3)
5. The unlock lives in the database, so it survives a server restart and still expires on time. (Task 3)
6. Account 2FA removed while photos are unlocked: the setting is switched off and photos are no longer served as "unlocked by 2FA". (Task 5)

## File Structure

- Create `migrations/versions/0037_body_photos.py` (table + two columns).
- Create `app/body_photos.py` (validate, clean, store, blur) and `app/photo_access.py` (unlock rules).
- Create `app/routers/body_photos.py` (photo routes + the settings toggle route); register in `app/main.py`.
- Create `app/templates/measurements/_body_photos.html`, `app/static/js/body-photos.js`; edit `app/templates/measurements/index.html`, `app/templates/settings/settings.html`, `app/static/css/app.css`.
- Edit `app/models.py`, `app/config.py`, `requirements.txt`, `app/routers/measurements.py` (context), `app/routers/auth.py`, `app/routers/settings.py`, `app/users.py`, `app/backup/sections.py`, `tests/conftest.py`, `docs/ROADMAP.md`.
- Tests: `tests/photo_helpers.py`, `tests/test_body_photos_store.py`, `tests/test_body_photos_access.py`, `tests/test_body_photos_page.py`, `tests/test_body_photos_setting.py`, `tests/test_body_photos_backup.py`.

---

### Task 1: Schema, config and dependencies

**Files:**
- Create: `migrations/versions/0037_body_photos.py`
- Modify: `app/models.py`, `app/config.py`, `requirements.txt`, `tests/conftest.py`
- Test: `tests/test_body_photos_store.py` (model part), `tests/test_migrations.py` (existing, must still pass)

**Interfaces:**
- Produces: `app.models.BodyPhoto(id, owner_id, taken_on, angle, note, filename, created_at)`; `User.photo_2fa_required: bool`; `LoginSession.photo_unlocked_until: datetime | None`; `config.BODY_PHOTO_DIR`, `config.PHOTO_UNLOCK_MINUTES = 10`, `config.PHOTO_MAX_SIDE = 2000`, `config.PHOTO_MAX_PIXELS = 50_000_000`; `BODY_PHOTO_ANGLES = ("front", "side", "back", "other")` in `app.models`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_body_photos_store.py` with only the model test for now:

```python
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import config
from app.models import BodyPhoto, LoginSession, User


def test_a_photo_row_round_trips_and_defaults_are_off(db, me):
    db.add(BodyPhoto(owner_id=me, taken_on=date(2026, 10, 6), angle="front", note="start", filename="a" * 32 + ".jpg"))
    db.commit()
    photo = db.query(BodyPhoto).one()
    assert (photo.angle, photo.note, photo.created_at is not None) == ("front", "start", True)
    assert db.get(User, me).photo_2fa_required is False
    assert db.query(LoginSession).first().photo_unlocked_until is None


def test_an_unknown_angle_is_refused_by_the_database(db, me):
    db.add(BodyPhoto(owner_id=me, taken_on=date(2026, 10, 6), angle="top", filename="b" * 32 + ".jpg"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_the_photo_folder_is_created_with_the_other_upload_folders():
    config.ensure_dirs()
    assert config.BODY_PHOTO_DIR.is_dir() and config.PHOTO_UNLOCK_MINUTES == 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_store.py`
Expected: FAIL at import (`BodyPhoto` does not exist).

- [ ] **Step 3: Implement**

`requirements.txt`: append two lines `Pillow==12.3.0` and `pillow-heif==1.8.0`. Install: `.venv/Scripts/python.exe -m pip install pillow-heif==1.8.0`.

`app/config.py`: after `WALLET_QR_DIR` add
```python
BODY_PHOTO_DIR = UPLOAD_DIR / "body_photos"
PHOTO_UNLOCK_MINUTES = 10
PHOTO_MAX_SIDE = 2000
PHOTO_MAX_PIXELS = 50_000_000
```
and in `ensure_dirs()` add `BODY_PHOTO_DIR.mkdir(parents=True, exist_ok=True)`.

`app/models.py`: add `BODY_PHOTO_ANGLES = ("front", "side", "back", "other")` near the other module constants (next to `WALLET_COINS`); add to `User` (after `shipment_delay_days`):
```python
    photo_2fa_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```
add to `LoginSession` (after `last_seen`):
```python
    photo_unlocked_until: Mapped[datetime | None] = mapped_column(DateTime)
```
and add the model after `WaterLog`/`BodyMeasurement` area (before `JournalEntry`):
```python
class BodyPhoto(Base):
    """A private progress photo, owner-only. `filename` is a random name of a JPEG in config.BODY_PHOTO_DIR."""
    __tablename__ = "body_photos"
    __table_args__ = (
        CheckConstraint("angle IS NULL OR angle IN ('front', 'side', 'back', 'other')", name="ck_body_photo_angle"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    taken_on: Mapped[date] = mapped_column(Date)
    angle: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

`migrations/versions/0037_body_photos.py`:
```python
"""body_photos table, users.photo_2fa_required, sessions.photo_unlocked_until

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0037'
down_revision: Union[str, None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'body_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('taken_on', sa.Date(), nullable=False),
        sa.Column('angle', sa.String(10)),
        sa.Column('note', sa.String(200)),
        sa.Column('filename', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("angle IS NULL OR angle IN ('front', 'side', 'back', 'other')", name='ck_body_photo_angle'),
    )
    op.create_index('ix_body_photos_owner_id', 'body_photos', ['owner_id'])
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('photo_2fa_required', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.add_column(sa.Column('photo_unlocked_until', sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.drop_column('photo_unlocked_until')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('photo_2fa_required')
    op.drop_index('ix_body_photos_owner_id', table_name='body_photos')
    op.drop_table('body_photos')
```

`tests/conftest.py`: import `BodyPhoto` in the models import list; in `clean()` add `s.query(BodyPhoto).delete()` before `s.commit()`; after the wallet loop add
```python
    for f in config.BODY_PHOTO_DIR.glob("*"):
        f.unlink()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_store.py tests/test_migrations.py`
Expected: PASS. Then the full suite: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` → all pass except any backup guard test naming the unregistered `body_photos` table (that test is satisfied in Task 6; if it fails now, note it and continue).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0037_body_photos.py app/models.py app/config.py requirements.txt tests/conftest.py tests/test_body_photos_store.py
git commit -m "feat: body photos schema (photos table, per-account setting, per-session unlock time)"
```

---

### Task 2: Photo processing and storage

**Files:**
- Create: `app/body_photos.py`, `tests/photo_helpers.py`
- Modify: `tests/test_body_photos_store.py`

**Interfaces:**
- Consumes: Task 1 config values.
- Produces: `app.body_photos.PhotoError(ValueError)`; `process_upload(data: bytes) -> bytes` (clean JPEG bytes); `store(jpeg: bytes) -> str` (writes a file, returns its random name); `photo_path(name: str) -> Path` (raises `ValueError` for any name not matching `^[0-9a-f]{32}[.]jpg$`); `delete_file(name: str | None) -> None`; `blurred_jpeg(path: Path) -> bytes`.
- Test helpers in `tests/photo_helpers.py`: `jpeg(size=(300, 200), exif=False)`, `png(size=(64, 64), alpha=False)`, `heic(size=(64, 64))`, `gradient(size)` (a Pillow image with a strong detail pattern, so blur is measurable), `spread(jpeg_bytes) -> float` (pixel standard deviation).

- [ ] **Step 1: Write the failing tests**

Create `tests/photo_helpers.py`:
```python
"""Builders for body-photo tests: tiny synthetic images only, never real photos."""

import io
import statistics

from PIL import Image, ImageDraw

try:
    from pillow_heif import from_pillow
except ImportError:  # pragma: no cover
    from_pillow = None


def gradient(size=(300, 200)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 6):
        draw.line([(x, 0), (x, size[1])], fill=(x * 255 // size[0], 40, 200 - x * 200 // size[0]), width=3)
    for y in range(0, size[1], 10):
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0), width=1)
    return image


def jpeg(size=(300, 200), exif=False, orientation=None):
    buffer = io.BytesIO()
    kwargs = {}
    if exif or orientation:
        tags = Image.Exif()
        tags[0x010F] = "TestMake"                    # camera make
        tags[0x8825] = {1: "N", 2: (40.0, 0.0, 0.0)}  # a GPS block
        if orientation:
            tags[0x0112] = orientation
        kwargs["exif"] = tags
    gradient(size).save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


def png(size=(64, 64), alpha=False):
    buffer = io.BytesIO()
    image = gradient(size).convert("RGBA" if alpha else "RGB")
    if alpha:
        image.putalpha(0)
    image.save(buffer, "PNG")
    return buffer.getvalue()


def heic(size=(64, 64)):
    buffer = io.BytesIO()
    from_pillow(gradient(size)).save(buffer, format="HEIF")
    return buffer.getvalue()


def spread(jpeg_bytes):
    """How much detail an image holds: the standard deviation of its grey levels."""
    grey = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    return statistics.pstdev(grey.getdata())
```

Append to `tests/test_body_photos_store.py`:
```python
import io

from PIL import Image

from app import body_photos
from app.body_photos import PhotoError, blurred_jpeg, delete_file, photo_path, process_upload, store
from photo_helpers import gradient, heic, jpeg, png, spread


def opened(data):
    return Image.open(io.BytesIO(data))


def test_metadata_is_stripped_and_the_result_is_a_jpeg():
    cleaned = process_upload(jpeg(exif=True))
    image = opened(cleaned)
    assert image.format == "JPEG" and len(image.getexif()) == 0 and "exif" not in image.info


def test_orientation_is_applied_not_just_dropped():
    cleaned = process_upload(jpeg(size=(300, 200), orientation=6))     # 6 = rotate 90 degrees
    assert opened(cleaned).size == (200, 300)


def test_a_large_image_is_shrunk_to_the_longest_side_limit():
    big = io.BytesIO()
    Image.new("RGB", (3000, 1000), "gray").save(big, "JPEG")
    assert max(opened(process_upload(big.getvalue())).size) == 2000


def test_png_with_transparency_is_flattened_onto_white():
    cleaned = opened(process_upload(png(alpha=True))).convert("RGB")
    assert cleaned.getpixel((5, 5)) == (255, 255, 255)


def test_an_iphone_heic_photo_is_converted():
    assert opened(process_upload(heic())).format == "JPEG"


@pytest.mark.parametrize("data", [b"not an image at all", b"", jpeg()[:40], b"%PDF-1.4 fake"])
def test_files_that_are_not_readable_photos_are_refused_with_a_plain_message(data):
    with pytest.raises(PhotoError, match="not a photo|too large|larger"):
        process_upload(data)


def test_an_oversized_file_is_refused(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(PhotoError, match="larger"):
        process_upload(jpeg())


def test_a_tiny_file_with_huge_dimensions_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 1000)
    with pytest.raises(PhotoError, match="too large|dimensions"):
        process_upload(png(size=(64, 64)))


def test_store_writes_a_random_jpeg_name_and_delete_removes_it():
    name = store(process_upload(jpeg()))
    assert photo_path(name).read_bytes()[:3] == b"\xff\xd8\xff" and name.endswith(".jpg") and len(name) == 36
    assert store(process_upload(jpeg())) != name
    delete_file(name)
    assert not photo_path(name).exists()
    delete_file(name)          # deleting twice, or deleting nothing, is harmless
    delete_file(None)


@pytest.mark.parametrize("name", ["../x.jpg", "a.jpg", "A" * 32 + ".jpg", "a" * 32 + ".png", "a" * 32 + ".jpg/../x", ""])
def test_only_names_this_app_generated_can_be_turned_into_a_path(name):
    with pytest.raises(ValueError):
        photo_path(name)


def test_the_preview_is_blurred_small_and_never_the_original():
    name = store(process_upload(jpeg(size=(600, 400))))
    original = photo_path(name).read_bytes()
    preview = blurred_jpeg(photo_path(name))
    assert preview != original and max(opened(preview).size) <= 480
    assert spread(preview) < spread(original) * 0.6
```
(`@` backslash note: the byte literals above use `\xff` escapes — write the file with the Write/Edit tool, not a shell heredoc.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_store.py`
Expected: FAIL (`app.body_photos` missing).

- [ ] **Step 3: Implement `app/body_photos.py`**

```python
"""Body photos: check, clean and store an upload; make the blurred preview. Pure file work, no web or database."""

import io
import re
import secrets
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from app import config

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()  # lets Pillow read iPhone HEIC photos
except ImportError:  # pragma: no cover
    pass

_NAME = re.compile(r"^[0-9a-f]{32}[.]jpg$")
_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}
_UNREADABLE = "That file is not a photo this app can read. Use a JPG, PNG, WEBP or iPhone HEIC picture."
_PREVIEW_SIDE = 480


class PhotoError(ValueError):
    """The upload cannot be used; the message is safe to show."""


def process_upload(data: bytes) -> bytes:
    """Clean JPEG bytes from an uploaded photo: orientation applied, metadata removed, no larger than
    config.PHOTO_MAX_SIDE on the longest side. Raises PhotoError for anything that is not a readable photo."""
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise PhotoError(f"Photo is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in _FORMATS:
            raise PhotoError(_UNREADABLE)
        if image.width * image.height > config.PHOTO_MAX_PIXELS:
            raise PhotoError("That photo's dimensions are too large.")
        image = ImageOps.exif_transpose(image)
        image.load()
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise PhotoError(_UNREADABLE) from None
    if image.mode in ("RGBA", "LA", "P"):
        flat = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((config.PHOTO_MAX_SIDE, config.PHOTO_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88, optimize=True)   # no exif= argument: nothing is carried over
    return out.getvalue()


def photo_path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError("not a stored photo name")
    return config.BODY_PHOTO_DIR / name


def store(jpeg: bytes) -> str:
    config.ensure_dirs()
    name = secrets.token_hex(16) + ".jpg"
    photo_path(name).write_bytes(jpeg)
    return name


def delete_file(name: str | None) -> None:
    if name:
        try:
            photo_path(name).unlink(missing_ok=True)
        except ValueError:
            pass


def blurred_jpeg(path: Path) -> bytes:
    """A small, heavily blurred copy: faces and bodies cannot be made out and the blur cannot be reversed."""
    with Image.open(path) as image:
        image = image.convert("RGB")
    image.thumbnail((_PREVIEW_SIDE, _PREVIEW_SIDE), Image.LANCZOS)
    image = image.filter(ImageFilter.GaussianBlur(radius=max(12, min(image.size) // 10)))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=60)
    return out.getvalue()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_store.py`
Expected: PASS (all). If the HEIC test cannot build a HEIC on this platform, mark only that test `skipif(from_pillow is None)` and note it.

- [ ] **Step 5: Commit**

```bash
git add app/body_photos.py tests/photo_helpers.py tests/test_body_photos_store.py
git commit -m "feat: body photo processing (metadata stripped, HEIC converted, shrunk) and server-side blur"
```

---

### Task 3: Unlock rules and the owner-only photo routes

**Files:**
- Create: `app/photo_access.py`, `app/routers/body_photos.py` (photo routes only for now), `tests/test_body_photos_access.py`
- Modify: `app/main.py`, `tests/photo_helpers.py`

**Interfaces:**
- Consumes: Task 1 models and config; Task 2 `process_upload`, `store`, `photo_path`, `blurred_jpeg`, `delete_file`; `app.routers.auth.check_code(user, code, now) -> str | None`; `app.auth.sessions` (`now_utc`, `is_locked`, `record_failure`, `clear_failures`).
- Produces in `app/photo_access.py`: `is_unlocked(row, now) -> bool`; `seconds_left(row, now) -> int`; `can_view_full(user, row, now) -> bool`; `unlock(db, user, row, code, now) -> str | None` (None means success, otherwise a message); `lock(db, row) -> None`.
- Produces routes: `GET /measurements/photos/{id}/preview`, `GET /measurements/photos/{id}/full`, `POST /measurements/photos/unlock` (form field `code`; JSON `{"ok": true, "seconds": 600}` or 422 `{"error": "..."}`), `POST /measurements/photos/lock` (JSON `{"ok": true}`).
- Test helpers added to `tests/photo_helpers.py`: `other_client(username)` (a context manager yielding a signed-in second user's `TestClient`), `enable_2fa(db, user_id) -> secret`, `code_now(secret)` (a valid code for the current step).

- [ ] **Step 1: Write the failing tests**

Append to `tests/photo_helpers.py`:
```python
import time
from contextlib import contextmanager

import pyotp
from fastapi.testclient import TestClient

from app.main import app
from app.models import BodyPhoto, User
from app.body_photos import process_upload, store


@contextmanager
def other_client(username="photoother"):
    """A second signed-in user in a separate browser (not the administrator)."""
    with TestClient(app, follow_redirects=False) as c:
        assert c.post("/notice", data={"understand": "1"}).status_code == 303
        r = c.post("/register", data={"username": username, "password": "Test1!", "confirm": "Test1!"})
        assert r.status_code in (200, 303), r.text
        yield c


def enable_2fa(db, user_id):
    secret = pyotp.random_base32()
    user = db.get(User, user_id)
    user.totp_secret, user.totp_enabled, user.totp_last_step = secret, True, None
    db.commit()
    return secret


def code_now(secret):
    return pyotp.TOTP(secret).at(time.time())


def make_photo(db, owner_id, taken_on=None, angle=None, note=None):
    from datetime import date
    photo = BodyPhoto(owner_id=owner_id, taken_on=taken_on or date(2026, 10, 6), angle=angle, note=note,
                      filename=store(process_upload(jpeg(size=(600, 400)))))
    db.add(photo)
    db.commit()
    return photo
```

Create `tests/test_body_photos_access.py`:
```python
from datetime import timedelta

import pytest

from app import config
from app.auth import sessions
from app.models import LoginSession, User
from app.photo_access import is_unlocked, seconds_left
from photo_helpers import code_now, enable_2fa, make_photo, other_client, spread


def set_setting(db, me, on=True):
    db.get(User, me).photo_2fa_required = on
    db.commit()


def login_row(db):
    return db.scalars(__import__("sqlalchemy").select(LoginSession).where(LoginSession.user_id.is_not(None))).first()


# ---------------------------------------------------------------- who may fetch a photo

def test_the_owner_gets_a_blurred_preview_with_no_store_headers(client, db, me):
    photo = make_photo(db, me)
    r = client.get(f"/measurements/photos/{photo.id}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "no-store" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    assert spread(r.content) < spread(open(config.BODY_PHOTO_DIR / photo.filename, "rb").read()) * 0.6


def test_with_the_setting_off_the_owner_gets_the_sharp_photo(client, db, me):
    photo = make_photo(db, me)
    r = client.get(f"/measurements/photos/{photo.id}/full")
    assert r.status_code == 200 and r.content == (config.BODY_PHOTO_DIR / photo.filename).read_bytes()
    assert "no-store" in r.headers["cache-control"]


def test_someone_elses_photo_is_404_for_every_route_even_for_the_administrator(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        photo = make_photo(db, other_id)
        for kind in ("preview", "full"):
            assert client.get(f"/measurements/photos/{photo.id}/{kind}").status_code == 404   # the administrator
    mine = make_photo(db, me)
    with other_client("photoother") as other:
        for kind in ("preview", "full"):
            assert other.get(f"/measurements/photos/{mine.id}/{kind}").status_code == 404


def test_a_missing_photo_and_a_photo_whose_file_is_gone_are_404(client, db, me):
    assert client.get("/measurements/photos/999999/preview").status_code == 404
    photo = make_photo(db, me)
    (config.BODY_PHOTO_DIR / photo.filename).unlink()
    assert client.get(f"/measurements/photos/{photo.id}/preview").status_code == 404


def test_a_person_the_owner_shares_with_still_cannot_fetch_photos(client, db, me):
    from app.models import Share, ShareCategory
    photo = make_photo(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        for category in ShareCategory:
            db.add(Share(owner_id=me, grantee_id=other_id, category=category))
        db.commit()
        assert other.get(f"/measurements/photos/{photo.id}/full").status_code == 404
        assert other.get(f"/measurements/photos/{photo.id}/preview").status_code == 404
        db.query(Share).delete()
        db.commit()


# ---------------------------------------------------------------- the 2FA lock

def test_with_the_setting_on_the_sharp_photo_is_refused_until_unlocked(client, db, me):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    photo = make_photo(db, me)
    r = client.get(f"/measurements/photos/{photo.id}/full")
    assert r.status_code == 403 and r.json() == {"locked": True}
    assert client.get(f"/measurements/photos/{photo.id}/preview").status_code == 200      # the blurred one is always fine
    ok = client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    assert ok.status_code == 200 and ok.json()["ok"] is True and 0 < ok.json()["seconds"] <= 600
    assert client.get(f"/measurements/photos/{photo.id}/full").status_code == 200


def test_the_unlock_ends_after_exactly_ten_minutes_and_is_not_extended_by_use(client, db, me, monkeypatch):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    photo = make_photo(db, me)
    start = sessions.now_utc()
    assert client.post("/measurements/photos/unlock", data={"code": code_now(secret)}).status_code == 200
    for minutes, expected in ((5, 200), (9, 200)):      # using the photos along the way does not extend the unlock
        monkeypatch.setattr(sessions, "now_utc", lambda m=minutes: start + timedelta(minutes=m))
        assert client.get(f"/measurements/photos/{photo.id}/full").status_code == expected
    monkeypatch.setattr(sessions, "now_utc", lambda: start + timedelta(minutes=10, seconds=5))
    assert client.get(f"/measurements/photos/{photo.id}/full").status_code == 403


def test_the_same_code_cannot_unlock_twice(client, db, me):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    code = code_now(secret)
    assert client.post("/measurements/photos/unlock", data={"code": code}).status_code == 200
    client.post("/measurements/photos/lock")
    again = client.post("/measurements/photos/unlock", data={"code": code})
    assert again.status_code == 422 and "already used" in again.json()["error"]


def test_a_wrong_code_is_refused_and_counts_toward_the_lockout(client, db, me, monkeypatch):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    for _ in range(config.LOCKOUT_ATTEMPTS):
        r = client.post("/measurements/photos/unlock", data={"code": "000000"})
        assert r.status_code == 422
    r = client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    assert r.status_code == 422 and "Too many attempts" in r.json()["error"]
    db.expire_all()
    user = db.get(User, me)
    user.locked_until, user.failed_attempts = None, 0
    db.commit()


def test_lock_now_ends_the_unlock_at_once(client, db, me):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    photo = make_photo(db, me)
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    assert client.post("/measurements/photos/lock").json() == {"ok": True}
    assert client.get(f"/measurements/photos/{photo.id}/full").status_code == 403


def test_the_unlock_belongs_to_one_browser_session_and_is_kept_in_the_database(client, db, me):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    db.expire_all()
    row = login_row(db)
    assert is_unlocked(row, sessions.now_utc()) and 0 < seconds_left(row, sessions.now_utc()) <= 600
    assert not is_unlocked(None, sessions.now_utc())


def test_unlock_without_account_2fa_is_refused(client, db, me):
    set_setting(db, me)
    r = client.post("/measurements/photos/unlock", data={"code": "123456"})
    assert r.status_code == 422 and "two-factor" in r.json()["error"].lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_access.py`
Expected: FAIL (`app.photo_access` missing).

- [ ] **Step 3: Implement**

`app/photo_access.py`:
```python
"""When the sharp version of a body photo may be shown: the 2FA setting and this browser session's unlock window."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import config
from app.auth import sessions
from app.models import LoginSession, User
from app.routers.auth import check_code


def is_unlocked(row: LoginSession | None, now: datetime) -> bool:
    return row is not None and row.photo_unlocked_until is not None and now < row.photo_unlocked_until


def seconds_left(row: LoginSession | None, now: datetime) -> int:
    return max(0, int((row.photo_unlocked_until - now).total_seconds())) if is_unlocked(row, now) else 0


def can_view_full(user: User, row: LoginSession | None, now: datetime) -> bool:
    return (not user.photo_2fa_required) or is_unlocked(row, now)


def _locked_message(user: User, now: datetime) -> str:
    minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
    return f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


def unlock(db: Session, user: User, row: LoginSession, code: str, now: datetime) -> str | None:
    """Open this session's photos for config.PHOTO_UNLOCK_MINUTES. None on success, otherwise the message to show.
    Uses the login code check, so a code just used to sign in cannot be reused, and wrong codes count toward lockout."""
    if sessions.is_locked(user, now):
        return _locked_message(user, now)
    if not user.totp_enabled:
        return "Turn on two-factor authentication for your account first."
    if problem := check_code(user, code, now):
        sessions.record_failure(user, now)
        db.commit()
        return _locked_message(user, now) if sessions.is_locked(user, now) else problem
    sessions.clear_failures(user)
    row.photo_unlocked_until = now + timedelta(minutes=config.PHOTO_UNLOCK_MINUTES)
    db.commit()
    return None


def lock(db: Session, row: LoginSession | None) -> None:
    if row is not None:
        row.photo_unlocked_until = None
        db.commit()
```

`app/routers/body_photos.py`:
```python
"""Body photos: owner-only blurred and sharp images, and the 2FA unlock."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app import body_photos, photo_access
from app.auth import sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import BodyPhoto, LoginSession, User

router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store"}


def _own_photo(session: Session, photo_id: int, uid: int) -> BodyPhoto:
    photo = session.get(BodyPhoto, photo_id)
    if photo is None or photo.owner_id != uid:
        raise HTTPException(404, "Not found")        # never 403: that would confirm someone else's photo exists
    return photo


def _login_row(session: Session, request: Request) -> LoginSession | None:
    return session.get(LoginSession, request.state.session_id) if request.state.session_id else None


def _file(photo: BodyPhoto):
    try:
        path = body_photos.photo_path(photo.filename)
    except ValueError:
        raise HTTPException(404, "Not found") from None
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return path


@router.get("/measurements/photos/{photo_id}/preview")
def preview(photo_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    path = _file(_own_photo(session, photo_id, uid))
    return Response(body_photos.blurred_jpeg(path), media_type="image/jpeg", headers=_NO_STORE)


@router.get("/measurements/photos/{photo_id}/full")
def full(photo_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo = _own_photo(session, photo_id, uid)
    path = _file(photo)
    if not photo_access.can_view_full(session.get(User, uid), _login_row(session, request), sessions.now_utc()):
        return JSONResponse({"locked": True}, status_code=403, headers=_NO_STORE)
    return Response(path.read_bytes(), media_type="image/jpeg", headers=_NO_STORE)


@router.post("/measurements/photos/unlock")
async def unlock(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    code = str((await request.form()).get("code", ""))
    row = _login_row(session, request)
    now = sessions.now_utc()
    problem = photo_access.unlock(session, session.get(User, uid), row, code, now)
    if problem:
        return JSONResponse({"error": problem}, status_code=422, headers=_NO_STORE)
    return JSONResponse({"ok": True, "seconds": photo_access.seconds_left(row, now)}, headers=_NO_STORE)


@router.post("/measurements/photos/lock")
def lock(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo_access.lock(session, _login_row(session, request))
    return JSONResponse({"ok": True}, headers=_NO_STORE)
```

`app/main.py`: add `body_photos` to the router import list and `app.include_router(body_photos.router)` after `measurements`. (Route order: `/measurements/photos/...` must not collide with `/measurements` handlers; they do not.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_access.py` then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/photo_access.py app/routers/body_photos.py app/main.py tests/photo_helpers.py tests/test_body_photos_access.py
git commit -m "feat: owner-only blurred and sharp body photo routes with a 10-minute 2FA unlock"
```

### Task 4: Upload, delete and the Body photos section

**Files:**
- Modify: `app/routers/body_photos.py`, `app/photo_access.py`, `app/routers/measurements.py`, `app/templates/measurements/index.html`, `app/static/css/app.css`
- Create: `app/templates/measurements/_body_photos.html`, `app/static/js/body-photos.js`, `tests/test_body_photos_page.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `photo_access.page_context(session, user, row, now) -> dict` with keys `photos` (newest first), `photo_2fa`, `photo_sharp` (the setting is on **and** this session is unlocked), `photo_seconds`, `photo_angle_labels`; routes `POST /measurements/photos` (multipart `file`, `taken_on`, `angle`, `note`; 303 to `/measurements?tab=measurements#body-photos` on success, 422 re-rendering the page with `photo_error` and `photo_form` on error) and `POST /measurements/photos/{id}/delete` (303, 404 for a photo that is not the caller's).

- [ ] **Step 1: Write the failing tests** (`tests/test_body_photos_page.py`)

```python
import html
import io
import re
from datetime import date

from PIL import Image

from app import config
from app.models import BodyPhoto, User
from photo_helpers import code_now, enable_2fa, jpeg, make_photo, other_client


def upload(client, data=None, **fields):
    data = data if data is not None else jpeg(exif=True)
    return client.post("/measurements/photos", data=fields,
                       files={"file": ("me.jpg", data, "image/jpeg")}, follow_redirects=False)


def page(client, tab="measurements"):
    return client.get(f"/measurements?tab={tab}").text


def test_an_uploaded_photo_is_saved_cleaned_and_labelled(client, db, me):
    r = upload(client, taken_on="2026-10-01", angle="front", note="Week one")
    assert r.status_code == 303 and r.headers["location"] == "/measurements?tab=measurements#body-photos"
    photo = db.query(BodyPhoto).one()
    assert (photo.owner_id, photo.taken_on, photo.angle, photo.note) == (me, date(2026, 10, 1), "front", "Week one")
    stored = Image.open(config.BODY_PHOTO_DIR / photo.filename)
    assert stored.format == "JPEG" and len(stored.getexif()) == 0


def test_the_date_defaults_to_today_and_label_and_note_are_optional(client, db, me):
    upload(client)
    photo = db.query(BodyPhoto).one()
    assert photo.taken_on == date.today() and photo.angle is None and photo.note is None


def test_a_bad_file_is_refused_with_a_message_and_nothing_is_saved(client, db, me):
    r = upload(client, data=b"this is not a photo")
    assert r.status_code == 422 and "not a photo" in html.unescape(r.text)
    assert db.query(BodyPhoto).count() == 0 and not list(config.BODY_PHOTO_DIR.glob("*"))


def test_other_bad_input_is_refused(client, db, me):
    for fields in ({"taken_on": "not-a-date"}, {"taken_on": "2999-01-01"}, {"angle": "top"}, {"note": "x" * 201}):
        assert upload(client, **fields).status_code == 422, fields
    assert client.post("/measurements/photos", data={}, files={"x": ("", b"")}).status_code == 422
    assert db.query(BodyPhoto).count() == 0 and not list(config.BODY_PHOTO_DIR.glob("*"))


def test_deleting_a_photo_removes_the_row_and_the_file(client, db, me):
    photo = make_photo(db, me)
    path = config.BODY_PHOTO_DIR / photo.filename
    assert client.post(f"/measurements/photos/{photo.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(BodyPhoto).count() == 0 and not path.exists()


def test_deleting_someone_elses_photo_is_404_and_changes_nothing(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        photo = make_photo(db, other_id)
        assert client.post(f"/measurements/photos/{photo.id}/delete").status_code == 404
        db.expire_all()
        assert db.query(BodyPhoto).count() == 1 and (config.BODY_PHOTO_DIR / photo.filename).exists()


def test_the_section_shows_blurred_previews_newest_first_with_a_reveal_hint(client, db, me):
    old = make_photo(db, me, taken_on=date(2026, 9, 1), angle="side", note="Older")
    new = make_photo(db, me, taken_on=date(2026, 10, 1), angle="front")
    text = page(client)
    assert 'id="body-photos"' in text and "Add body photo" in text
    assert text.index(f"/photos/{new.id}/preview") < text.index(f"/photos/{old.id}/preview")
    assert "/full" not in text and "Click to reveal" in text and "Older" in text and "Front" in text
    assert 'data-mode="click"' in text


def test_with_the_setting_on_and_locked_tiles_ask_for_the_code(client, db, me):
    enable_2fa(db, me)
    db.get(User, me).photo_2fa_required = True
    db.commit()
    make_photo(db, me)
    text = page(client)
    assert 'data-mode="code"' in text and "Enter code to view" in text and "/full" not in text and "Lock now" not in text


def test_after_an_unlock_tiles_are_sharp_with_a_countdown_and_a_lock_button(client, db, me):
    secret = enable_2fa(db, me)
    db.get(User, me).photo_2fa_required = True
    db.commit()
    photo = make_photo(db, me)
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    text = page(client)
    assert f"/photos/{photo.id}/full" in text and "Lock now" in text and "data-photo-countdown" in text


def test_a_users_page_never_lists_another_users_photos(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        theirs = make_photo(db, other_id)
        assert f"/photos/{theirs.id}/" not in page(client)
        assert f"/photos/{theirs.id}/" in other.get("/measurements?tab=measurements").text


def test_photos_appear_on_no_other_page(client, db, me):
    photo = make_photo(db, me)
    for path in ("/dashboard", "/measurements?tab=journal", "/measurements?tab=labs", "/calendar", "/today"):
        assert f"/photos/{photo.id}/" not in client.get(path).text


def test_the_journal_tab_links_to_the_upload_form_which_opens_ready(client, db, me):
    assert "/measurements?tab=measurements&amp;add_photo=1#body-photos" in page(client, "journal") or \
        "/measurements?tab=measurements&add_photo=1#body-photos" in page(client, "journal")
    opened = client.get("/measurements?tab=measurements&add_photo=1").text
    assert re.search(r'<dialog id="photo-upload-dialog"[^>]*data-open-on-load', opened)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_page.py`
Expected: FAIL (routes and section missing).

- [ ] **Step 3: Implement**

`app/photo_access.py` — add imports `from sqlalchemy import select`, `from app.models import BODY_PHOTO_ANGLES, BodyPhoto` and:
```python
ANGLE_LABELS = {"front": "Front", "side": "Side", "back": "Back", "other": "Other"}


def page_context(session: Session, user: User, row: LoginSession | None, now: datetime) -> dict:
    photos = session.scalars(select(BodyPhoto).where(BodyPhoto.owner_id == user.id)
                             .order_by(BodyPhoto.taken_on.desc(), BodyPhoto.id.desc())).all()
    sharp = bool(user.photo_2fa_required) and is_unlocked(row, now)
    return {"photos": photos, "photo_2fa": bool(user.photo_2fa_required), "photo_sharp": sharp,
            "photo_seconds": seconds_left(row, now) if sharp else 0, "photo_angle_labels": ANGLE_LABELS}
```

`app/routers/measurements.py`, inside `_render`, after the `elif tab == "labs":` block and before `if extra:`:
```python
    if tab == "measurements":
        login_row = session.get(LoginSession, request.state.session_id) if request.state.session_id else None
        context.update(photo_access.page_context(session, me_user, login_row, sessions.now_utc()))
        context["open_photo_dialog"] = request.query_params.get("add_photo") == "1"
```
with imports `from app import photo_access`, `from app.auth import sessions`, and `LoginSession` added to the models import (`me_user` is the signed-in `User` already used in `_render`; if it can be `None` guard with `if me_user`).

`app/routers/body_photos.py` — append:
```python
from datetime import date, timedelta

from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile

from app import config
from app.models import BODY_PHOTO_ANGLES

_GALLERY = "/measurements?tab=measurements#body-photos"


@router.post("/measurements/photos")
async def upload(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    from app.routers import measurements    # imported here: measurements imports photo_access, not this module
    form = await request.form()
    values = {k: str(form.get(k, "")).strip() for k in ("taken_on", "angle", "note")}
    errors: dict[str, str] = {}
    taken_on = date.today()
    if values["taken_on"]:
        try:
            taken_on = date.fromisoformat(values["taken_on"])
        except ValueError:
            errors["taken_on"] = "Enter the photo date as a valid date."
        else:
            if taken_on > date.today() + timedelta(days=1):
                errors["taken_on"] = "A photo's date cannot be in the future."
    if values["angle"] and values["angle"] not in BODY_PHOTO_ANGLES:
        errors["angle"] = "Choose Front, Side, Back or Other."
    if len(values["note"]) > 200:
        errors["note"] = "The note is 200 characters at most."
    upload_file = form.get("file")
    data = b""
    if not isinstance(upload_file, UploadFile) or not upload_file.filename:
        errors["file"] = "Choose a photo to upload."
    else:
        data = await upload_file.read(config.MAX_UPLOAD_BYTES + 1)
    jpeg = None
    if not errors:
        try:
            jpeg = body_photos.process_upload(data)
        except body_photos.PhotoError as exc:
            errors["file"] = str(exc)
    if errors:
        return measurements._render(request, session, uid, tab="measurements", status_code=422,
                                    extra={"photo_error": errors, "photo_form": values, "open_photo_dialog": True})
    name = body_photos.store(jpeg)
    session.add(BodyPhoto(owner_id=uid, taken_on=taken_on, angle=values["angle"] or None,
                          note=values["note"] or None, filename=name))
    try:
        session.commit()
    except Exception:
        session.rollback()
        body_photos.delete_file(name)
        raise
    return RedirectResponse(_GALLERY, status_code=303)


@router.post("/measurements/photos/{photo_id}/delete")
def delete(photo_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    photo = _own_photo(session, photo_id, uid)
    name = photo.filename
    session.delete(photo)
    session.commit()
    body_photos.delete_file(name)
    return RedirectResponse(_GALLERY, status_code=303)
```

`app/templates/measurements/_body_photos.html` — the section, the upload dialog and the code dialog, plus the script tag:
```html
<section aria-labelledby="body-photos-heading" id="body-photos" class="body-photos"
         data-mode="{{ 'code' if photo_2fa else 'click' }}" data-sharp="{{ '1' if photo_sharp else '0' }}"
         data-seconds="{{ photo_seconds }}">
  <div class="photos-head">
    <h2 id="body-photos-heading" class="section-title">Body photos</h2>
    <button type="button" class="btn btn-primary" data-photo-add>Add body photo</button>
  </div>
  <p class="muted small">Private to you: never shared with anyone. Photos stay blurred until you reveal them{{ ' with your authenticator code' if photo_2fa }}.</p>
  {% if photo_sharp %}
  <div class="note photo-unlocked" role="status">Unlocked: <strong data-photo-countdown>{{ photo_seconds }}</strong> seconds left.
    <button type="button" class="btn btn-ghost" data-photo-lock>Lock now</button></div>
  {% endif %}
  {% if photos %}
  <ul class="photo-grid">
    {% for p in photos %}
    <li class="photo-tile">
      <button type="button" class="photo-open" data-photo-id="{{ p.id }}" aria-label="Show the photo from {{ p.taken_on | shortdate }}">
        <img src="/measurements/photos/{{ p.id }}/{{ 'full' if photo_sharp else 'preview' }}" alt="Body photo from {{ p.taken_on | shortdate }}" loading="lazy">
        {% if not photo_sharp %}<span class="photo-hint">{{ 'Enter code to view' if photo_2fa else 'Click to reveal' }}</span>{% endif %}
      </button>
      <div class="photo-meta small"><strong>{{ p.taken_on | shortdate }}</strong>{% if p.angle %} &middot; {{ photo_angle_labels[p.angle] }}{% endif %}{% if p.note %}<br>{{ p.note }}{% endif %}</div>
      <form method="post" action="/measurements/photos/{{ p.id }}/delete" data-photo-delete><button type="submit" class="btn btn-ghost small">Delete</button></form>
    </li>
    {% endfor %}
  </ul>
  {% else %}<p class="empty muted">No body photos yet.</p>{% endif %}
</section>

<dialog id="photo-upload-dialog" class="dialog"{{ ' data-open-on-load' if open_photo_dialog or photo_error }}>
  <form method="post" action="/measurements/photos" enctype="multipart/form-data" class="settings-form">
    <div class="dialog-head"><h3>Add body photo</h3><button type="button" class="btn btn-ghost" data-photo-close aria-label="Close">&times;</button></div>
    <div class="form-section">
      <label class="field"><span>Photo</span><input type="file" name="file" accept="image/*,.heic,.heif" required></label>
      {% if photo_error and photo_error.file %}<p class="error small">{{ photo_error.file }}</p>{% endif %}
      <label class="field"><span>Date</span><input type="date" name="taken_on" value="{{ (photo_form or {}).get('taken_on') or today }}" max="{{ today }}"></label>
      {% if photo_error and photo_error.taken_on %}<p class="error small">{{ photo_error.taken_on }}</p>{% endif %}
      <label class="field"><span>Angle (optional)</span>
        <select name="angle"><option value="">None</option>
          {% for key, label in photo_angle_labels.items() %}<option value="{{ key }}" {{ 'selected' if (photo_form or {}).get('angle') == key }}>{{ label }}</option>{% endfor %}
        </select></label>
      {% if photo_error and photo_error.angle %}<p class="error small">{{ photo_error.angle }}</p>{% endif %}
      <label class="field"><span>Note (optional)</span><input type="text" name="note" maxlength="200" value="{{ (photo_form or {}).get('note') or '' }}"></label>
      {% if photo_error and photo_error.note %}<p class="error small">{{ photo_error.note }}</p>{% endif %}
      <p class="muted small">Location and camera details are removed when the photo is saved. It stays blurred until you reveal it and is never shared.</p>
    </div>
    <div class="dialog-foot"><button type="submit" class="btn btn-primary">Save photo</button></div>
  </form>
</dialog>

<dialog id="photo-unlock-dialog" class="dialog">
  <form method="post" action="/measurements/photos/unlock" class="settings-form" data-photo-unlock-form>
    <div class="dialog-head"><h3>Show body photos</h3><button type="button" class="btn btn-ghost" data-photo-close aria-label="Close">&times;</button></div>
    <div class="form-section">
      <label class="field"><span>6-digit code from your authenticator</span>
        <input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required></label>
      <p class="error small" data-photo-unlock-error hidden></p>
      <p class="muted small">Photos stay visible for 10 minutes after a correct code.</p>
    </div>
    <div class="dialog-foot"><button type="submit" class="btn btn-primary">Show photos</button></div>
  </form>
</dialog>
<script src="{{ static_url('js/body-photos.js') }}" defer></script>
```

`app/templates/measurements/index.html`: insert `{% include "measurements/_body_photos.html" %}` on its own line immediately before the line `{% elif active_tab == 'macros' %}`; in the journal tab, after the `Log Workout` button line add
`  <a class="btn" href="/measurements?tab=measurements&add_photo=1#body-photos">Add body photo</a>`.

`app/static/js/body-photos.js`:
```js
// Body photos: click to reveal, optional authenticator-code unlock with a 10-minute countdown. The server enforces all of
// it; this only drives the page.
(() => {
  const root = document.getElementById("body-photos");
  if (!root) return;
  const mode = root.dataset.mode;                 // "click" (no 2FA setting) or "code" (setting on)
  const sharp = root.dataset.sharp === "1";       // the server rendered the photos sharp (unlocked)
  const upload = document.getElementById("photo-upload-dialog");
  const unlock = document.getElementById("photo-unlock-dialog");

  document.querySelectorAll("[data-photo-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  root.querySelector("[data-photo-add]").addEventListener("click", () => upload.showModal());
  if (upload.hasAttribute("data-open-on-load")) upload.showModal();

  root.querySelectorAll("form[data-photo-delete]").forEach((form) => form.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this photo? This cannot be undone.")) event.preventDefault();
  }));

  root.querySelectorAll(".photo-open").forEach((tile) => tile.addEventListener("click", () => {
    if (sharp) return;
    if (mode === "code") { unlock.showModal(); unlock.querySelector("input[name=code]").focus(); return; }
    const img = tile.querySelector("img");
    const hint = tile.querySelector(".photo-hint");
    const id = tile.dataset.photoId;
    const showing = img.dataset.revealed === "1";
    img.src = "/measurements/photos/" + id + (showing ? "/preview" : "/full");
    img.dataset.revealed = showing ? "0" : "1";
    if (hint) hint.hidden = !showing;
  }));

  const form = unlock.querySelector("[data-photo-unlock-form]");
  const error = unlock.querySelector("[data-photo-unlock-error]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    const response = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
    if (response.ok) { window.location.reload(); return; }
    const body = await response.json().catch(() => ({}));
    error.textContent = body.error || "Something went wrong. Try again.";
    error.hidden = false;
  });

  const lockButton = root.querySelector("[data-photo-lock]");
  if (lockButton) lockButton.addEventListener("click", async () => {
    await fetch("/measurements/photos/lock", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  });

  const countdown = root.querySelector("[data-photo-countdown]");
  if (countdown) {
    let left = Number(root.dataset.seconds);
    const tick = setInterval(() => {
      left -= 1;
      if (left <= 0) { clearInterval(tick); window.location.reload(); return; }
      const m = Math.floor(left / 60), s = String(left % 60).padStart(2, "0");
      countdown.textContent = m + ":" + s;
    }, 1000);
  }
})();
```
(Note the template prints the initial seconds as a plain number; the script replaces it with m:ss on the first tick. Change the server text to read "...left" only after the colon format is shown: use `Unlocked: <strong data-photo-countdown>{{ photo_seconds // 60 }}:{{ '%02d' | format(photo_seconds % 60) }}</strong> left.` in the template instead of the "seconds left" wording.)

`app/static/css/app.css` — append:
```css
/* ---------- body photos ---------- */
.photos-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.photo-grid { list-style: none; margin: 12px 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.photo-tile { display: flex; flex-direction: column; gap: 6px; }
.photo-open { position: relative; padding: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--bg); cursor: pointer; aspect-ratio: 3 / 4; }
.photo-open img { width: 100%; height: 100%; object-fit: cover; display: block; }
.photo-hint { position: absolute; inset: auto 0 0 0; padding: 6px 8px; font-size: 0.8rem; text-align: center; background: rgb(0 0 0 / 55%); color: #fff; }
.photo-unlocked { display: flex; align-items: center; gap: 10px; }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_body_photos_page.py` then the full suite.
Expected: PASS.

- [ ] **Step 5: Browser check, then commit**

Render the section with a throwaway test database and the app's real CSS/JS the way the price-popup was checked (load the partial into the in-app browser pane): confirm blurred tiles, click-to-reveal swaps to the sharp URL, the code dialog opens, and the countdown renders. Then:
```bash
git add app/photo_access.py app/routers/body_photos.py app/routers/measurements.py app/templates/measurements/ app/static/js/body-photos.js app/static/css/app.css tests/test_body_photos_page.py
git commit -m "feat: Body photos section with upload, blurred tiles, click or code reveal, and delete"
```

---

### Task 5: The Body Recomp Photo 2FA setting and its 2FA coupling

**Files:**
- Modify: `app/routers/body_photos.py`, `app/templates/settings/settings.html`, `app/routers/auth.py`, `app/routers/settings.py`, `app/users.py`
- Create: `tests/test_body_photos_setting.py`

**Interfaces:**
- Consumes: Tasks 1-4; `check_code`, `sessions.record_failure/clear_failures/is_locked`, `photo_access.lock`.
- Produces: `POST /settings/photo-2fa` (form `action` = `enable` or `disable`, `code` for disable) redirecting to `/settings?photo2fa=<state>#photo-2fa` with state one of `on`, `off`, `need2fa`, `badcode`, `locked`.

Ruling (recorded here so it reaches the owner): the spec's one-time "your photo setting was switched off" notice is replaced by a permanent line on the Settings card ("This turns off by itself if two-factor authentication is removed from your account"). It avoids a new column for a notice and says the same thing.

- [ ] **Step 1: Write the failing tests** (`tests/test_body_photos_setting.py`)

```python
from app.models import User
from photo_helpers import code_now, enable_2fa, other_client


def user(db, me):
    db.expire_all()
    return db.get(User, me)


def test_turning_the_setting_on_needs_account_2fa(client, db, me):
    r = client.post("/settings/photo-2fa", data={"action": "enable"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?photo2fa=need2fa#photo-2fa"
    assert user(db, me).photo_2fa_required is False


def test_turning_the_setting_on_with_account_2fa_works(client, db, me):
    enable_2fa(db, me)
    r = client.post("/settings/photo-2fa", data={"action": "enable"}, follow_redirects=False)
    assert r.headers["location"] == "/settings?photo2fa=on#photo-2fa" and user(db, me).photo_2fa_required is True


def test_turning_it_off_needs_a_valid_code(client, db, me):
    secret = enable_2fa(db, me)
    client.post("/settings/photo-2fa", data={"action": "enable"})
    bad = client.post("/settings/photo-2fa", data={"action": "disable", "code": "000000"}, follow_redirects=False)
    assert bad.headers["location"] == "/settings?photo2fa=badcode#photo-2fa" and user(db, me).photo_2fa_required is True
    ok = client.post("/settings/photo-2fa", data={"action": "disable", "code": code_now(secret)}, follow_redirects=False)
    assert ok.headers["location"] == "/settings?photo2fa=off#photo-2fa" and user(db, me).photo_2fa_required is False
    db.get(User, me).failed_attempts = 0
    db.commit()


def test_turning_it_off_ends_this_sessions_unlock(client, db, me):
    from app.models import LoginSession
    secret = enable_2fa(db, me)
    client.post("/settings/photo-2fa", data={"action": "enable"})
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    from datetime import timedelta
    from app.auth import sessions
    import pyotp, time
    client.post("/settings/photo-2fa", data={"action": "disable", "code": pyotp.TOTP(secret).at(time.time() + 30)})
    db.expire_all()
    assert all(r.photo_unlocked_until is None for r in db.query(LoginSession))


def test_account_2fa_cannot_be_disabled_while_the_photo_setting_is_on(client, db, me):
    secret = enable_2fa(db, me)
    client.post("/settings/photo-2fa", data={"action": "enable"})
    r = client.post("/account/2fa", data={"action": "disable", "code": code_now(secret)})
    assert r.status_code == 422 and "Body Recomp Photo 2FA" in r.text
    assert user(db, me).totp_enabled is True


def test_an_administrator_reset_of_2fa_switches_the_photo_setting_off(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        enable_2fa(db, other_id)
        db.get(User, other_id).photo_2fa_required = True
        db.commit()
        client.post(f"/settings/admin/users/{other_id}/remove-2fa")
        db.expire_all()
        u = db.get(User, other_id)
        assert u.totp_enabled is False and u.photo_2fa_required is False


def test_the_command_line_2fa_reset_switches_it_off_too(client, db, me):
    from app import users as users_cli
    with other_client("photocli"):
        uid = db.query(User).filter_by(username_key="photocli").one().id
        enable_2fa(db, uid)
        db.get(User, uid).photo_2fa_required = True
        db.commit()
        assert users_cli.main(["reset-2fa", "photocli"]) == 0
        db.expire_all()
        assert db.get(User, uid).photo_2fa_required is False


def test_the_settings_page_shows_the_card_and_each_message(client, db, me):
    text = client.get("/settings").text
    assert "Body Recomp Photo 2FA" in text and "turns off by itself" in text
    for state, words in (("on", "turned on"), ("off", "turned off"), ("need2fa", "two-factor authentication first"),
                         ("badcode", "didn")):
        assert words in client.get(f"/settings?photo2fa={state}").text
```

- [ ] **Step 2: Run to verify it fails**, expecting failures for the missing route and card.

- [ ] **Step 3: Implement**

`app/routers/body_photos.py` — append:
```python
from app.routers.auth import check_code


@router.post("/settings/photo-2fa")
async def photo_2fa(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    user = session.get(User, uid)
    form = await request.form()
    now = sessions.now_utc()

    def back(state: str):
        return RedirectResponse(f"/settings?photo2fa={state}#photo-2fa", status_code=303)

    if form.get("action") == "enable":
        if not user.totp_enabled:
            return back("need2fa")
        user.photo_2fa_required = True
        session.commit()
        return back("on")
    if form.get("action") != "disable":
        raise HTTPException(404, "Not found")
    if sessions.is_locked(user, now):
        return back("locked")
    if check_code(user, str(form.get("code", "")), now):
        sessions.record_failure(user, now)
        session.commit()
        return back("locked" if sessions.is_locked(user, now) else "badcode")
    sessions.clear_failures(user)
    user.photo_2fa_required = False
    photo_access.lock(session, _login_row(session, request))     # commits, ending this session's unlock too
    session.commit()
    return back("off")
```

`app/routers/auth.py`, in `twofa_change`, right after `form = await _form(request)` add:
```python
    if form.get("action") == "disable" and user.photo_2fa_required:
        return _twofa_page(request, user, "Turn off Body Recomp Photo 2FA in Settings before turning off two-factor authentication.", 422)
```

`app/routers/settings.py` `admin_remove_2fa`: change the assignment line to also set `target.photo_2fa_required = False` (keep the one commit). `app/users.py` reset branch: add `user.photo_2fa_required = False` after the existing `user.totp_enabled, ... = False, None, None` line.

`app/templates/settings/settings.html` — insert after the closing `</div>` of the "Two-factor authentication" block (the `<div class="settings-form">` that ends right before the Timezone `<form>`):
```html
      <div class="settings-form" id="photo-2fa">
        <h3 class="small muted">Body Recomp Photo 2FA</h3>
        {% set photo_msg = {'on': 'Body Recomp Photo 2FA is turned on. Photos stay blurred until you enter an authenticator code.',
                            'off': 'Body Recomp Photo 2FA is turned off.',
                            'need2fa': 'Turn on two-factor authentication for your account first, then come back here.',
                            'badcode': "That code didn't work. Enter the 6-digit code your app shows now.",
                            'locked': 'Too many attempts. Wait a few minutes and try again.'}.get(request.query_params.get('photo2fa')) %}
        {% if photo_msg %}<p class="note" role="status">{{ photo_msg }}</p>{% endif %}
        <p>Status: <span class="status {{ 'status-active' if me.photo_2fa_required else '' }}">{{ 'On' if me.photo_2fa_required else 'Off' }}</span></p>
        <p class="small muted">When on, your body photos stay blurred until you enter a code from your authenticator, and then stay clear for 10 minutes. It uses the same authenticator as your login. This turns off by itself if two-factor authentication is removed from your account.</p>
        {% if me.photo_2fa_required %}
        <form method="post" action="/settings/photo-2fa" class="settings-form">
          <input type="hidden" name="action" value="disable">
          <label class="field"><span>Code to turn it off</span><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required></label>
          <button type="submit" class="btn">Turn off</button>
        </form>
        {% else %}
        <form method="post" action="/settings/photo-2fa"><input type="hidden" name="action" value="enable"><button type="submit" class="btn btn-primary">Turn on</button></form>
        {% endif %}
      </div>
```

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add app/routers/body_photos.py app/routers/auth.py app/routers/settings.py app/users.py app/templates/settings/settings.html tests/test_body_photos_setting.py
git commit -m "feat: Body Recomp Photo 2FA setting; account 2FA cannot be removed from under it"
```

---

### Task 6: Backup, export, share exclusion and cleanup

**Files:**
- Modify: `app/backup/sections.py`, `app/routers/settings.py` (admin delete user)
- Create: `tests/test_body_photos_backup.py`
- Modify (if a guard test lists sections): existing backup tests that assert the exact person-section list.

**Interfaces:**
- Consumes: Tasks 1-2; `app.backup.export.build_archive(db, kind=, uid=, creator=, keys=)`, `app.backup.archive.read_archive`, `app.backup.load.load(db, archive, uid=, username_key=, is_admin=, plan=)` with `load.ADD` / `load.REPLACE`.
- Produces: backup section key `body_photos` (person level, not shareable); `FILE_DIRS["body_photos"] = "BODY_PHOTO_DIR"`.

- [ ] **Step 1: Write the failing tests** (`tests/test_body_photos_backup.py`)

```python
import pytest

from app import config
from app.backup import load as loader
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import BodyPhoto, User
from photo_helpers import make_photo, other_client


def export(db, me, keys, kind="backup"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def test_a_personal_backup_carries_photo_rows_and_files(client, db, me):
    photo = make_photo(db, me, angle="front", note="Start")
    archive = export(db, me, ["body_photos"])
    assert f"files/body_photos/{photo.filename}" in archive.names()
    assert any(name.endswith("body_photos.json") or "body_photos" in name for name in archive.names() if name.startswith(("sections/", "persons/")))


def test_a_share_file_can_never_contain_photos(client, db, me):
    make_photo(db, me)
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        export(db, me, ["body_photos"], kind="share")
    archive = export(db, me, ["inventory"], kind="share")
    assert not [n for n in archive.names() if "body_photos" in n]


def test_loading_photos_adds_them_with_their_files_and_replace_swaps_them(client, db, me):
    keep = make_photo(db, me, note="one")
    archive = export(db, me, ["body_photos"])
    run(db, me, archive, {"body_photos": loader.ADD})
    db.expire_all()
    assert db.query(BodyPhoto).filter_by(owner_id=me).count() == 2          # photos have no natural key: Add appends
    run(db, me, archive, {"body_photos": loader.REPLACE})
    db.expire_all()
    rows = db.query(BodyPhoto).filter_by(owner_id=me).all()
    assert len(rows) == 1 and all((config.BODY_PHOTO_DIR / r.filename).exists() for r in rows)


def test_photos_load_for_the_loader_never_for_the_name_in_the_file(client, db, me):
    make_photo(db, me)
    archive = export(db, me, ["body_photos"])
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        loader.load(db, archive, uid=other_id, username_key="photoother", is_admin=False, plan={"body_photos": loader.ADD})
        db.expire_all()
        assert db.query(BodyPhoto).filter_by(owner_id=other_id).count() == 1


def test_deleting_an_account_removes_its_photo_files(client, db, me):
    with other_client("photogone") as other:
        gone = db.query(User).filter_by(username_key="photogone").one()
        photo = make_photo(db, gone.id)
        path = config.BODY_PHOTO_DIR / photo.filename
        r = client.post(f"/settings/admin/users/{gone.id}/delete", data={"username": gone.username}, follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert db.query(BodyPhoto).filter_by(owner_id=gone.id).count() == 0 and not path.exists()


def test_the_backup_page_offers_body_photos_for_personal_backups_only(client, db, me):
    text = client.get("/backup").text
    assert "Body photos" in text
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL (section unknown); the existing registry guard test may also fail until Step 3 is done.

- [ ] **Step 3: Implement**

`app/backup/sections.py`:
- `FILE_DIRS` gains `"body_photos": "BODY_PHOTO_DIR"`.
- Add after the `measurements` section:
```python
    Section("body_photos", "Body photos", PERSON, (
        Tbl("body_photos", "owner_id = :uid", file=("filename", "body_photos")),),
        help="Your progress photos with their image files. Never offered in a Share file."),
```
- `LOAD_ORDER`: insert `"body_photos"` after `"measurements"`.

`app/routers/settings.py` `admin_delete_user`: before `session.flush()` add
```python
    photo_filenames = []
    for photo in session.scalars(select(BodyPhoto).where(BodyPhoto.owner_id == target.id)):
        photo_filenames.append(photo.filename)
        session.delete(photo)
```
and after the existing lab-report unlink loop add `for filename in photo_filenames: body_photos.delete_file(filename)` with imports `from app import body_photos` and `BodyPhoto` in the models import.

Run the full suite; fix any existing backup/page test that asserts the exact list or count of person sections (update the expected list to include `body_photos`; do not weaken the assertion).

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add app/backup/sections.py app/routers/settings.py tests/test_body_photos_backup.py
git commit -m "feat: body photos in personal backups and exports, never in share files; files removed with the account"
```

---

### Task 7: Roadmap note and final verification

**Files:** Modify `docs/ROADMAP.md`.

- [ ] **Step 1: Add a roadmap entry** under the Measurements notes (match nearby style): "Body photos (built 2026-10-06) (spec: `docs/superpowers/specs/2026-10-06-body-photos-design.md`; plan: `docs/superpowers/plans/2026-10-06-body-photos.md`) - private gallery on Weight & Measurements, blurred on the server until revealed; optional Body Recomp Photo 2FA unlocks them for 10 minutes using the account's authenticator; never shared; carried in the owner's own backup and Export; metadata stripped, HEIC converted, large photos shrunk. Not built: editing a photo's date or label, comparison views, a separate photo-only authenticator."
- [ ] **Step 2: Full suite and scans**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (expect green) and `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` (expect no hits).

- [ ] **Step 3: Whole-branch review** (the executing-plans skill's final review), then fix pass, then commit and push per the owner's standing preference:
```bash
git add docs/ROADMAP.md
git commit -m "docs: roadmap note for body photos"
git push origin main
```

