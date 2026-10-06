from datetime import timedelta

from sqlalchemy import select

from app import config
from app.auth import sessions
from app.models import LoginSession, Share, ShareCategory, User
from app.photo_access import is_unlocked, seconds_left
from photo_helpers import code_now, enable_2fa, make_photo, other_client, spread


def set_setting(db, me, on=True):
    db.get(User, me).photo_2fa_required = on
    db.commit()


def login_row(db):
    return db.scalars(select(LoginSession).where(LoginSession.user_id.is_not(None))).first()


def reset_lockout(db, me):
    db.expire_all()
    user = db.get(User, me)
    user.locked_until, user.failed_attempts = None, 0
    db.commit()


# ---------------------------------------------------------------- who may fetch a photo

def test_the_owner_gets_a_blurred_preview_with_no_store_headers(client, db, me):
    photo = make_photo(db, me)
    r = client.get(f"/measurements/photos/{photo.id}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "no-store" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    assert spread(r.content) < spread((config.BODY_PHOTO_DIR / photo.filename).read_bytes()) * 0.6


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
    for minutes in (5, 9):      # using the photos along the way does not extend the unlock
        monkeypatch.setattr(sessions, "now_utc", lambda m=minutes: start + timedelta(minutes=m))
        assert client.get(f"/measurements/photos/{photo.id}/full").status_code == 200
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


def test_a_wrong_code_is_refused_and_counts_toward_the_lockout(client, db, me):
    secret = enable_2fa(db, me)
    set_setting(db, me)
    for _ in range(config.LOCKOUT_ATTEMPTS):
        assert client.post("/measurements/photos/unlock", data={"code": "000000"}).status_code == 422
    r = client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    assert r.status_code == 422 and "Too many attempts" in r.json()["error"]
    reset_lockout(db, me)


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
