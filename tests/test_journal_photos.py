"""Photos on a journal day: a motivating picture, a scale report, anything worth keeping with that day. Private to their owner, cleaned of location data."""

import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import clock, config
from app.main import app
from app.models import JournalEntry, JournalPhoto

TODAY = clock.today()


def jpeg(color=(200, 30, 30), size=(64, 48), exif=False) -> bytes:
    image = Image.new("RGB", size, color)
    out = io.BytesIO()
    if exif:
        exif_data = Image.Exif()
        exif_data[0x010F] = "Acme Camera"                      # make
        exif_data[0x8825] = {1: "N", 2: (40.0, 0.0, 0.0)}       # a GPS block
        image.save(out, "JPEG", exif=exif_data)
    else:
        image.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    for p in db.query(JournalPhoto).all():
        (config.JOURNAL_PHOTO_DIR / p.filename).unlink(missing_ok=True)
    db.query(JournalPhoto).delete()
    db.query(JournalEntry).delete()
    db.commit()


def upload(client, files=None, day=TODAY, caption="", **extra):
    files = files if files is not None else [("file", ("pic.jpg", io.BytesIO(jpeg()), "image/jpeg"))]
    return client.post("/journal/photos", data={"entry_date": day.isoformat(), "caption": caption, **extra}, files=files, follow_redirects=False)


def test_a_photo_is_added_to_a_day_creating_the_entry_when_there_is_none(client, db, me):
    r = upload(client, caption="Scale report")
    assert r.status_code == 303
    entry = db.scalar(select(JournalEntry).where(JournalEntry.owner_id == me, JournalEntry.entry_date == TODAY))
    (photo,) = entry.photos
    assert photo.caption == "Scale report" and photo.filename.endswith(".jpg")
    assert (config.JOURNAL_PHOTO_DIR / photo.filename).is_file()


def test_several_photos_can_be_added_at_once(client, db, me):
    files = [("file", (f"p{i}.jpg", io.BytesIO(jpeg((i * 40, 10, 10))), "image/jpeg")) for i in range(3)]
    assert upload(client, files=files).status_code == 303
    assert db.query(JournalPhoto).count() == 3


def test_the_stored_copy_is_a_clean_jpeg_with_no_location_or_camera_data(client, db, me):
    upload(client, files=[("file", ("pic.jpg", io.BytesIO(jpeg(exif=True)), "image/jpeg"))])
    photo = db.scalar(select(JournalPhoto))
    with Image.open(config.JOURNAL_PHOTO_DIR / photo.filename) as stored:
        assert stored.format == "JPEG" and not stored.getexif()


def test_something_that_is_not_a_picture_is_refused_and_nothing_is_stored(client, db, me):
    r = upload(client, files=[("file", ("notes.txt", io.BytesIO(b"hello"), "text/plain"))])
    assert r.status_code == 422 and "not a photo" in r.text
    assert db.query(JournalPhoto).count() == 0


def test_no_file_chosen_is_refused(client, db, me):
    r = client.post("/journal/photos", data={"entry_date": TODAY.isoformat()}, follow_redirects=False)
    assert r.status_code == 422 and "Choose a photo" in r.text


def test_a_day_in_the_future_is_refused(client, db, me):
    r = upload(client, day=TODAY + timedelta(days=3))
    assert r.status_code == 422
    assert db.query(JournalPhoto).count() == 0


def test_an_earlier_day_works(client, db, me):
    day = TODAY - timedelta(days=9)
    assert upload(client, day=day).status_code == 303
    assert db.scalar(select(JournalEntry).where(JournalEntry.entry_date == day)).photos


def test_a_long_caption_is_refused(client, db, me):
    assert upload(client, caption="x" * 300).status_code == 422


def test_a_day_holds_at_most_twelve_photos(client, db, me):
    files = [("file", (f"p{i}.jpg", io.BytesIO(jpeg((i * 15, 10, 10))), "image/jpeg")) for i in range(13)]
    r = upload(client, files=files)
    assert r.status_code == 422 and "12" in r.text
    assert db.query(JournalPhoto).count() == 0


def test_the_journal_page_shows_the_photo_with_a_thumbnail_and_a_delete(client, db, me):
    upload(client, caption="Look at this")
    photo = db.scalar(select(JournalPhoto))
    page = client.get("/measurements?tab=journal").text
    assert f'/journal/photos/{photo.id}/thumb' in page and f'/journal/photos/{photo.id}"' in page
    assert f'action="/journal/photos/{photo.id}/delete"' in page and "Look at this" in page


def test_the_owner_can_open_the_full_photo_and_the_thumbnail(client, db, me):
    upload(client)
    photo = db.scalar(select(JournalPhoto))
    full = client.get(f"/journal/photos/{photo.id}")
    thumb = client.get(f"/journal/photos/{photo.id}/thumb")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg" and "no-store" in full.headers["cache-control"]
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"


def test_nobody_else_can_open_it_and_a_signed_out_browser_is_sent_away(client, db, me):
    upload(client)
    photo = db.scalar(select(JournalPhoto))
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "jphotoother", "password": "Other1!", "confirm": "Other1!"})
    assert other.get(f"/journal/photos/{photo.id}").status_code == 404
    assert other.get(f"/journal/photos/{photo.id}/thumb").status_code == 404
    assert other.post(f"/journal/photos/{photo.id}/delete").status_code == 404
    stranger = TestClient(app, follow_redirects=False)
    assert stranger.get(f"/journal/photos/{photo.id}").status_code in (303, 401)


def test_deleting_a_photo_removes_the_row_and_the_file(client, db, me):
    upload(client)
    photo = db.scalar(select(JournalPhoto))
    path = config.JOURNAL_PHOTO_DIR / photo.filename
    assert client.post(f"/journal/photos/{photo.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(JournalPhoto).count() == 0 and not path.exists()


def test_deleting_the_journal_entry_deletes_its_photo_files(client, db, me):
    upload(client)
    photo = db.scalar(select(JournalPhoto))
    path = config.JOURNAL_PHOTO_DIR / photo.filename
    client.post(f"/journal/entries/{TODAY.isoformat()}/delete", follow_redirects=False)
    db.expire_all()
    assert db.query(JournalPhoto).count() == 0 and not path.exists()


def test_a_shared_journal_never_shows_photos_to_the_person_it_is_shared_with(client, db, me):
    from app.routers import journal
    upload(client)
    entry = db.scalar(select(JournalEntry))
    view = journal._entry_view(entry, [], [], owner_name="Someone")
    assert view["photos"] == []


def test_a_refused_upload_reopens_the_popup_with_the_reason_inside_it_and_keeps_the_day_and_caption(client, db, me):
    day = TODAY - timedelta(days=2)
    r = upload(client, day=day, caption="Kept", files=[("file", ("notes.txt", io.BytesIO(b"hello"), "text/plain"))])
    dialog = r.text.split('id="journal-photo-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert r.status_code == 422 and "data-open-on-load" in r.text.split('id="journal-photo-dialog"', 1)[1][:60]
    assert "Nothing was uploaded" in dialog and "not a photo" in dialog
    assert f'value="{day.isoformat()}"' in dialog and 'value="Kept"' in dialog
