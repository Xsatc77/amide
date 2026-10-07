"""Scheduled automatic backups: every few days an encrypted whole-installation file goes into data/backups, and only the newest few are kept.
The passphrase lives in the environment (AMIDE_BACKUP_PASSPHRASE), never in the database, so a copy of the database alone cannot open them.
Copy data/backups off the computer (a NAS, a cloud drive) for protection against a lost disk."""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import config
from app.backup.container import BackupError, check_passphrase, seal
from app.backup.export import build_archive
from app.backup.restore import backups_dir
from app.models import User

INTERVAL = 3600


def _latest(folder: Path) -> float | None:
    times = [f.stat().st_mtime for f in folder.glob("amide-auto-*.amidebackup")]
    return max(times) if times else None


def run_if_due(session_factory, now: datetime) -> Path | None:
    """Write a backup when the newest automatic one is older than the interval (or there is none). Returns its path, or None."""
    if not config.BACKUP_PASSPHRASE:
        return None
    try:
        check_passphrase(config.BACKUP_PASSPHRASE)
    except BackupError:
        return None
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    latest = _latest(folder)
    if latest is not None and now.timestamp() - latest < config.BACKUP_EVERY_DAYS * 86400:
        return None
    with session_factory() as session:
        admin = session.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))
        if admin is None:
            return None
        data = build_archive(session, kind="backup", uid=admin.id, creator=admin.username, keys=[], installation=True)
    path = folder / f"amide-auto-{datetime.now(timezone.utc):%Y-%m-%d-%H%M%S}-{uuid.uuid4().hex[:6]}.amidebackup"
    path.write_bytes(seal(data, config.BACKUP_PASSPHRASE))
    for old in sorted(folder.glob("amide-auto-*.amidebackup"), key=lambda f: f.stat().st_mtime)[:-config.BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return path


async def _loop():
    from app.db import SessionLocal
    while True:
        try:
            await asyncio.to_thread(run_if_due, SessionLocal, datetime.now(timezone.utc))
        except Exception:
            pass                                    # the next hour tries again
        await asyncio.sleep(INTERVAL)


def start():
    return asyncio.create_task(_loop()) if config.BACKUP_PASSPHRASE else None


async def stop(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
