"""Apply Alembic migrations programmatically (run on app startup)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from app import config

ROOT = Path(__file__).resolve().parent.parent


def upgrade_db(url: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or config.DATABASE_URL)
    # Don't let alembic.ini's logging config override the web server's.
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
