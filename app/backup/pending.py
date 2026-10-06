"""Opened backups waiting for the person's next step.

After a file is decrypted and checked, the preview page needs the contents again to load sections. Rather than ask for
the file and passphrase twice, the verified archive is kept in a private temporary file for ten minutes under a random
token tied to the account that opened it. Nothing is kept in the browser. Expired and used files are deleted."""

import json
import re
import secrets
import time

from app import config
from app.backup.container import BackupError

TTL_SECONDS = 600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")


def _folder():
    folder = config.DATA_DIR / "backups" / ".pending"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def purge() -> None:
    """Delete every pending file older than the time to live."""
    cutoff = time.time() - TTL_SECONDS
    for path in _folder().glob("*"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def put(uid: int, zipped: bytes) -> str:
    purge()
    token = secrets.token_urlsafe(24)
    (_folder() / f"{token}.zip").write_bytes(zipped)
    (_folder() / f"{token}.json").write_text(json.dumps({"uid": uid}), encoding="utf-8")
    return token


def get(uid: int, token: str) -> bytes:
    """The archive bytes for this token if it is the caller's and still fresh, else BackupError."""
    purge()
    if not _TOKEN.fullmatch(token or ""):
        raise BackupError("That step has expired. Open the backup file again.")
    meta, data = _folder() / f"{token}.json", _folder() / f"{token}.zip"
    try:
        owner = json.loads(meta.read_text(encoding="utf-8"))["uid"]
        if owner != uid:
            raise BackupError("That step has expired. Open the backup file again.")
        return data.read_bytes()
    except (OSError, ValueError, KeyError):
        raise BackupError("That step has expired. Open the backup file again.") from None


def drop(token: str) -> None:
    if _TOKEN.fullmatch(token or ""):
        (_folder() / f"{token}.zip").unlink(missing_ok=True)
        (_folder() / f"{token}.json").unlink(missing_ok=True)
