"""sharing: shares table, vendors become shared

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SHARE_CATEGORY_VALUES = ('inventory', 'personal_data')


def upgrade() -> None:
    bind = op.get_bind()

    # Dedupe vendors sharing the same name (case-insensitive) across different owners -- the old
    # per-owner unique constraint allowed this; the new global-unique constraint can't have it.
    # Keep the lowest id, repoint any inventory_items.vendor_id referencing a duplicate, drop the rest.
    rows = bind.execute(text("SELECT id, name FROM vendors ORDER BY name COLLATE NOCASE, id")).fetchall()
    keep_by_name: dict[str, int] = {}
    remap: dict[int, int] = {}
    for row in rows:
        key = row.name.strip().lower()
        if key not in keep_by_name:
            keep_by_name[key] = row.id
        else:
            remap[row.id] = keep_by_name[key]
    for old_id, new_id in remap.items():
        bind.execute(text("UPDATE inventory_items SET vendor_id = :new_id WHERE vendor_id = :old_id"),
                    {"new_id": new_id, "old_id": old_id})
        bind.execute(text("DELETE FROM vendors WHERE id = :old_id"), {"old_id": old_id})

    # Rebuilt by hand with raw SQL rather than batch_alter_table: SQLite reflection (which batch
    # mode's recreate relies on) doesn't carry a column's COLLATE clause back into the rebuilt
    # table, so a batch recreate here would silently drop vendors.name's COLLATE NOCASE.
    bind.execute(text("ALTER TABLE vendors RENAME COLUMN owner_id TO created_by_id"))
    bind.execute(text("""
        CREATE TABLE vendors_new (
            id INTEGER NOT NULL PRIMARY KEY,
            created_by_id INTEGER,
            name VARCHAR(200) COLLATE NOCASE NOT NULL,
            website VARCHAR(300),
            contact_info VARCHAR(300),
            notes TEXT,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_vendor_name UNIQUE (name),
            FOREIGN KEY(created_by_id) REFERENCES users (id)
        )
    """))
    bind.execute(text("""
        INSERT INTO vendors_new (id, created_by_id, name, website, contact_info, notes, created_at)
        SELECT id, created_by_id, name, website, contact_info, notes, created_at FROM vendors
    """))
    bind.execute(text("DROP TABLE vendors"))
    bind.execute(text("ALTER TABLE vendors_new RENAME TO vendors"))
    bind.execute(text("CREATE INDEX ix_vendors_created_by_id ON vendors (created_by_id)"))

    op.create_table(
        'shares',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('grantee_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.Enum(*SHARE_CATEGORY_VALUES, name='sharecategory',
                                      native_enum=False, length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['grantee_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'grantee_id', 'category', name='uq_share_owner_grantee_category'),
    )
    op.create_index('ix_shares_owner_id', 'shares', ['owner_id'])
    op.create_index('ix_shares_grantee_id', 'shares', ['grantee_id'])


def downgrade() -> None:
    op.drop_index('ix_shares_grantee_id', table_name='shares')
    op.drop_index('ix_shares_owner_id', table_name='shares')
    op.drop_table('shares')

    bind = op.get_bind()
    bind.execute(text("ALTER TABLE vendors RENAME COLUMN created_by_id TO owner_id"))
    # owner_id stays nullable here (the original 0007 shape had it NOT NULL) -- a vendor whose
    # created_by_id was nulled by a user deletion after upgrading has no non-lossy way back.
    bind.execute(text("""
        CREATE TABLE vendors_new (
            id INTEGER NOT NULL PRIMARY KEY,
            owner_id INTEGER,
            name VARCHAR(200) COLLATE NOCASE NOT NULL,
            website VARCHAR(300),
            contact_info VARCHAR(300),
            notes TEXT,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_vendor_owner_name UNIQUE (owner_id, name),
            FOREIGN KEY(owner_id) REFERENCES users (id)
        )
    """))
    bind.execute(text("""
        INSERT INTO vendors_new (id, owner_id, name, website, contact_info, notes, created_at)
        SELECT id, owner_id, name, website, contact_info, notes, created_at FROM vendors
    """))
    bind.execute(text("DROP TABLE vendors"))
    bind.execute(text("ALTER TABLE vendors_new RENAME TO vendors"))
    bind.execute(text("CREATE INDEX ix_vendors_owner_id ON vendors (owner_id)"))
