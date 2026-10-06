"""What the Backup page does, with the checks the page relies on: making a sealed download and opening an upload."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import config
from app.backup import sections as reg
from app.backup.archive import Archive, read_archive
from app.backup.container import BackupError, check_passphrase, seal, unseal
from app.backup.export import build_archive
from app.models import User


def download_name(kind: str) -> str:
    """A new dated name every time, so one file never overwrites another."""
    return f"amide-{kind}-{datetime.now(timezone.utc):%Y-%m-%d-%H%M%S}.amidebackup"


def make_download(session: Session, user: User, *, kind: str, keys: list[str], installation: bool,
                  passphrase: str, confirm: str) -> tuple[str, bytes]:
    """(file name, sealed bytes) for a backup, export or share file. Raises BackupError with a message to show."""
    if passphrase != confirm:
        raise BackupError("The two passphrases do not match.")
    check_passphrase(passphrase)
    if installation and not user.is_admin:
        raise BackupError("Only an administrator can back up the whole installation.")
    if not installation:
        shared = [k for k in keys if k in reg.SHARED_SECTIONS]
        if shared and not user.is_admin:
            raise BackupError("Only an administrator can include shared data (vendors, price lists, library).")
    data = build_archive(session, kind=kind, uid=user.id, creator=user.username, keys=keys, installation=installation)
    if len(data) > config.MAX_BACKUP_BYTES:
        raise BackupError("This backup is larger than the allowed size. Choose fewer sections.")
    return download_name(kind), seal(data, passphrase)


def open_upload(blob: bytes, passphrase: str) -> tuple[bytes, Archive]:
    """Decrypt and verify an uploaded file: (the verified zip bytes, the opened archive)."""
    zipped = unseal(blob, passphrase)
    return zipped, read_archive(zipped, max_bytes=config.MAX_BACKUP_BYTES)
