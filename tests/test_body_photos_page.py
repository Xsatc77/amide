import html
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
    journal = html.unescape(page(client, "journal"))
    assert "/measurements?tab=measurements&add_photo=1#body-photos" in journal
    opened = client.get("/measurements?tab=measurements&add_photo=1").text
    assert re.search(r'<dialog id="photo-upload-dialog"[^>]*data-open-on-load', opened)
