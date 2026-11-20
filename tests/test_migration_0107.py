"""0107: shares.accepted_at. Existing shares are accepted by the upgrade (nobody loses access); new ones are pending; downgrade drops the column."""
import sqlite3

from alembic import command

from tests.test_migrations import _cfg


def test_existing_shares_become_accepted_and_downgrade_drops_the_column(tmp_path):
    db = tmp_path / "m.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0106")
    with sqlite3.connect(db) as c:
        for n in ("one", "two"):
            c.execute("insert into users(username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) "
                      "values (?, ?, 'x', 0, 0, 0, '2026-01-01')", (n, n))
        c.execute("insert into shares(owner_id, grantee_id, category, created_at) values (1, 2, 'inventory', '2026-01-01')")
    command.upgrade(cfg, "0107")
    with sqlite3.connect(db) as c:
        assert c.execute("select accepted_at is not null from shares").fetchall() == [(1,)]
        c.execute("insert into shares(owner_id, grantee_id, category, created_at) values (1, 2, 'personal_data', '2026-01-02')")
        assert c.execute("select accepted_at from shares where category='personal_data'").fetchone()[0] is None   # new = pending
    command.downgrade(cfg, "0106")
    with sqlite3.connect(db) as c:
        assert "accepted_at" not in {r[1] for r in c.execute("pragma table_info(shares)")}
        assert c.execute("select category from shares").fetchall() == [("inventory",)]    # the pending one is dropped, not made live
