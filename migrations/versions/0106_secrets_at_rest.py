"""seal users.totp_secret and users.ai_api_key; sessions keyed by the hash of the cookie token

Upgrade: every plaintext 2FA seed and AI key is sealed with the secret key (app/secretbox.py; rows already sealed are skipped, so
running it twice changes nothing), the two columns are widened to hold the ciphertext, and every login session is deleted: session
ids are now the SHA-256 of the cookie token, so the cookies already in browsers no longer match anything and everyone signs in once more.

Downgrade: seals are opened again where the current key can read them (best effort; a value that cannot be read is left as ciphertext,
which an older Amide would not understand, so that person sets 2FA up again or an administrator removes it), the columns go back to their
old lengths, and the sessions are deleted again (an older Amide looks sessions up by the raw token).

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0106'
down_revision: Union[str, None] = '0105'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("totp_secret", "ai_api_key")


def _rewrite(convert) -> None:
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, totp_secret, ai_api_key FROM users")).mappings().all():
        changes = {}
        for name in COLUMNS:
            value = row[name]
            if value:
                new = convert(value)
                if new is not None and new != value:
                    changes[name] = new
        if changes:
            sets = ", ".join(f"{name} = :{name}" for name in changes)
            bind.execute(sa.text(f"UPDATE users SET {sets} WHERE id = :id"), {**changes, "id": row["id"]})


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.alter_column('totp_secret', existing_type=sa.String(length=64), type_=sa.String(length=128), existing_nullable=True)
        batch.alter_column('ai_api_key', existing_type=sa.String(length=300), type_=sa.String(length=512), existing_nullable=True)
    from app import secretbox
    _rewrite(lambda value: value if secretbox.is_encrypted(value) else secretbox.encrypt(value))
    op.execute("DELETE FROM sessions")


def downgrade() -> None:
    from app import secretbox
    _rewrite(lambda value: secretbox.decrypt(value) if secretbox.is_encrypted(value) else None)
    op.execute("DELETE FROM sessions")
    with op.batch_alter_table('users') as batch:
        batch.alter_column('totp_secret', existing_type=sa.String(length=128), type_=sa.String(length=64), existing_nullable=True)
        batch.alter_column('ai_api_key', existing_type=sa.String(length=512), type_=sa.String(length=300), existing_nullable=True)
