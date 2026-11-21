"""users.calendar_token_hash: the calendar feed address is found by its SHA-256, and the address itself is kept sealed

Upgrade: adds calendar_token_hash (unique, indexed), then for every person with a feed address stores its SHA-256 there and seals the
address itself with the secret key (app/secretbox.py) so Settings can still show it. Addresses already sealed are read back first, so
running it twice changes nothing, and every feed URL already handed to a calendar app keeps working.

Downgrade: opens the sealed addresses again where the current key can read them (best effort: one that cannot be read is cleared, and
that person makes a new address) and drops the hash column.

Revision ID: 0108
Revises: 0107
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0108'
down_revision: Union[str, None] = '0107'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('calendar_token_hash', sa.String(64), nullable=True))
        batch.alter_column('calendar_token', existing_type=sa.String(length=64), type_=sa.String(length=256), existing_nullable=True)
    op.create_index('ix_users_calendar_token_hash', 'users', ['calendar_token_hash'], unique=True)
    from app import secretbox
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, calendar_token FROM users WHERE calendar_token IS NOT NULL")).mappings().all():
        token = secretbox.decrypt(row["calendar_token"]) if secretbox.is_encrypted(row["calendar_token"]) else row["calendar_token"]
        if not token:
            bind.execute(sa.text("UPDATE users SET calendar_token = NULL WHERE id = :id"), {"id": row["id"]})      # sealed with a key we no longer have: a new address is needed
            continue
        sealed = row["calendar_token"] if secretbox.is_encrypted(row["calendar_token"]) else secretbox.encrypt(token)
        bind.execute(sa.text("UPDATE users SET calendar_token = :t, calendar_token_hash = :h WHERE id = :id"),
                     {"t": sealed, "h": secretbox.token_hash(token), "id": row["id"]})


def downgrade() -> None:
    from app import secretbox
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, calendar_token FROM users WHERE calendar_token IS NOT NULL")).mappings().all():
        token = secretbox.decrypt(row["calendar_token"]) if secretbox.is_encrypted(row["calendar_token"]) else row["calendar_token"]
        bind.execute(sa.text("UPDATE users SET calendar_token = :t WHERE id = :id"), {"t": token, "id": row["id"]})
    op.drop_index('ix_users_calendar_token_hash', table_name='users')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('calendar_token_hash')
        batch.alter_column('calendar_token', existing_type=sa.String(length=256), type_=sa.String(length=64), existing_nullable=True)
