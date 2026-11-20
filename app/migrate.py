"""Apply Alembic migrations programmatically (run on app startup)."""

import contextlib
import time
from pathlib import Path

from alembic import command
from alembic.config import Config

from app import config

ROOT = Path(__file__).resolve().parent.parent


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


def upgrade_db(url: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or config.DATABASE_URL)
    # Don't let alembic.ini's logging config override the web server's.
    cfg.attributes["configure_logger"] = False
    with _one_at_a_time():
        command.upgrade(cfg, "head")
