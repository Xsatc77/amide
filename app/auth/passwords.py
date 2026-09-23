"""Username and password rules, and password hashing (argon2)."""

import re
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app import config

_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_hasher = PasswordHasher()


def username_error(name: str) -> str | None:
    if not _USERNAME.match(name or ""):
        return "Usernames are 3–32 characters: letters, numbers, dot, dash or underscore."
    return None


def username_key(name: str) -> str:
    """Usernames are not case sensitive: this is the form used for matching."""
    return name.strip().lower()


@dataclass
class Check:
    key: str
    label: str
    met: bool


def password_checks(password: str) -> list[Check]:
    """Each password rule and whether it's met (the form shows the same list with live checkmarks)."""
    n = config.PASSWORD_MIN_LENGTH
    return [
        Check("length", f"At least {n} characters", len(password) >= n),
        Check("upper", "1 uppercase letter", bool(re.search(r"[A-Z]", password))),
        Check("lower", "1 lowercase letter", bool(re.search(r"[a-z]", password))),
        Check("number", "1 number", bool(re.search(r"[0-9]", password))),
        Check("special", "1 special character", bool(re.search(r"[^A-Za-z0-9]", password))),
    ]


def password_errors(password: str, confirm: str) -> list[str]:
    errors = [f"Password needs {c.label[0].lower() + c.label[1:]}." for c in password_checks(password) if not c.met]
    if password != confirm:
        errors.append("Passwords don't match.")
    return errors


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
