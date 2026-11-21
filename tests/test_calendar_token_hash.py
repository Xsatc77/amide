"""The calendar feed address is found by its SHA-256 and kept sealed: a copy of the database holds neither a readable nor a guessable address."""

import hashlib
import os
import sqlite3
import sys
import tempfile

from sqlalchemy import text

from app import secretbox
from app.db import SessionLocal
from app.models import User


def raw(sql, **params):
    with SessionLocal() as s:
        return s.execute(text(sql), params).all()


def make_feed(client):
    client.post("/settings/calendar-feed", data={"action": "create"})
    with SessionLocal() as s:
        return s.get(User, s.scalar(text("SELECT id FROM users WHERE username_key = 'tester'"))).calendar_token


def test_the_database_holds_a_hash_and_a_sealed_copy_never_the_address(client, me):
    token = make_feed(client)
    assert token and len(token) >= 30
    (sealed, stored_hash), = raw("SELECT calendar_token, calendar_token_hash FROM users WHERE id = :i", i=me)
    assert sealed.startswith("enc1:") and token not in sealed
    assert stored_hash == hashlib.sha256(token.encode()).hexdigest() and token not in stored_hash


def test_the_feed_opens_by_the_address_and_settings_still_shows_it(client, me):
    token = make_feed(client)
    assert client.get(f"/calendar/feed/{token}.ics").status_code == 200
    assert f"/calendar/feed/{token}.ics" in client.get("/settings").text
    assert client.get("/calendar/feed/not-the-address.ics").status_code == 404


def test_a_new_address_replaces_the_old_one_and_turning_it_off_ends_it(client, me):
    first = make_feed(client)
    second = make_feed(client)
    assert first != second
    assert client.get(f"/calendar/feed/{first}.ics").status_code == 404
    assert client.get(f"/calendar/feed/{second}.ics").status_code == 200
    client.post("/settings/calendar-feed", data={"action": "off"})
    assert client.get(f"/calendar/feed/{second}.ics").status_code == 404
    assert raw("SELECT calendar_token, calendar_token_hash FROM users WHERE id = :i", i=me) == [(None, None)]


def test_the_hash_alone_is_not_an_address(client, me):
    make_feed(client)
    (stored_hash,), = raw("SELECT calendar_token_hash FROM users WHERE id = :i", i=me)
    assert client.get(f"/calendar/feed/{stored_hash}.ics").status_code == 404


def test_secretbox_seals_the_calendar_address_and_writes_its_hash_for_a_restore():
    values = secretbox.seal_columns({"calendar_token": "plain-address-123", "totp_secret": None})
    assert values["calendar_token"].startswith("enc1:") and values["calendar_token_hash"] == hashlib.sha256(b"plain-address-123").hexdigest()


def test_the_upgrade_keeps_an_existing_feed_address_working():
    """An old database holds the address in plain text: migration 0108 hashes it, seals it, and the same address still opens the feed."""
    data = tempfile.mkdtemp(prefix="cal-mig-")
    code = r"""
import os, sqlite3, sys
os.environ["AMIDE_DATA_DIR"] = sys.argv[1]
sys.path.insert(0, sys.argv[2])
from alembic import command
from alembic.config import Config
from app import config
cfg = Config(os.path.join(sys.argv[2], "alembic.ini")); cfg.set_main_option("script_location", os.path.join(sys.argv[2], "migrations"))
cfg.set_main_option("sqlalchemy.url", config.DATABASE_URL); cfg.attributes["configure_logger"] = False
command.upgrade(cfg, "0107")
db = sqlite3.connect(os.path.join(sys.argv[1], "amide.db"))
info = list(db.execute("pragma table_info(users)"))
vals = {r[1]: ("2026-01-01 00:00:00" if "DATE" in (r[2] or "").upper() else "" if "CHAR" in (r[2] or "").upper() or "TEXT" in (r[2] or "").upper() else 0) for r in info if r[3] and r[4] is None and r[1] != "id"}
vals.update(username="olduser", username_key="olduser", password_hash="x", calendar_token="oldaddress-abcdefghijklmnopqrstuvwxyz0123")
db.execute("insert into users (%s) values (%s)" % (",".join(vals), ",".join("?" * len(vals))), list(vals.values())); db.commit(); db.close()
command.upgrade(cfg, "head")
db = sqlite3.connect(os.path.join(sys.argv[1], "amide.db"))
print(db.execute("select calendar_token, calendar_token_hash from users where username_key = 'olduser'").fetchone())
"""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code, data, root], capture_output=True, text=True, timeout=120).stdout.strip().splitlines()[-1]
    sealed, stored_hash = eval(out)          # a tuple of two plain strings printed by the child above
    assert sealed.startswith("enc1:") and "oldaddress" not in sealed
    assert stored_hash == hashlib.sha256(b"oldaddress-abcdefghijklmnopqrstuvwxyz0123").hexdigest()
