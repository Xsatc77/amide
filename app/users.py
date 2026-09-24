"""Account admin for whoever runs the server (there is no email for self-service recovery).

    python -m app.users list
    python -m app.users reset-password NAME        (asks for the new password twice)
    python -m app.users reset-2fa NAME             (turns two-factor off; they can set it up again)
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth import passwords, sessions
from app.db import SessionLocal
from app.migrate import upgrade_db
from app.models import User


def _find(db, name: str) -> User | None:
    return db.scalar(select(User).where(User.username_key == passwords.username_key(name)))


def user_rows(db) -> list[dict]:
    """All accounts as plain dicts, for the CLI `list` command and the Settings admin table."""
    rows = []
    for u in db.scalars(select(User).order_by(User.id)):
        rows.append({
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "totp_enabled": u.totp_enabled,
            "locked": sessions.is_locked(u, sessions.now_utc()),
            "last_login": u.last_login_at,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.users", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show all accounts")
    reset_pw = sub.add_parser("reset-password", help="set a new password (also lifts a lockout)")
    reset_pw.add_argument("username")
    reset_pw.add_argument("--password", help=argparse.SUPPRESS)  # for scripts/tests; normally prompted
    reset_2fa = sub.add_parser("reset-2fa", help="turn off two-factor authentication")
    reset_2fa.add_argument("username")
    args = parser.parse_args(argv)

    upgrade_db()
    with SessionLocal() as db:
        if args.command == "list":
            for row in user_rows(db):
                flags = ["admin" if row["is_admin"] else "", "2FA on" if row["totp_enabled"] else "2FA off",
                         "locked" if row["locked"] else ""]
                last = row["last_login"].strftime("%Y-%m-%d %H:%M UTC") if row["last_login"] else "never"
                print(f"{row['username']:<32} {', '.join(f for f in flags if f):<24} last login: {last}")
            return 0

        user = _find(db, args.username)
        if user is None:
            print(f"No user named {args.username!r}.", file=sys.stderr)
            return 1

        if args.command == "reset-password":
            new = args.password
            if new is None:
                new = getpass.getpass("New password: ")
                confirm = getpass.getpass("Repeat it: ")
            else:
                confirm = new
            if problems := passwords.password_errors(new, confirm):
                print("\n".join(problems), file=sys.stderr)
                return 1
            user.password_hash = passwords.hash_password(new)
            sessions.clear_failures(user)
            print(f"Password reset for {user.username}.")
        else:
            user.totp_enabled, user.totp_secret, user.totp_last_step = False, None, None
            print(f"Two-factor authentication turned off for {user.username}.")
        db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
