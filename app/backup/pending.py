"""Opened backups waiting for the person's next step.

After a file is decrypted and checked, the preview page needs the contents again to load sections. Rather than ask for
the file and passphrase twice, the verified archive is kept for ten minutes under a random token tied to the account that
opened it. On disk it is encrypted with a random key that exists only in this process's memory, so it is never plaintext
on disk and cannot be read after a restart (leftover files are deleted). Nothing is kept in the browser. Expired and used
files are deleted."""

import json
import os
import re
import secrets
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import config
from app.backup.container import BackupError

TTL_SECONDS = 600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")
_EXPIRED = "That step has expired. Open the backup file again."
_KEYS: dict[str, tuple[int, bytes, float]] = {}      # token -> (account id, key, when it was opened); memory only


def _folder():
    folder = config.DATA_DIR / "backups" / ".pending"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def purge(*, everything: bool = False) -> None:
    """Delete expired entries, and any file this process holds no key for (a leftover from an earlier run)."""
    cutoff = time.time() - TTL_SECONDS
    for token in [t for t, (_, _, made) in _KEYS.items() if everything or made < cutoff]:
        _KEYS.pop(token, None)
    for path in _folder().glob("*"):
        if everything or path.stem not in _KEYS:
            path.unlink(missing_ok=True)


def put(uid: int, zipped: bytes) -> str:
    purge()
    token, key, nonce = secrets.token_urlsafe(24), AESGCM.generate_key(256), os.urandom(12)
    (_folder() / f"{token}.bin").write_bytes(nonce + AESGCM(key).encrypt(nonce, zipped, token.encode()))
    _KEYS[token] = (uid, key, time.time())
    return token


def get(uid: int, token: str) -> bytes:
    """The archive bytes for this token if it is the caller's and still fresh, else BackupError."""
    purge()
    entry = _KEYS.get(token or "") if _TOKEN.fullmatch(token or "") else None
    if entry is None or entry[0] != uid:
        raise BackupError(_EXPIRED)
    try:
        blob = (_folder() / f"{token}.bin").read_bytes()
        return AESGCM(entry[1]).decrypt(blob[:12], blob[12:], token.encode())
    except (OSError, InvalidTag):
        raise BackupError(_EXPIRED) from None


def drop(token: str) -> None:
    if _TOKEN.fullmatch(token or ""):
        _KEYS.pop(token, None)
        (_folder() / f"{token}.bin").unlink(missing_ok=True)
