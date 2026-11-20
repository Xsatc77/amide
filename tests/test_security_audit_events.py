"""N-13 follow-up: tokens, shares, backup loads and restores, and vendor changes leave a line in the security log.
Never a secret in a line."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import passwords
from app.backup import restore as backup_restore
from app.db import SessionLocal
from app.main import app
from app.models import IngestToken, SecurityEvent, User, Vendor
from tests.test_backup_page import PASS, create, open_file, token_of, world  # noqa: F401  (world is a fixture)


def events(name):
    with SessionLocal() as s:
        return list(s.scalars(select(SecurityEvent).where(SecurityEvent.event == name).order_by(SecurityEvent.id)))


def _second_user(name="AuditGrantee"):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == name.lower()))
        if uid is None:
            u = User(username=name, username_key=name.lower(), password_hash=passwords.hash_password("Sec0nd!pw"))
            s.add(u)
            s.commit()
            uid = u.id
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    assert c.post("/login", data={"username": name, "password": "Sec0nd!pw"}).status_code == 303
    return c, uid


def test_watcher_token_create_and_revoke_are_logged_without_the_secret(client):
    before = (len(events("token_created")), len(events("token_revoked")))
    r = client.post("/settings/ingest/tokens", data={"label": "audit watcher"})
    assert r.status_code == 200
    created = events("token_created")
    assert len(created) == before[0] + 1 and created[-1].detail.startswith("watcher token")
    with SessionLocal() as s:
        token = s.scalar(select(IngestToken).where(IngestToken.label == "audit watcher").order_by(IngestToken.id.desc()))
    assert "amw_" not in created[-1].detail.replace(token.prefix, "") and "token_hash" not in created[-1].detail
    client.post(f"/settings/ingest/tokens/{token.id}/revoke")
    assert len(events("token_revoked")) == before[1] + 1


def test_health_token_create_and_revoke_are_logged(client, me):
    n = len(events("token_created"))
    client.post("/settings/apple-health/token", data={"label": "audit phone"})
    created = events("token_created")
    assert len(created) == n + 1 and created[-1].user_id == me and created[-1].detail.startswith("health token")
    with SessionLocal() as s:
        token = s.scalar(select(IngestToken).where(IngestToken.label == "audit phone").order_by(IngestToken.id.desc()))
    m = len(events("token_revoked"))
    client.post(f"/settings/apple-health/token/{token.id}/revoke")
    assert len(events("token_revoked")) == m + 1


def test_share_granted_removed_and_declined_are_logged(client, me):
    other, other_id = _second_user()
    g, r_, d = (len(events(e)) for e in ("share_granted", "share_removed", "share_declined"))
    client.post(f"/settings/sharing/{other_id}/inventory", data={"on": "1"})
    assert len(events("share_granted")) == g + 1
    client.post(f"/settings/sharing/{other_id}/inventory", data={"on": "0"})
    assert len(events("share_removed")) == r_ + 1
    client.post(f"/settings/sharing/{other_id}/inventory", data={"on": "1"})
    a = len(events("share_accepted"))
    assert other.post(f"/settings/shared-with-me/{me}/inventory/accept").status_code == 303
    assert other.post(f"/settings/shared-with-me/{me}/inventory/accept").status_code == 303   # a second accept changes nothing
    assert len(events("share_accepted")) == a + 1
    assert other.post(f"/settings/shared-with-me/{me}/inventory/remove").status_code == 303
    assert len(events("share_declined")) == d + 1
    assert events("share_declined")[-1].username == "AuditGrantee"
    client.post(f"/settings/sharing/{other_id}/inventory", data={"on": "1"})            # a pending offer the grantee declines
    assert other.post(f"/settings/shared-with-me/{me}/inventory/decline").status_code == 303
    assert len(events("share_declined")) == d + 2


def test_backup_load_success_and_failure_are_logged(client, db, me, world):
    blob = create(client, section=["inventory"]).content
    token = token_of(open_file(client, blob))
    n = len(events("backup_load_failed"))
    bad = client.post("/backup/load", data={"token": token, "load": ["inventory"], "mode-inventory": "bogus"})
    assert bad.status_code == 422 and len(events("backup_load_failed")) == n + 1
    ok_before = len(events("backup_loaded"))
    client.post("/backup/load", data={"token": token, "load": ["inventory"], "mode-inventory": "add"})
    assert len(events("backup_loaded")) == ok_before + 1
    assert PASS not in " ".join(e.detail for e in events("backup_loaded"))


def test_restore_failure_and_success_are_logged(client, db, me, world, monkeypatch):
    blob = create(client, scope="installation", section=[]).content
    token = token_of(open_file(client, blob))
    base = {"token": token, "safety_passphrase": PASS, "safety_confirm": PASS}
    n = len(events("restore_failed"))
    client.post("/backup/restore", data={**base, "confirm": "nope"})
    assert len(events("restore_failed")) == n + 1
    monkeypatch.setattr(backup_restore, "restore_installation", lambda *a, **k: backup_restore.RestoreReport(rows={"users": 1}, files=0, safety_copy="x.amidebackup"))
    ok = len(events("restore_ok"))
    own = TestClient(app, follow_redirects=False)
    own.post("/notice", data={"understand": "1"})
    own.post("/login", data={"username": "Tester", "password": "Test1!"})
    token = token_of(open_file(own, blob))
    own.post("/backup/restore", data={**base, "token": token, "confirm": "RESTORE"})
    rows = events("restore_ok")
    assert len(rows) == ok + 1 and rows[-1].username == "Tester"


def test_vendor_edit_and_delete_are_logged(client):
    client.post("/vendors", data={"name": "Audit Example Vendor"})
    with SessionLocal() as s:
        vid = s.scalar(select(Vendor.id).where(Vendor.name == "Audit Example Vendor"))
    e, d = len(events("vendor_edited")), len(events("vendor_deleted"))
    r = client.post(f"/vendors/{vid}", data={"name": "Audit Example Vendor"})
    assert r.status_code in (200, 303)
    assert len(events("vendor_edited")) == e + 1
    client.post(f"/vendors/{vid}/delete")
    deleted = events("vendor_deleted")
    assert len(deleted) == d + 1 and "Audit Example" not in deleted[-1].detail
