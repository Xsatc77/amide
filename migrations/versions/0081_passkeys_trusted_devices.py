"""sign-in: passkeys, remembered devices, and the passkey challenge held on a session

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0081'
down_revision: Union[str, None] = '0080'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'passkeys',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('credential_id', sa.String(length=512), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('credential_id'),
    )
    op.create_index('ix_passkeys_user_id', 'passkeys', ['user_id'])
    op.create_table(
        'trusted_devices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_trusted_devices_user_id', 'trusted_devices', ['user_id'])
    with op.batch_alter_table('sessions') as b:
        b.add_column(sa.Column('webauthn_challenge', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('sessions') as b:
        b.drop_column('webauthn_challenge')
    op.drop_index('ix_trusted_devices_user_id', table_name='trusted_devices')
    op.drop_table('trusted_devices')
    op.drop_index('ix_passkeys_user_id', table_name='passkeys')
    op.drop_table('passkeys')
