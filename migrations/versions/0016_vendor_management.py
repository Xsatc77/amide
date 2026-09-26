"""vendor management: contact/payment method types, vendor contacts/payment methods/favorites,
new Vendor columns, contact_info retirement

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'contact_method_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60, collation='NOCASE'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_contact_method_type_name'),
    )
    op.create_table(
        'payment_method_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60, collation='NOCASE'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_payment_method_type_name'),
    )

    # Backfill contact_info into notes before dropping it -- appended, not overwritten, so a
    # vendor with both a contact_info value and existing notes keeps both.
    bind.execute(text(
        "UPDATE vendors SET notes = "
        "CASE "
        "  WHEN contact_info IS NULL OR contact_info = '' THEN notes "
        "  WHEN notes IS NULL OR notes = '' THEN contact_info "
        "  ELSE notes || char(10) || char(10) || contact_info "
        "END "
        "WHERE contact_info IS NOT NULL AND contact_info != ''"
    ))

    # Rebuilt by hand with raw SQL rather than batch_alter_table: SQLite reflection (which batch
    # mode's recreate relies on) doesn't carry a column's COLLATE clause back into the rebuilt
    # table, so a batch recreate here would silently drop vendors.name's COLLATE NOCASE (see
    # 0009_sharing.py, which hit this same issue rebuilding this same table).
    bind.execute(text("""
        CREATE TABLE vendors_new (
            id INTEGER NOT NULL PRIMARY KEY,
            created_by_id INTEGER,
            name VARCHAR(200) COLLATE NOCASE NOT NULL,
            website VARCHAR(300),
            notes TEXT,
            created_at DATETIME NOT NULL,
            supplier VARCHAR(200),
            contact_name VARCHAR(120),
            recommended BOOLEAN,
            price_list_filename VARCHAR(120),
            price_list_url VARCHAR(500),
            price_list_updated_at DATE,
            CONSTRAINT uq_vendor_name UNIQUE (name),
            FOREIGN KEY(created_by_id) REFERENCES users (id)
        )
    """))
    bind.execute(text("""
        INSERT INTO vendors_new (id, created_by_id, name, website, notes, created_at)
        SELECT id, created_by_id, name, website, notes, created_at FROM vendors
    """))
    bind.execute(text("DROP TABLE vendors"))
    bind.execute(text("ALTER TABLE vendors_new RENAME TO vendors"))
    bind.execute(text("CREATE INDEX ix_vendors_created_by_id ON vendors (created_by_id)"))

    op.create_table(
        'vendor_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('method_type_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['method_type_id'], ['contact_method_types.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_vendor_contacts_vendor_id', 'vendor_contacts', ['vendor_id'])

    op.create_table(
        'vendor_payment_methods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('method_type_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['method_type_id'], ['payment_method_types.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_id', 'method_type_id', name='uq_vendor_payment_method'),
    )
    op.create_index('ix_vendor_payment_methods_vendor_id', 'vendor_payment_methods', ['vendor_id'])

    op.create_table(
        'vendor_favorites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'vendor_id', name='uq_vendor_favorite'),
    )
    op.create_index('ix_vendor_favorites_user_id', 'vendor_favorites', ['user_id'])
    op.create_index('ix_vendor_favorites_vendor_id', 'vendor_favorites', ['vendor_id'])

    for name in ("Email", "WhatsApp", "Telegram", "Phone"):
        bind.execute(text("INSERT INTO contact_method_types(name) VALUES (:name)"), {"name": name})
    for name in ("Credit Card", "Cash", "Crypto", "Alibaba"):
        bind.execute(text("INSERT INTO payment_method_types(name) VALUES (:name)"), {"name": name})


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index('ix_vendor_favorites_vendor_id', table_name='vendor_favorites')
    op.drop_index('ix_vendor_favorites_user_id', table_name='vendor_favorites')
    op.drop_table('vendor_favorites')
    op.drop_index('ix_vendor_payment_methods_vendor_id', table_name='vendor_payment_methods')
    op.drop_table('vendor_payment_methods')
    op.drop_index('ix_vendor_contacts_vendor_id', table_name='vendor_contacts')
    op.drop_table('vendor_contacts')

    # Rebuilt by hand (see upgrade()) to preserve vendors.name's COLLATE NOCASE. The re-added
    # contact_info column comes back empty -- the backfill-into-notes isn't reversible, matching
    # every other migration's downgrade in this codebase, which restore shape but not necessarily
    # historical values.
    bind.execute(text("""
        CREATE TABLE vendors_old (
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
        INSERT INTO vendors_old (id, created_by_id, name, website, notes, created_at)
        SELECT id, created_by_id, name, website, notes, created_at FROM vendors
    """))
    bind.execute(text("DROP TABLE vendors"))
    bind.execute(text("ALTER TABLE vendors_old RENAME TO vendors"))
    bind.execute(text("CREATE INDEX ix_vendors_created_by_id ON vendors (created_by_id)"))

    op.drop_table('payment_method_types')
    op.drop_table('contact_method_types')
