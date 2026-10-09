"""library: old-style peptide names become the new-style names

An entry that still carries one of the 33 old names is renamed to its new-style name, or, when the new-style entry already exists and the
old one holds no card data, everything that points at the old one (goal stacks, protocols, dose logs, price-list rows, notes) is moved to the
new one and the empty old one is removed. An old-name entry that holds card data is left alone. Names only; raw SQL, so it keeps working
whatever columns later migrations add.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0058'
down_revision: Union[str, None] = '0057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TO_NEW = {
    "Amylin": "Amylin (IAPP)", "Atosiban": "Atosiban (Tractocile)", "Calcitonin": "Calcitonin (Miacalcin)",
    "Carbetocin": "Carbetocin (Duratocin/Pabal)", "Cholecystokinin (CCK)": "Cholecystokinin (CCK-8)", "Cibinetide": "ARA-290",
    "CJC-1295": "CJC-1295 (No DAC)", "CJC-1295 DAC": "CJC-1295 (DAC)", "Desmopressin": "Desmopressin (DDAVP)",
    "Dulaglutide": "Dulaglutide (Trulicity)", "Elamipretide (SS-31)": "SS-31 (Elamipretide)", "Epitalon": "Epithalon",
    "Exenatide": "Exenatide (Byetta / Bydureon)", "Glucagon": "Glucagon (GlucaGen)", "Gonadorelin (GnRH)": "Gonadorelin",
    "Icatibant": "Icatibant (Firazyr)", "IGF-1 DES (1-3)": "IGF-1 DES", "Lanreotide": "Lanreotide (Somatuline Depot)",
    "Larazotide acetate": "Larazotide (AT-1001)", "Leuprorelin": "Leuprolide (Lupron)", "Linaclotide": "Linaclotide (Linzess)",
    "Melanotan I (Afamelanotide)": "Melanotan I", "Octreotide": "Octreotide (Sandostatin)", "Pasireotide": "Pasireotide (Signifor)",
    "Plecanatide": "Plecanatide (Trulance)", "Pramlintide": "Pramlintide (Symlin)", "PT-141 (Bremelanotide)": "PT-141",
    "Somatostatin": "Somatostatin (SST-14 and SST-28)", "Teduglutide": "Teduglutide (Gattex/Revestive)",
    "Thymosin alpha-1": "Thymosin Alpha 1", "Vasopressin": "Vasopressin (Vasostrict)",
    "VIP": "VIP (Vasoactive Intestinal Peptide)", "Ziconotide": "Ziconotide (Prialt)",
}
# Rows that belong to a library entry itself: they go with it. Everything else that points at a peptide is a person's data and is moved.
CHILD_TABLES = {"peptide_cycles", "peptide_stack_relations", "peptide_dosing_tiers", "peptide_monitoring_tests"}
EMPTY = "summary IS NULL AND tags IS NULL AND card_details IS NULL AND card_class IS NULL AND sheet_sections IS NULL"


def _id(conn, name):
    return conn.execute(sa.text("SELECT id FROM peptides WHERE name = :n COLLATE NOCASE"), {"n": name}).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    tables = [r[0] for r in conn.execute(sa.text(
        "SELECT m.name FROM sqlite_master m, pragma_foreign_key_list(m.name) p WHERE m.type = 'table' AND p.\"table\" = 'peptides'")).all()]
    for old, new in OLD_TO_NEW.items():
        old_id = _id(conn, old)
        if old_id is None:
            continue
        new_id = _id(conn, new)
        if new_id is None:
            conn.execute(sa.text("UPDATE peptides SET name = :new WHERE id = :id"), {"new": new, "id": old_id})
            continue
        if not conn.execute(sa.text(f"SELECT 1 FROM peptides WHERE id = :id AND {EMPTY}"), {"id": old_id}).scalar():
            continue                                    # the old entry holds card data: leave it alone
        for table in tables:
            if table not in CHILD_TABLES:
                conn.execute(sa.text(f"UPDATE OR IGNORE {table} SET peptide_id = :new WHERE peptide_id = :old"), {"new": new_id, "old": old_id})
            conn.execute(sa.text(f"DELETE FROM {table} WHERE peptide_id = :old"), {"old": old_id})
        conn.execute(sa.text("DELETE FROM peptides WHERE id = :id"), {"id": old_id})


def downgrade() -> None:
    pass
