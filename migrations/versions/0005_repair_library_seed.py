"""repair library seed

A database can have the 0003 tables without its seed rows (it was migrated while 0003 was still being
written). Add any missing card/starter peptides and, if there are no goal stacks at all, the starter
stacks. Peptides the owner already added under the same name are adopted (given their card number), so
anything pointing at them keeps working. On a complete database this does nothing.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22
"""
import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seed_data():
    """The seed lists live in 0003; load that module by path (its filename starts with a digit)."""
    path = next(Path(__file__).parent.glob("0003_*.py"))
    spec = importlib.util.spec_from_file_location("_seed_0003", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CARD_PEPTIDES, module.STARTER_PEPTIDES, module.GOAL_STACKS


def upgrade() -> None:
    card_names, starter_names, stacks = _seed_data()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, card_number FROM peptides")).all()
    by_name = {name.lower(): (pid, number) for pid, name, number in rows}
    numbers = {number for _, _, number in rows if number is not None}

    for i, name in enumerate(card_names):
        number = i + 1
        if number in numbers:
            continue
        existing = by_name.get(name.lower())
        if existing and existing[1] is None:
            conn.execute(sa.text("UPDATE peptides SET card_number = :n, source = 'card' WHERE id = :id"),
                         {"n": number, "id": existing[0]})
        elif not existing:
            conn.execute(sa.text("INSERT INTO peptides (name, card_number, source) VALUES (:name, :n, 'card')"),
                         {"name": name, "n": number})
    for name in starter_names:
        if name.lower() not in by_name:
            conn.execute(sa.text("INSERT INTO peptides (name, source) VALUES (:name, 'starter')"), {"name": name})

    if not conn.execute(sa.text("SELECT COUNT(*) FROM goal_peptides")).scalar():
        ids = {name.lower(): pid for pid, name in conn.execute(sa.text("SELECT id, name FROM peptides")).all()}
        for goal, names in stacks.items():
            for position, name in enumerate(names):
                conn.execute(sa.text("INSERT INTO goal_peptides (goal, peptide_id, position) VALUES (:g, :p, :pos)"),
                             {"g": goal, "p": ids[name.lower()], "pos": position})


def downgrade() -> None:
    pass  # data repair only; nothing to undo
