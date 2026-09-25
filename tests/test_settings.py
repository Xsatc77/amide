import html

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.auth import passwords
from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, LoginSession, Protocol, User, Vendor

PW = "Test1!"


def text(r) -> str:
    # Extracts and unescapes body text regardless of status: callers check status separately, and
    # several tests inspect the body of a 422 error re-render, not just a 200 page.
    return html.unescape(r.text)


def _current(uid: int) -> User:
    # Looks up by id (stable across a username change), not by username string -- several tests here
    # rename the account, and a string-keyed lookup would silently return None/a stale row afterward.
    with SessionLocal() as s:
        return s.get(User, uid)


def test_settings_page_has_user_and_integrations_sections(client):
    # Note: `client` (conftest's "Tester") is the first-ever account, so it IS the admin (see
    # conftest.py's client fixture docstring) -- this test only checks the sections every user gets;
    # Admin-section visibility is tested properly in Task 4 with an explicitly non-admin account.
    t = text(client.get("/settings"))
    assert 'id="user"' in t and 'id="integrations"' in t
    assert "Apple Health" in t and "Hume" in t and "Coming soon" in t


def test_menu_has_one_settings_link_not_separate_2fa_and_backup(client):
    t = client.get("/protocols").text
    assert 'href="/settings"' in t
    assert 'href="/account/2fa"' not in t and 'href="/backup"' not in t


