import time

import pyotp

from app import users as users_cli
from app.models import LoginSession, User
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


def test_wrong_codes_here_count_toward_the_same_lockout_as_login(client, db, me):
    enable_2fa(db, me)
    client.post("/settings/photo-2fa", data={"action": "enable"})
    for _ in range(5):
        client.post("/settings/photo-2fa", data={"action": "disable", "code": "000000"})
    locked = client.post("/settings/photo-2fa", data={"action": "disable", "code": "000000"}, follow_redirects=False)
    assert locked.headers["location"] == "/settings?photo2fa=locked#photo-2fa" and user(db, me).photo_2fa_required is True


def test_turning_it_off_ends_this_sessions_unlock(client, db, me):
    secret = enable_2fa(db, me)
    client.post("/settings/photo-2fa", data={"action": "enable"})
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    db.expire_all()
    assert any(r.photo_unlocked_until is not None for r in db.query(LoginSession))
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
    for state, words in (("on", "turned on"), ("off", "turned off"), ("need2fa", "two-factor authentication for your account first"),
                         ("badcode", "didn")):
        assert words in client.get(f"/settings?photo2fa={state}").text
