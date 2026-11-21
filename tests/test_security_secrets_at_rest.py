"""S-07: 2FA seeds, AI keys and session ids are not readable from a copy of the database."""

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config, secretbox
from app.auth import sessions
from app.backup import restore
from app.backup.archive import read_archive
from app.backup.export import build_archive
from app.db import Base, SessionLocal, make_engine
from app.models import LoginSession, User
from photo_helpers import other_client

SEED = "JBSWY3DPEHPK3PXP"
AI_KEY = "sk-example-not-a-real-key-1234567890"


@pytest.fixture
def key_dir(tmp_path, monkeypatch):
    """A private data folder (and no environment key) so the key file is created from scratch."""
    monkeypatch.delenv(secretbox.ENV_KEY, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def raw(user_id, column):
    """The stored value, read by plain SQL (what a copy of the database file would show)."""
    with SessionLocal() as s:
        return s.execute(text(f"SELECT {column} FROM users WHERE id = :i"), {"i": user_id}).scalar()


# ---------------------------------------------------------------- the box

def test_round_trip_is_prefixed_and_random(key_dir):
    one, two = secretbox.encrypt("hello"), secretbox.encrypt("hello")
    assert one.startswith("enc1:") and secretbox.is_encrypted(one) and "hello" not in one
    assert one != two                                   # a fresh nonce every time
    assert secretbox.decrypt(one) == "hello" == secretbox.decrypt(two)
    assert not secretbox.is_encrypted("hello") and not secretbox.is_encrypted(None)


def test_decrypt_never_raises(key_dir):
    good = secretbox.encrypt("hello")
    assert secretbox.decrypt("hello") is None                       # not sealed
    assert secretbox.decrypt("enc1:") is None and secretbox.decrypt("enc1:!!!") is None
    assert secretbox.decrypt(good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")) is None      # damaged tag
    assert secretbox.decrypt(None) is None


def test_key_file_is_created_once_and_reused(key_dir):
    secretbox._file_keys.clear()
    sealed = secretbox.encrypt("x")
    key_file = key_dir / "secret.key"
    assert key_file.is_file() and len(bytes.fromhex(key_file.read_text().strip())) == 32
    secretbox._file_keys.clear()                         # as after a restart
    assert secretbox.decrypt(sealed) == "x" and not list(key_dir.glob("*.tmp"))


def test_environment_key_wins_and_a_different_key_cannot_read(key_dir, monkeypatch):
    monkeypatch.setenv(secretbox.ENV_KEY, "first key")
    sealed = secretbox.encrypt("secret value")
    assert not (key_dir / "secret.key").exists()         # the file is only for installs without the environment key
    assert secretbox.decrypt(sealed) == "secret value"
    monkeypatch.setenv(secretbox.ENV_KEY, "second key")
    assert secretbox.decrypt(sealed) is None


def test_a_broken_key_file_is_never_overwritten(key_dir):
    secretbox._file_keys.clear()
    (key_dir / "secret.key").write_text("not a key")
    assert secretbox.decrypt(secretbox.PREFIX + "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") is None
    with pytest.raises(secretbox.SecretKeyError):
        secretbox.encrypt("x")
    assert (key_dir / "secret.key").read_text() == "not a key"


# ---------------------------------------------------------------- the columns

def test_totp_seed_and_ai_key_are_sealed_in_the_database_and_read_back(client, me, db):
    user = db.get(User, me)
    user.totp_secret, user.ai_api_key = SEED, AI_KEY
    db.commit()
    for column, plain in (("totp_secret", SEED), ("ai_api_key", AI_KEY)):
        stored = raw(me, column)
        assert stored.startswith("enc1:") and plain not in stored
    with SessionLocal() as s:
        fresh = s.get(User, me)
        assert (fresh.totp_secret, fresh.ai_api_key) == (SEED, AI_KEY)


def test_a_legacy_plaintext_value_is_still_readable(client, me):
    with SessionLocal() as s:
        s.execute(text("UPDATE users SET totp_secret = :v WHERE id = :i"), {"v": SEED, "i": me})
        s.commit()
        assert s.get(User, me).totp_secret == SEED


def test_unreadable_values_read_as_not_set_and_are_logged_once(client, me, monkeypatch, caplog):
    with SessionLocal() as s:
        user = s.get(User, me)
        user.totp_secret, user.ai_api_key, user.calendar_token = SEED, AI_KEY, None
        s.commit()
    monkeypatch.setenv(secretbox.ENV_KEY, "a different key")
    secretbox._unreadable_seen.clear()
    with caplog.at_level(logging.WARNING, logger="amide.security"):
        with SessionLocal() as s:
            user = s.get(User, me)
            assert user.totp_secret is None and user.ai_api_key is None
        with SessionLocal() as s:
            s.get(User, me)
    lines = [r.getMessage() for r in caplog.records if r.name == "amide.security"]
    assert len(lines) == 2                               # one per value, not one per page
    assert all(SEED not in line and AI_KEY not in line and "enc1:" not in line for line in lines)


def test_a_page_still_renders_when_the_ai_key_is_unreadable_and_never_shows_a_key(client, me, monkeypatch):
    with SessionLocal() as s:
        s.get(User, me).ai_api_key = AI_KEY
        s.commit()
    page = client.get("/settings")
    assert page.status_code == 200 and AI_KEY not in page.text and "enc1:" not in page.text
    monkeypatch.setenv(secretbox.ENV_KEY, "another key")
    page = client.get("/settings")
    assert page.status_code == 200 and "enc1:" not in page.text
    with SessionLocal() as s:
        s.get(User, me).ai_api_key = None
        s.commit()


def test_admin_can_remove_2fa_for_someone_whose_seed_is_unreadable(client, monkeypatch):
    with other_client("sealedmember") as member:
        with SessionLocal() as s:
            target = s.query(User).filter_by(username_key="sealedmember").one()
            target.totp_secret, target.totp_enabled = SEED, True
            s.commit()
            target_id = target.id
        monkeypatch.setenv(secretbox.ENV_KEY, "the key was lost")
        with SessionLocal() as s:
            assert s.get(User, target_id).totp_secret is None and s.get(User, target_id).totp_enabled     # still required: never silently dropped
        r = client.post(f"/settings/admin/users/{target_id}/remove-2fa", follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            after = s.get(User, target_id)
            assert not after.totp_enabled and after.totp_secret is None
        monkeypatch.delenv(secretbox.ENV_KEY)
        r = member.post("/login", data={"username": "sealedmember", "password": "Test1!"})
        assert r.status_code in (200, 303)


# ---------------------------------------------------------------- the migration

def test_migration_seals_plaintext_once_and_clears_sessions(key_dir):
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("m0106", Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0106_secrets_at_rest.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    engine = sa.create_engine(f"sqlite:///{(key_dir / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, totp_secret VARCHAR(64), ai_api_key VARCHAR(300))")
        conn.exec_driver_sql("CREATE TABLE sessions (id VARCHAR(64) PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO users VALUES (1, 'PLAINSEED', 'plain-key'), (2, NULL, NULL), (3, 'x', '')")
        conn.exec_driver_sql("INSERT INTO sessions VALUES ('oldtoken')")
        already = secretbox.encrypt("keep")
        conn.exec_driver_sql("INSERT INTO users VALUES (4, ?, NULL)", (already,))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            first = conn.exec_driver_sql("SELECT id, totp_secret, ai_api_key FROM users ORDER BY id").all()
            mig.upgrade()
            second = conn.exec_driver_sql("SELECT id, totp_secret, ai_api_key FROM users ORDER BY id").all()
        assert first == second                              # idempotent: sealed rows are skipped
        assert secretbox.decrypt(first[0][1]) == "PLAINSEED" and secretbox.decrypt(first[0][2]) == "plain-key"
        assert first[1][1:] == (None, None) and first[2][2] == "" and first[3][1] == already
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM sessions").scalar() == 0
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        assert conn.exec_driver_sql("SELECT totp_secret, ai_api_key FROM users WHERE id = 1").one() == ("PLAINSEED", "plain-key")
    engine.dispose()


# ---------------------------------------------------------------- session ids

def test_the_database_holds_only_a_hash_of_the_cookie(client):
    cookie = client.cookies.get(sessions.COOKIE)
    assert cookie
    with SessionLocal() as s:
        ids = [r[0] for r in s.execute(text("SELECT id FROM sessions"))]
    assert cookie not in ids and sessions.hash_token(cookie) in ids
    assert client.get("/dashboard").status_code == 200       # the cookie still works


def test_a_stolen_database_id_is_not_a_working_cookie(client):
    with other_client("hashprobe") as probe:
        stolen = sessions.hash_token(probe.cookies.get(sessions.COOKIE))
        probe.cookies.clear()
        probe.cookies.set(sessions.COOKIE, stolen)
        assert probe.get("/dashboard").status_code in (303, 401)
        assert probe.get("/dashboard").headers.get("location", "").startswith("/notice")


def test_create_returns_the_raw_token_but_stores_its_hash(client):
    with SessionLocal() as s:
        row = sessions.create(s, sessions.now_utc())
        assert row.token and row.id == sessions.hash_token(row.token) and row.token not in (row.id,)
        assert sessions.get_live(s, row.token, sessions.now_utc()).id == row.id
        assert sessions.get_live(s, row.id, sessions.now_utc()) is None
        s.delete(row)
        s.commit()


# ---------------------------------------------------------------- backups

@pytest.fixture
def two_installs(tmp_path, monkeypatch):
    for name in ("COA_DIR", "PRICE_LIST_DIR", "LAB_REPORT_DIR", "WORKOUT_PDF_DIR", "WALLET_QR_DIR"):
        monkeypatch.setattr(config, name, tmp_path / "uploads" / name.lower())
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "library" / "cards")
    monkeypatch.setattr(config, "CARDS_JSON", tmp_path / "library" / "cards.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    engine = make_engine(f"sqlite:///{(tmp_path / 'scratch.db').as_posix()}")
    Base.metadata.create_all(engine)
    with SessionLocal() as shared:
        head = shared.execute(text("SELECT version_num FROM alembic_version")).scalar()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES (?)", (head,))
    with Session(engine) as s:
        yield s
    engine.dispose()


def test_backup_carries_plaintext_and_a_restore_seals_with_its_own_key(two_installs, monkeypatch):
    s = two_installs
    monkeypatch.setenv(secretbox.ENV_KEY, "key of installation one")
    admin = User(username="Admin", username_key="admin", password_hash="h", is_admin=True, totp_secret=SEED, totp_enabled=True, ai_api_key=AI_KEY)
    s.add(admin)
    s.commit()
    assert raw_in(s, "totp_secret").startswith("enc1:")
    archive = read_archive(build_archive(s, kind="backup", uid=admin.id, creator="Admin", keys=[], installation=True), max_bytes=50_000_000)
    users = [row for name in archive.names() if name.startswith("sections/") for row in __import__("json").loads(archive.read(name))["tables"].get("users", [])]
    assert users and users[0]["totp_secret"] == SEED and users[0]["ai_api_key"] == AI_KEY      # the archive is passphrase-sealed as a whole

    monkeypatch.setenv(secretbox.ENV_KEY, "key of installation two")       # restore into an installation with another key
    s.execute(text("UPDATE users SET totp_secret = 'junk'"))
    s.commit()
    restore.restore_installation(s, archive, uid=admin.id, creator="Admin", safety_passphrase="safety passphrase")
    stored = raw_in(s, "totp_secret")
    assert stored.startswith("enc1:") and SEED not in stored and secretbox.decrypt(stored) == SEED      # readable with the new key
    assert secretbox.decrypt(raw_in(s, "ai_api_key")) == AI_KEY
    monkeypatch.setenv(secretbox.ENV_KEY, "key of installation one")
    assert secretbox.decrypt(stored) is None                                # and not with the old one


def raw_in(session, column):
    return session.execute(text(f"SELECT {column} FROM users")).scalar()


def test_a_profile_export_never_carries_either_secret(client, me):
    from app.backup.sections import PROFILE_COLUMNS
    assert "totp_secret" not in PROFILE_COLUMNS and "ai_api_key" not in PROFILE_COLUMNS


def test_no_log_or_page_template_prints_the_secrets():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    for path in root.rglob("*.html"):
        assert "value=\"{{ user.ai_api_key" not in path.read_text(encoding="utf-8") and "me.ai_api_key }}" not in path.read_text(encoding="utf-8"), path
    for path in root.rglob("*.py"):
        text_ = path.read_text(encoding="utf-8")
        for number, line in enumerate(text_.splitlines(), 1):
            if ("log." in line or "logger." in line or "print(" in line) and ("totp_secret" in line or "ai_api_key" in line):
                raise AssertionError(f"{path}:{number} logs a secret")