def test_change_username_requires_current_password(client, db, me):
    original = _current(me).username
    r = client.post("/settings/username", data={"username": "NewName", "current_password": "wrong"})
    assert r.status_code == 422 and "incorrect" in text(r).lower()
    assert _current(me).username == original

    r = client.post("/settings/username", data={"username": "NewName", "current_password": PW},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _current(me).username == "NewName"
    # restore for other tests sharing this session-scoped client/user
    r = client.post("/settings/username", data={"username": original, "current_password": PW},
                    follow_redirects=False)
    assert r.status_code == 303 and _current(me).username == original


def test_change_username_rejects_duplicate_case_insensitive(client, db):
    other_key = "dupuser"
    with SessionLocal() as s:
        if not s.scalar(select(User).where(User.username_key == other_key)):
            s.add(User(username="DupUser", username_key=other_key, password_hash=passwords.hash_password("x")))
            s.commit()
    r = client.post("/settings/username", data={"username": "dupuser", "current_password": PW})
    assert r.status_code == 422 and "taken" in text(r).lower()


def test_change_password_ends_other_sessions_not_this_one(client, db):
    # A genuinely separate session for the same account needs its own login (a new LoginSession row is
    # minted on every sign-in -- copying `client`'s cookies would just be a second handle on the SAME
    # session/row, which can't be independently kept and ended).
    from fastapi.testclient import TestClient
    from app.main import app

    other_device = TestClient(app, follow_redirects=False)
    other_device.post("/notice", data={"understand": "1"})
    other_device.post("/login", data={"username": "tester", "password": PW})
    assert other_device.get("/protocols").status_code == 200  # proves it starts out signed in

    r = client.post("/settings/password", data={"current_password": PW, "new_password": "Newer1!",
                                                 "confirm": "Newer1!"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/protocols").status_code == 200  # this session: still signed in
    assert other_device.get("/protocols").status_code in (302, 303, 307)  # the other device: signed out

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username_key == "tester"))
        assert passwords.verify_password(u.password_hash, "Newer1!")
    client.post("/settings/password", data={"current_password": "Newer1!", "new_password": PW, "confirm": PW})


def test_change_password_wrong_current_rejected(client, db):
    r = client.post("/settings/password", data={"current_password": "wrong", "new_password": "Whatever1!",
                                                 "confirm": "Whatever1!"})
    assert r.status_code == 422 and "incorrect" in text(r).lower()


def test_change_password_error_does_not_show_on_username_form(client, db):
    t = text(client.post("/settings/password", data={"current_password": "wrong", "new_password": "Whatever1!",
                                                       "confirm": "Whatever1!"}))
    # The username form's own current-password field must stay clean -- the two forms share a field
    # name but must not share an error key, or a password-form error bleeds onto the username form.
    import re
    username_form = re.search(r'action="/settings/username".*?</form>', t, re.S).group(0)
    assert "incorrect" not in username_form.lower()


def test_timezone_saves_and_blank_clears_it(client, db, me):
    client.post("/settings/timezone", data={"mode": "manual", "timezone": "America/Chicago"})
    assert _current(me).timezone == "America/Chicago"
    client.post("/settings/timezone", data={"mode": "system", "timezone": ""})
    assert _current(me).timezone is None


def test_timezone_rejects_unknown_zone(client, db, me):
    r = client.post("/settings/timezone", data={"mode": "manual", "timezone": "Nowhere/Fake"})
    assert r.status_code == 422
    assert _current(me).timezone is None


def test_email_saves_and_validates(client, db, me):
    r = client.post("/settings/email", data={"email": "not-an-email"})
    assert r.status_code == 422
    client.post("/settings/email", data={"email": "me@example.com"})
    assert _current(me).email == "me@example.com"
    client.post("/settings/email", data={"email": ""})
    assert _current(me).email is None


def test_colorway_is_a_dropdown_with_all_options(client):
    t = text(client.get("/settings"))
    assert '<select name="colorway"' in t
    for label in ("Auto", "Light", "Dark", "Tequila Sunrise", "Fireworks", "Solarin", "The Bricks",
                 "Retro", "Greensleeves", "High Contrast"):
        assert label in t


def test_colorway_dropdown_preselects_the_current_value(client, db, me):
    client.post("/settings/display", data={"colorway": "solarin"})
    t = text(client.get("/settings"))
    assert '<option value="solarin" selected>' in t
    client.post("/settings/display", data={"colorway": ""})  # reset for later tests


def test_colorway_saves_and_sets_data_theme(client, db, me):
    client.post("/settings/display", data={"colorway": "tequila_sunrise"})
    assert _current(me).colorway.value == "tequila_sunrise"
    t = client.get("/protocols").text
    assert 'data-theme="tequila_sunrise"' in t

    client.post("/settings/display", data={"colorway": ""})
    assert _current(me).colorway is None
    t = client.get("/protocols").text
    assert "data-theme=" not in t  # Auto: no attribute, prefers-color-scheme rules as before


def test_colorway_save_returns_to_the_display_section(client, db):
    r = client.post("/settings/display", data={"colorway": "retro"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings#display"
    client.post("/settings/display", data={"colorway": ""})  # reset for later tests


def test_colorway_rejects_unknown_value(client, db, me):
    r = client.post("/settings/display", data={"colorway": "not-a-real-one"})
    assert r.status_code == 422
    assert _current(me).colorway is None


# `client` (conftest's "Tester") is the first account ever created in the test database, so it IS the
# admin -- see conftest.py's client fixture docstring. Admin-positive tests below use `client` directly.
# Negative-path tests need a definitely non-admin account instead:


@pytest.fixture(scope="module")
def nonadmin():
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    c.post("/register", data={"username": "NotAdmin", "password": "NotAdm1n!", "confirm": "NotAdm1n!"})
    return c


def test_admin_section_hidden_from_non_admin(nonadmin):
    t = text(nonadmin.get("/settings"))
    assert 'id="admin"' not in t


def test_admin_section_shown_to_admin(client):
    t = text(client.get("/settings"))
    assert 'id="admin"' in t and "Tester" in t


def test_admin_table_shows_last_login_in_admins_own_timezone(client, db, me):
    client.post("/settings/timezone", data={"mode": "manual", "timezone": "America/Chicago"})
    with SessionLocal() as s:
        s.get(User, me).last_login_at = __import__("datetime").datetime(2026, 6, 1, 12, 0, tzinfo=__import__("datetime").timezone.utc)
        s.commit()
    t = text(client.get("/settings"))
    # 12:00 UTC on 2026-06-01 is 07:00 in America/Chicago (CDT, UTC-5) -- the UTC time must not appear as-is.
    assert "2026-06-01 07:00" in t
    assert "2026-06-01 12:00 UTC" not in t


def test_admin_routes_404_for_non_admin(nonadmin, db):
    with SessionLocal() as s:
        target_id = s.scalar(select(User.id).where(User.username_key == "notadmin"))
    for path, data in [
        ("/settings/admin/users/new", {"username": "X", "password": "X1!aaaaa", "confirm": "X1!aaaaa"}),
        (f"/settings/admin/users/{target_id}/reset-password", {"password": "X1!aaaaa", "confirm": "X1!aaaaa"}),
        (f"/settings/admin/users/{target_id}/remove-2fa", {}),
        (f"/settings/admin/users/{target_id}/delete", {"username": "whatever"}),
    ]:
        assert nonadmin.post(path, data=data).status_code == 404, path


def test_admin_can_add_reset_and_remove_2fa(client, db):
    r = client.post("/settings/admin/users/new",
                    data={"username": "AdminMade", "password": "Made1!aaa", "confirm": "Made1!aaa"},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        made = s.scalar(select(User).where(User.username_key == "adminmade"))
        assert made is not None and not made.is_admin
        made.totp_enabled, made.totp_secret = True, "JBSWY3DPEHPK3PXP"
        s.commit()
        made_id = made.id

    client.post(f"/settings/admin/users/{made_id}/reset-password",
               data={"password": "Reset1!aaa", "confirm": "Reset1!aaa"})
    with SessionLocal() as s:
        made = s.get(User, made_id)
        assert passwords.verify_password(made.password_hash, "Reset1!aaa")

    client.post(f"/settings/admin/users/{made_id}/remove-2fa")
    with SessionLocal() as s:
        assert not s.get(User, made_id).totp_enabled


def test_admin_cannot_reset_own_password_or_remove_own_2fa_via_admin_routes(client, db):
    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        original_hash = s.get(User, me_id).password_hash

    r = client.post(f"/settings/admin/users/{me_id}/reset-password",
                    data={"password": "Sneaky1!aaa", "confirm": "Sneaky1!aaa"})
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(User, me_id).password_hash == original_hash

    r = client.post(f"/settings/admin/users/{me_id}/remove-2fa")
    assert r.status_code == 422


def test_admin_cannot_delete_self(client, db):
    with SessionLocal() as s:
        my_id = s.scalar(select(User.id).where(User.username_key == "tester"))
    r = client.post(f"/settings/admin/users/{my_id}/delete", data={"username": "Tester"})
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(User, my_id) is not None


def test_delete_user_requires_exact_username_match_server_side(client, db):
    with SessionLocal() as s:
        s.add(User(username="ToDelete", username_key="todelete", password_hash=passwords.hash_password("x")))
        s.commit()
        target_id = s.scalar(select(User.id).where(User.username_key == "todelete"))

    r = client.post(f"/settings/admin/users/{target_id}/delete", data={"username": "WrongName"})
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(User, target_id) is not None

    r = client.post(f"/settings/admin/users/{target_id}/delete", data={"username": "ToDelete"},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(User, target_id) is None


def test_delete_user_cascades_inventory_and_protocols_but_keeps_vendor(client, db):
    from datetime import date

    owner = TestClient(app, follow_redirects=False)
    owner.post("/notice", data={"understand": "1"})
    owner.post("/register", data={"username": "OwnsStuff", "password": "Owns1!aaa", "confirm": "Owns1!aaa"})
    owner.post("/inventory", data={"name": "Their vial", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "vendor": "Their Vendor"},
              files={"coa": ("c.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png")})

    with SessionLocal() as s:
        owner_id = s.scalar(select(User.id).where(User.username_key == "ownsstuff"))
        s.add(Protocol(name="Their protocol", start_date=date(2026, 9, 1), owner_id=owner_id))
        s.commit()

        inv = s.scalar(select(InventoryItem).where(InventoryItem.owner_id == owner_id))
        coa_filename = inv.coa_filename
        vendor_id = inv.vendor_id
        assert coa_filename is not None
        assert (config.COA_DIR / coa_filename).exists()
        assert s.query(Protocol).filter_by(owner_id=owner_id).count() == 1
        assert s.get(Vendor, vendor_id).created_by_id == owner_id

    r = client.post(f"/settings/admin/users/{owner_id}/delete", data={"username": "OwnsStuff"},
                    follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        assert s.get(User, owner_id) is None
        assert s.query(InventoryItem).filter_by(owner_id=owner_id).count() == 0
        assert s.query(Protocol).filter_by(owner_id=owner_id).count() == 0
        # Vendor row survives -- it's a shared resource, not owned data -- but its creator is cleared.
        vendor = s.get(Vendor, vendor_id)
        assert vendor is not None and vendor.name == "Their Vendor" and vendor.created_by_id is None
    assert not (config.COA_DIR / coa_filename).exists()


def test_delete_user_cleans_up_shares_both_directions(client, db):
    from app.models import Share, ShareCategory

    a = TestClient(app, follow_redirects=False)
    a.post("/notice", data={"understand": "1"})
    a.post("/register", data={"username": "ShareA", "password": "ShareA1!aa", "confirm": "ShareA1!aa"})

    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        a_id = s.scalar(select(User.id).where(User.username_key == "sharea"))
        s.add(Share(owner_id=a_id, grantee_id=me_id, category=ShareCategory.INVENTORY))
        s.add(Share(owner_id=me_id, grantee_id=a_id, category=ShareCategory.PERSONAL_DATA))
        s.commit()
        assert s.query(Share).filter(
            (Share.owner_id == a_id) | (Share.grantee_id == a_id)).count() == 2

    client.post(f"/settings/admin/users/{a_id}/delete", data={"username": "ShareA"})

    with SessionLocal() as s:
        assert s.query(Share).filter(
            (Share.owner_id == a_id) | (Share.grantee_id == a_id)).count() == 0


def test_sharing_section_lists_other_users(client, db):
    t = text(client.get("/settings"))
    assert 'id="sharing"' in t


def test_sharing_toggle_grants_and_revokes(client, db):
    from app.models import Share, ShareCategory

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "SharePartner", "password": "Share1!aa", "confirm": "Share1!aa"})
    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        partner_id = s.scalar(select(User.id).where(User.username_key == "sharepartner"))

    r = client.post(f"/settings/sharing/{partner_id}/inventory", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#sharing"
    with SessionLocal() as s:
        assert s.query(Share).filter_by(owner_id=me_id, grantee_id=partner_id,
                                        category=ShareCategory.INVENTORY).count() == 1
    t = text(client.get("/settings"))
    assert "Sharing" in t  # button now shows the granted state somewhere on the page

    client.post(f"/settings/sharing/{partner_id}/inventory")  # toggle again -- revoke
    with SessionLocal() as s:
        assert s.query(Share).filter_by(owner_id=me_id, grantee_id=partner_id,
                                        category=ShareCategory.INVENTORY).count() == 0


def test_grant_is_one_directional(client, db):
    from app.models import Share, ShareCategory

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "OneWay", "password": "OneWay1!", "confirm": "OneWay1!"})
    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        other_id = s.scalar(select(User.id).where(User.username_key == "oneway"))

    client.post(f"/settings/sharing/{other_id}/inventory")
    with SessionLocal() as s:
        assert s.query(Share).filter_by(owner_id=me_id, grantee_id=other_id).count() == 1
        assert s.query(Share).filter_by(owner_id=other_id, grantee_id=me_id).count() == 0
    client.post(f"/settings/sharing/{other_id}/inventory")  # revoke, keep suite state clean


def test_sharing_route_404_for_self_or_unknown_user(client, db, me):
    assert client.post(f"/settings/sharing/{me}/inventory").status_code == 404
    assert client.post("/settings/sharing/999999/inventory").status_code == 404


def test_sharing_route_422_for_bad_category(client, db):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "BadCat", "password": "BadCat1!", "confirm": "BadCat1!"})
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "badcat"))
    assert client.post(f"/settings/sharing/{other_id}/not-a-real-category").status_code == 422
