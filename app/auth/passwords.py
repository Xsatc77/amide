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


# argon2 is deliberately slow (tens of milliseconds, a lot of memory): from an async route it must run in a worker thread, or one
# sign-in stalls every other request. A precomputed hash lets an unknown username cost the same work as a wrong password.
_DUMMY_HASH = _hasher.hash("amide-dummy-password")


async def hash_password_async(password: str) -> str:
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password_hash: str, password: str) -> bool:
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(verify_password, password_hash, password)


async def verify_dummy_async(password: str) -> None:
    """Do the work of a password check against nothing (for a name that does not exist)."""
    await verify_password_async(_DUMMY_HASH, password)
