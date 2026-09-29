"""peptide sheets: new columns and dosing/cycle/stacking/monitoring tables

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Rebuild peptides table by hand with raw SQL to preserve name's COLLATE NOCASE.
    # SQLite reflection (which batch_alter_table uses) doesn't carry the COLLATE clause back
    # into the rebuilt table, so a batch recreate would silently drop peptides.name's COLLATE NOCASE.
    bind.execute(text("""
        CREATE TABLE peptides_new (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(120) COLLATE NOCASE NOT NULL,
            aliases VARCHAR(300),
            card_number INTEGER,
            dose_low FLOAT,
            dose_mid FLOAT,
            dose_high FLOAT,
            dose_unit VARCHAR(20),
            typical_frequency VARCHAR(100),
            notes TEXT,
            source VARCHAR(20) NOT NULL DEFAULT 'custom',
            card_class VARCHAR(200),
            category VARCHAR(200),
            evidence_level VARCHAR(100),
            status VARCHAR(200),
            card_details JSON,
            card_image VARCHAR(100),
            half_life_text VARCHAR(100),
            bioavailability_text TEXT,
            tmax_text VARCHAR(100),
            route_summary VARCHAR(100),
            storage_before_text TEXT,
            storage_after_text TEXT,
            storage_temperature_text VARCHAR(100),
            legal_status_text TEXT,
            cost_estimate_text TEXT,
            usage_tips JSON,
            sheet_sections JSON,
            CONSTRAINT uq_peptide_name UNIQUE (name)
        )
    """))
    bind.execute(text("""
        INSERT INTO peptides_new (id, name, aliases, card_number, dose_low, dose_mid, dose_high,
                                  dose_unit, typical_frequency, notes, source, card_class, category,
                                  evidence_level, status, card_details, card_image)
        SELECT id, name, aliases, card_number, dose_low, dose_mid, dose_high,
               dose_unit, typical_frequency, notes, source, card_class, category,
               evidence_level, status, card_details, card_image FROM peptides
    """))
    bind.execute(text("DROP TABLE peptides"))
    bind.execute(text("ALTER TABLE peptides_new RENAME TO peptides"))

    op.create_table(
        'peptide_dosing_tiers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('level', sa.Enum(
            'Beginner', 'Intermediate', 'Advanced',
            name='dosing_tier_level', native_enum=False, length=20), nullable=False),
        sa.Column('dose_text', sa.String(length=100), nullable=False),
        sa.Column('frequency_text', sa.String(length=100), nullable=False),
        sa.Column('time_of_day', sa.Enum(
            'am', 'pm', 'bedtime', 'any',
            name='time_of_day', native_enum=False, length=20), nullable=True),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('peptide_id', 'level', name='uq_peptide_dosing_tier_level'),
    )
    op.create_index('ix_peptide_dosing_tiers_peptide_id', 'peptide_dosing_tiers', ['peptide_id'])

    op.create_table(
        'peptide_cycles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('on_weeks', sa.Integer(), nullable=True),
        sa.Column('off_weeks', sa.Integer(), nullable=True),
        sa.Column('max_cycles_per_year', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('peptide_id', name='uq_peptide_cycles_peptide_id'),
    )

    op.create_table(
        'peptide_stack_relations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('partner_name', sa.String(length=120), nullable=False),
        sa.Column('relation', sa.Enum(
            'works_with', 'avoid',
            name='stack_relation', native_enum=False, length=20), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_peptide_stack_relations_peptide_id', 'peptide_stack_relations', ['peptide_id'])

    op.create_table(
        'peptide_monitoring_tests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('test_name', sa.String(length=120), nullable=False),
        sa.Column('when_text', sa.String(length=200), nullable=False),
        sa.Column('why_text', sa.Text(), nullable=False),
        sa.Column('target_text', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_peptide_monitoring_tests_peptide_id', 'peptide_monitoring_tests', ['peptide_id'])


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index('ix_peptide_monitoring_tests_peptide_id', table_name='peptide_monitoring_tests')
    op.drop_table('peptide_monitoring_tests')
    op.drop_index('ix_peptide_stack_relations_peptide_id', table_name='peptide_stack_relations')
    op.drop_table('peptide_stack_relations')
    op.drop_table('peptide_cycles')
    op.drop_index('ix_peptide_dosing_tiers_peptide_id', table_name='peptide_dosing_tiers')
    op.drop_table('peptide_dosing_tiers')

    # Rebuild peptides table to remove the new columns.
    bind.execute(text("""
        CREATE TABLE peptides_new (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(120) COLLATE NOCASE NOT NULL,
            aliases VARCHAR(300),
            card_number INTEGER,
            dose_low FLOAT,
            dose_mid FLOAT,
            dose_high FLOAT,
            dose_unit VARCHAR(20),
            typical_frequency VARCHAR(100),
            notes TEXT,
            source VARCHAR(20) NOT NULL DEFAULT 'custom',
            card_class VARCHAR(200),
            category VARCHAR(200),
            evidence_level VARCHAR(100),
            status VARCHAR(200),
            card_details JSON,
            card_image VARCHAR(100),
            CONSTRAINT uq_peptide_name UNIQUE (name)
        )
    """))
    bind.execute(text("""
        INSERT INTO peptides_new (id, name, aliases, card_number, dose_low, dose_mid, dose_high,
                                  dose_unit, typical_frequency, notes, source, card_class, category,
                                  evidence_level, status, card_details, card_image)
        SELECT id, name, aliases, card_number, dose_low, dose_mid, dose_high,
               dose_unit, typical_frequency, notes, source, card_class, category,
               evidence_level, status, card_details, card_image FROM peptides
    """))
    bind.execute(text("DROP TABLE peptides"))
    bind.execute(text("ALTER TABLE peptides_new RENAME TO peptides"))
