"""Checking a two-factor (TOTP) code, shared by sign-in, account 2FA changes and the body photo unlock.

Rules every caller relies on:
- Replay protection: a code is good once. `check_code` records the 30-second step it matched in `user.totp_last_step`,
  and any code from that step or an earlier one is refused afterwards ("already used"). The caller commits.
- Lockout: `check_code` only judges the code. A caller that gets a message back must call
  `sessions.record_failure(user, now)` so wrong codes count toward the account lockout (config.LOCKOUT_ATTEMPTS),
  and call `sessions.clear_failures(user)` after a good one.
"""

from sqlalchemy import or_, update
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import set_committed_value

from app.auth import totp
from app.models import User


def check_code(user: User, code: str, now) -> str | None:
    """None if the code is good (and records it as used), otherwise the message to show."""
    step = totp.matching_step(user.totp_secret, code, now)
    if step is None:
        return "That code didn't match. Enter the 6-digit code your app shows now."
    if user.totp_last_step is not None and step <= user.totp_last_step:
        return "That code was already used. Wait for the next one in your app."
    db = object_session(user)
    if db is None or user.id is None:
        user.totp_last_step = step
        return None
    # Compare-and-set in the database: of two requests that carry the same code at the same moment only one UPDATE matches a
    # row (the other sees the step already recorded), so a code can never be used twice.
    won = db.execute(update(User).where(User.id == user.id, or_(User.totp_last_step.is_(None), User.totp_last_step < step))
                     .values(totp_last_step=step)).rowcount
    if not won:
        return "That code was already used. Wait for the next one in your app."
    set_committed_value(user, "totp_last_step", step)
    return None
