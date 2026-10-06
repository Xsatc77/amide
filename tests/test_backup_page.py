import html
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import config
from app.backup import pending
from app.backup import restore as backup_restore
from app.backup.archive import read_archive
from app.backup.container import unseal
from app.main import app
from backup_helpers import clean_files, person_counts, seed_world, wipe_person

PASS = "correct horse battery"


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


@pytest.fixture
def other_client(db):
    """A second, non-administrator person in their own browser; removed afterwards."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "vaultother", "password": "Other1!", "confirm": "Other1!"})
    yield other
    db.execute(text("DELETE FROM users WHERE username_key = 'vaultother'"))
    db.commit()


def text_of(response):
    return html.unescape(response.text)


def create(client, **fields):
    data = {"kind": "backup", "scope": "person", "passphrase": PASS, "confirm": PASS, **fields}
    return client.post("/backup/create", data=data)


def archive_from(response):
    return read_archive(unseal(response.content, PASS), max_bytes=50_000_000)


def open_file(client, blob, passphrase=PASS):
    return client.post("/backup/open", files={"file": ("x.amidebackup", blob, "application/octet-stream")},
                       data={"passphrase": passphrase})


def token_of(response):
    return re.search(r'name="token" value="([^"]+)"', response.text).group(1)


def test_the_page_has_three_tabs_and_the_older_formats(client):
    for tab in ("backup", "export", "restore"):
        page = text_of(client.get("/backup", params={"tab": tab}))
        assert "Back up" in page and "Export / Share" in page and "Restore / Import" in page
        assert "/backup/export.json" in page and "/backup/export/inventory.csv" in page
    assert 'action="/backup/create"' in client.get("/backup?tab=backup").text
    assert 'action="/backup/open"' in client.get("/backup?tab=restore").text
    assert client.get("/backup?tab=nonsense").status_code == 200


def test_an_administrator_sees_the_whole_installation_option_and_shared_sections(client, other_client):
    admin = text_of(client.get("/backup?tab=backup"))
    assert "Whole installation" in admin and 'value="vendors"' in admin and 'value="library"' in admin
    person = text_of(other_client.get("/backup?tab=backup"))
    assert "Whole installation" not in person and 'value="library"' not in person and 'value="vendors"' not in person
    assert 'value="inventory"' in person and 'value="journal"' in person
    share = text_of(other_client.get("/backup?tab=export"))
    assert "Share non-medical data" in share and 'value="vendors"' not in share and 'value="inventory"' in share


def test_a_backup_downloads_as_a_new_dated_encrypted_file_of_the_chosen_sections(client, db, me, world):
    r = create(client, section=["inventory", "journal"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert re.fullmatch(r'attachment; filename="amide-backup-\d{4}-\d{2}-\d{2}-\d{6}\.amidebackup"',
                        r.headers["content-disposition"])
    assert r.headers["cache-control"] == "no-store" and b"Zorvex" not in r.content
    archive = archive_from(r)
    assert set(archive.manifest["sections"]) == {"inventory", "journal"} and archive.manifest["creator"] == "Tester"


def test_a_share_download_holds_only_shareable_sections(client, db, me, world):
    r = create(client, kind="share", scope="person", section=["inventory", "protocols", "workouts"])
    archive = archive_from(r)
    assert archive.manifest["kind"] == "share" and "dose_logs" not in archive.json("sections/protocols.json")["tables"]
    bad = create(client, kind="share", section=["journal"])
    assert bad.status_code == 422 and "cannot be put in a share file" in text_of(bad)


@pytest.mark.parametrize("fields,message", [
    ({"confirm": "different pass"}, "do not match"),
    ({"passphrase": "short", "confirm": "short"}, "at least 8"),
    ({"section": []}, "at least one"),
    ({"section": ["nonsense"]}, "Unknown section"),
    ({"kind": "mystery", "section": ["journal"]}, "Unknown kind"),
])
def test_bad_requests_show_a_message_and_download_nothing(client, fields, message):
    r = create(client, **{"section": ["journal"], **fields})
    assert r.status_code == 422 and message in text_of(r) and r.headers["content-type"].startswith("text/html")


def test_only_an_administrator_can_back_up_the_whole_installation_or_shared_sections(client, other_client):
    r = create(other_client, scope="installation", section=[])
    assert r.status_code == 422 and "Only an administrator" in text_of(r)
    r = create(other_client, section=["vendors"])
    assert r.status_code == 422 and "Only an administrator" in text_of(r)
    assert create(other_client, section=["journal"]).status_code == 200
    whole = create(client, scope="installation", section=[])
    assert archive_from(whole).manifest["level"] == "installation"


def test_opening_a_file_shows_what_is_inside_and_changes_nothing(client, db, me, world):
    blob = create(client, section=["inventory", "journal", "profile"]).content
    before = person_counts(db, me)
    page = open_file(client, blob)
    assert page.status_code == 200
    text = text_of(page)
    assert "What is in this file" in text and "Inventory" in text and "Journal" in text and "Made by" in text
    assert "Restore everything" not in text and 'name="token"' in text
    assert "Replace deletes your 5 current rows" in text and "Replace deletes your 3 current rows" in text
    assert person_counts(db, me) == before


def test_a_wrong_passphrase_a_damaged_file_and_a_foreign_file_are_refused(client, db, me, world):
    blob = create(client, section=["journal"]).content
    assert "Wrong passphrase" in text_of(open_file(client, blob, "not the passphrase"))
    assert open_file(client, blob, "not the passphrase").status_code == 422
    assert "damaged" in text_of(open_file(client, blob[:-5]))
    assert "not an Amide backup" in text_of(open_file(client, b"just some text that is not a backup file at all"))


def test_a_file_over_the_size_limit_is_refused(client, db, me, world, monkeypatch):
    blob = create(client, section=["journal"]).content
    monkeypatch.setattr(config, "MAX_BACKUP_BYTES", 100)
    assert "larger than the allowed size" in text_of(open_file(client, blob))


def test_loading_sections_through_the_page_reports_what_happened(client, db, me, world):
    blob = create(client, section=["inventory", "journal"]).content
    db.execute(text("UPDATE inventory_items SET count = 99 WHERE owner_id = :u"), {"u": me})
    db.commit()
    token = token_of(open_file(client, blob))
    r = client.post("/backup/load", data={"token": token, "load": ["inventory", "journal"],
                                          "mode-inventory": "replace", "mode-journal": "add"})
    page = text_of(r)
    assert r.status_code == 200 and "Loaded" in page and "Inventory" in page and "Journal" in page
    assert db.execute(text("SELECT count FROM inventory_items WHERE owner_id = :u"), {"u": me}).scalar() == 5
    again = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert again.status_code == 422 and "expired" in text_of(again)             # a step can be used once


def test_a_bad_choice_keeps_the_step_open_with_a_message(client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "merge"})
    assert r.status_code == 422 and "can only be loaded as" in text_of(r) and token in r.text
    none = client.post("/backup/load", data={"token": token})
    assert none.status_code == 422 and "at least one section" in text_of(none)


def test_a_step_belongs_to_the_account_that_opened_the_file(client, other_client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = other_client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert r.status_code == 422 and "expired" in text_of(r)
    assert client.post("/backup/load", data={"token": "../../etc/passwd", "load": ["journal"]}).status_code == 422


def test_a_step_expires(client, db, me, world, monkeypatch):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    monkeypatch.setattr(pending, "TTL_SECONDS", -1)
    r = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert r.status_code == 422 and "expired" in text_of(r)


def test_a_person_can_only_load_their_own_sections_of_a_whole_installation_file(client, other_client, db, me, world):
    whole = create(client, scope="installation", section=[]).content
    page = text_of(open_file(other_client, whole))
    assert "Restore everything" not in page
    token = token_of(open_file(other_client, whole))
    r = other_client.post("/backup/load", data={"token": token, "load": ["vendors"], "mode-vendors": "add"})
    assert r.status_code == 422 and "administrator" in text_of(r).lower()


def test_restore_everything_is_for_administrators_and_needs_the_typed_word(client, other_client, db, me, world):
    whole = create(client, scope="installation", section=[]).content
    page = open_file(client, whole)
    assert "Restore everything" in text_of(page) and "RESTORE" in page.text
    token = token_of(page)
    assert other_client.post("/backup/restore", data={"token": token}).status_code == 404
    base = {"token": token, "safety_passphrase": PASS, "safety_confirm": PASS}
    assert "Type RESTORE" in text_of(client.post("/backup/restore", data={**base, "confirm": "restore it"}))
    assert "do not match" in text_of(client.post("/backup/restore", data={**base, "confirm": "RESTORE", "safety_confirm": "x" * 9}))
    short = client.post("/backup/restore", data={**base, "confirm": "RESTORE", "safety_passphrase": "short", "safety_confirm": "short"})
    assert short.status_code == 422 and "at least 8" in text_of(short)


def test_restore_everything_runs_the_restore_and_signs_the_person_out(client, db, me, world, monkeypatch):
    blob = create(client, scope="installation", section=[]).content
    own = TestClient(app, follow_redirects=False)       # its own browser: the sign-out below must not end the shared one
    own.post("/notice", data={"understand": "1"})
    assert own.post("/login", data={"username": "Tester", "password": "Test1!"}).status_code in (200, 303)
    token = token_of(open_file(own, blob))
    seen = {}

    def fake(session, archive, *, uid, creator, safety_passphrase):
        seen.update(uid=uid, creator=creator, passphrase=safety_passphrase)
        return backup_restore.RestoreReport(rows={"users": 1, "vendors": 2}, files=3, safety_copy="amide-safety-test.amidebackup")

    monkeypatch.setattr(backup_restore, "restore_installation", fake)
    r = own.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS,
                                           "safety_confirm": PASS})
    assert r.status_code == 200 and "Everything was restored" in text_of(r) and "amide-safety-test" in r.text
    assert seen == {"uid": me, "creator": "Tester", "passphrase": PASS}
    assert 'amide_session=""' in r.headers["set-cookie"] or "amide_session=;" in r.headers["set-cookie"]
    again = own.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS,
                                               "safety_confirm": PASS})
    assert "expired" in text_of(again) or again.status_code in (302, 303, 401)   # the sign-out already took effect


def test_a_person_level_file_cannot_restore_everything(client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = client.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS, "safety_confirm": PASS})
    assert r.status_code == 422 and "whole-installation" in text_of(r)


def test_the_settings_page_links_to_the_new_page(client):
    page = text_of(client.get("/settings"))
    assert 'href="/backup"' in page and "Back up, export, share and restore" in page


def test_a_backup_over_the_size_limit_is_refused_with_a_message(client, db, me, world, monkeypatch):
    monkeypatch.setattr(config, "MAX_BACKUP_BYTES", 100)
    r = create(client, section=["inventory"])
    assert r.status_code == 422 and "larger than the allowed size" in text_of(r)
