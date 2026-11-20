"""Secrets at rest: authenticated encryption for the few values Amide must be able to read back (a 2FA seed, an AI provider key).

Values are sealed with AES-256-GCM and stored as `enc1:` + base64url(nonce + ciphertext + tag). The prefix is versioned so a
plaintext value written before this existed is recognised (and still readable until the migration or the next save seals it).

Where the key comes from:
  * the environment variable AMIDE_SECRET_KEY (any string; 32 bytes are derived from it with HKDF-SHA256), or
  * otherwise a random 32-byte key that Amide creates on first use in DATA_DIR/secret.key (mode 0600 where the system has modes).

Losing the key makes every stored 2FA seed and AI key unreadable (people set 2FA up again, an administrator can remove it, and the AI key
is typed again). The key file lives inside data/, so a copy of data/ still carries it: put AMIDE_SECRET_KEY in the environment (not in
data/) so a copy of the database alone is useless. See docs/DEPLOYING.md, "Secrets At Rest".

Nothing here logs a key or a plaintext. decrypt() never raises: a bad tag or the wrong key gives None.
"""

import base64
import hashlib
import logging
import os
import secrets
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app import config

PREFIX = "enc1:"
ENV_KEY = "AMIDE_SECRET_KEY"
KEY_FILE = "secret.key"
_INFO = b"amide secretbox v1"

security_log = logging.getLogger("amide.security")

_lock = threading.Lock()
_file_keys: dict[str, bytes] = {}
_unreadable_seen: set[str] = set()


class SecretKeyError(RuntimeError):
    """The key file exists but is not a usable key (never replaced silently: that would destroy every sealed value)."""


def _from_passphrase(text: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(text.encode("utf-8"))


def _read_key_file(path) -> bytes:
    raw = path.read_text(encoding="ascii", errors="replace").strip()
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        key = b""
    if len(key) != 32:
        raise SecretKeyError(f"{path} is not a valid Amide secret key file; fix or restore it (it is never overwritten automatically)")
    return key


def _create_key_file(path) -> bytes:
    """Write a new random key atomically: a private temporary file is linked into place, so a second process (or a crash) never sees half a key."""
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(key.hex() + "\n")
        try:
            os.link(tmp, path)                      # fails when another process won the race
        except FileExistsError:
            return _read_key_file(path)
        except OSError:                             # a filesystem without hard links
            if path.exists():
                return _read_key_file(path)
            os.replace(tmp, path)
        return key
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _key() -> bytes:
    env = os.environ.get(ENV_KEY, "")
    if env:
        return _from_passphrase(env)
    path = config.DATA_DIR / KEY_FILE
    cache_key = str(path)
    with _lock:
        if cache_key not in _file_keys:
            _file_keys[cache_key] = _read_key_file(path) if path.exists() else _create_key_file(path)
        return _file_keys[cache_key]


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt(value: str) -> str | None:
    """The plaintext, or None when the value is not sealed by this key (wrong key, damaged, not ours). Never raises."""
    try:
        if not is_encrypted(value):
            return None
        blob = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
        if len(blob) < 12 + 16:
            return None
        return AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode("utf-8")
    except (InvalidTag, ValueError, UnicodeError, SecretKeyError, OSError):
        return None


def reveal(value: str | None, what: str = "a stored secret") -> str | None:
    """What a column holds, as plaintext: legacy plaintext as it is, sealed values opened, an unreadable one as None (with one log line)."""
    if value is None or not is_encrypted(value):
        return value
    plain = decrypt(value)
    if plain is None:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        with _lock:
            fresh = digest not in _unreadable_seen and len(_unreadable_seen) < 1000
            if fresh:
                _unreadable_seen.add(digest)
        if fresh:
            security_log.warning("%s cannot be read with the current secret key (wrong or lost AMIDE_SECRET_KEY / secret.key); it is treated as not set", what)
    return plain


class EncryptedString(TypeDecorator):
    """A text column sealed with the secret key. Reads give plaintext (legacy plaintext is passed through), unreadable values read as None."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or is_encrypted(value):
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return reveal(value)


# ---- backups: raw SQL bypasses the column type, so the backup code opens secrets for export and seals them again for the restoring installation

USER_SECRET_COLUMNS = ("totp_secret", "ai_api_key")


def open_columns(row: dict, columns=USER_SECRET_COLUMNS) -> dict:
    """A copy of a raw database row with the named secret columns as plaintext (unreadable ones become None)."""
    out = dict(row)
    for name in columns:
        if name in out:
            out[name] = reveal(out[name])
    return out


def seal_columns(values: dict, columns=USER_SECRET_COLUMNS) -> dict:
    """The same for writing a raw row: plaintext secrets are sealed with THIS installation's key."""
    out = dict(values)
    for name in columns:
        if out.get(name) and not is_encrypted(out[name]):
            out[name] = encrypt(out[name])
    return out
