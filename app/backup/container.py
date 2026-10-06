"""The encrypted wrapper around a backup archive.

A `.amidebackup` file is: magic bytes, the scrypt cost, a random salt and nonce, then the AES-256-GCM encryption of
the archive. The header is authenticated too, so any change to the file, a wrong passphrase, or a truncated download
all fail the same way, before anything inside is parsed. Pure bytes in, bytes out."""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"AMIDEBK1"
SALT_LEN = 16
NONCE_LEN = 12
SCRYPT_N = 2 ** 15              # about 100 ms and 32 MB per attempt
MIN_PASSPHRASE = 8
_N_MIN, _N_MAX = 2 ** 10, 2 ** 20   # accepted range when reading, so a crafted header cannot ask for gigabytes
_HEADER = struct.Struct(">8sI16s12s")


class BackupError(ValueError):
    """A backup cannot be created, read or loaded. The message is written for the person and safe to show."""


def _derive(passphrase: str, salt: bytes, n: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode("utf-8"))


def check_passphrase(passphrase: str) -> None:
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise BackupError(f"The passphrase must be at least {MIN_PASSPHRASE} characters.")


def seal(data: bytes, passphrase: str, *, n: int | None = None) -> bytes:
    """Encrypt `data` with `passphrase`. `n` is the scrypt cost (tests lower it; the default is SCRYPT_N)."""
    check_passphrase(passphrase)
    n = n or SCRYPT_N
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    header = _HEADER.pack(MAGIC, n, salt, nonce)
    return header + AESGCM(_derive(passphrase, salt, n)).encrypt(nonce, data, header)


def unseal(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a sealed file, or raise BackupError."""
    if len(blob) < _HEADER.size + 16 or blob[:8] != MAGIC:
        raise BackupError("This is not an Amide backup file.")
    magic, n, salt, nonce = _HEADER.unpack(blob[:_HEADER.size])
    if not _N_MIN <= n <= _N_MAX or n & (n - 1):
        raise BackupError("This backup file is damaged.")
    try:
        return AESGCM(_derive(passphrase or "", salt, n)).decrypt(nonce, blob[_HEADER.size:], blob[:_HEADER.size])
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the file is damaged.") from None
