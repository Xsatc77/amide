"""What a brand-new install's library starts with: the base list, in the new-style names, with no old-name duplicates."""

import importlib.util
from pathlib import Path

SEED = next(Path(__file__).parent.parent.glob("migrations/versions/0003_*.py"))
OLD_NAMES = {"Epitalon", "Octreotide", "Leuprorelin", "Thymosin alpha-1", "Elamipretide (SS-31)", "Cibinetide", "CJC-1295", "CJC-1295 DAC",
             "PT-141 (Bremelanotide)", "Melanotan I (Afamelanotide)", "Gonadorelin (GnRH)", "Somatostatin", "Amylin", "Vasopressin"}


def seed_module():
    spec = importlib.util.spec_from_file_location("_seed_0003_test", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_base_list_uses_new_style_names_and_has_no_duplicates():
    m = seed_module()
    names = m.CARD_PEPTIDES + m.STARTER_PEPTIDES
    assert len(names) == 105 and len({n.lower() for n in names}) == 105
    assert not OLD_NAMES & set(names)
    assert {"Epithalon", "Octreotide (Sandostatin)", "Leuprolide (Lupron)", "CJC-1295 (No DAC)", "ARA-290", "SS-31 (Elamipretide)"} <= set(names)


def test_every_goal_stack_peptide_is_in_the_base_list():
    m = seed_module()
    names = set(m.CARD_PEPTIDES + m.STARTER_PEPTIDES)
    assert [n for stack in m.GOAL_STACKS.values() for n in stack if n not in names] == []
