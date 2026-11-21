"""Apply Alembic migrations programmatically (run on app startup)."""

import contextlib
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app import config

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)
SNAPSHOT_KEEP = 3                    # how many pre-upgrade copies of the database are kept


@contextlib.contextmanager
def _one_at_a_time():
    """An exclusive lock on a file next to the database, so two processes starting on the same data folder (two workers, a restart
    overlapping the old container) migrate one after the other instead of both creating the same tables. The second one waits, then
    finds nothing left to do. The lock is the operating system's, so a crash can never leave it stuck."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handle = open(config.DATA_DIR / ".migrate.lock", "a+b")
    except OSError:
        yield                                   # a read-only or missing folder: nothing to lock, migrate as before
        return
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)       # noqa: E731
        except ImportError:                     # Windows
            import msvcrt
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
            locked = lambda: (handle.seek(0), msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1))      # noqa: E731
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                locked()
    finally:
        handle.close()


def snapshot_before_upgrade(cfg: Config, url: str) -> Path | None:
    """Before an UPDATE changes the database's shape, keep a consistent copy of it (data/backups/pre-upgrade-<from>-to-<to>-<time>.db) so an
    update can be undone: an older Amide cannot start on a database a newer one has migrated. Only an existing database that is behind the
    code gets one (a new install and a start with nothing to migrate do not), only SQLite files, and the newest SNAPSHOT_KEEP are kept.
    A failure to make the copy is logged and never stops Amide starting. Returns the copy's path, or None."""
    try:
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
            return None
        source = Path(parsed.database)
        if not source.is_file() or source.stat().st_size == 0:
            return None
        head = ScriptDirectory.from_config(cfg).get_current_head()
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
        finally:
            engine.dispose()
        if current is None or current == head:
            return None
        folder = config.DATA_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = folder / f"pre-upgrade-{current}-to-{head}-{stamp}.db"
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)          # SQLite's own online backup: consistent even with a write-ahead log
            finally:
                dst.close()
        finally:
            src.close()
        with contextlib.suppress(OSError):
            target.chmod(0o600)
        for old in sorted(folder.glob("pre-upgrade-*.db"), key=lambda f: f.stat().st_mtime)[:-SNAPSHOT_KEEP]:
            old.unlink(missing_ok=True)
        log.info("copied the database to %s before updating it from %s to %s", target.name, current, head)
        return target
    except Exception:                      # noqa: BLE001 -- a failed safety copy must not stop Amide from starting
        log.warning("could not copy the database before the update; continuing without a pre-upgrade copy", exc_info=True)
        return None


def upgrade_db(url: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or config.DATABASE_URL)
    # Don't let alembic.ini's logging config override the web server's.
    cfg.attributes["configure_logger"] = False
    with _one_at_a_time():
        snapshot_before_upgrade(cfg, url or config.DATABASE_URL)
        command.upgrade(cfg, "head")
