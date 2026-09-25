import pytest
from sqlalchemy.orm import Session

from app.db import make_engine
from app.inventory.rules import FIELD_LABELS, required_fields_for
from app.inventory.vendors import resolve_vendor
from app.migrate import upgrade_db
from app.models import Medium, User, Vendor


@pytest.mark.parametrize("medium,expected", [
    (None, set()),
    (Medium.LYOPHILIZED, {"vial_size_mg"}),
    (Medium.LIQUID, {"vial_size_mg", "volume_ml"}),
    (Medium.AUTOINJECTOR, {"units_per_package"}),
    (Medium.PILL, {"vial_size_mg", "units_per_package"}),
    (Medium.INHALER, {"vial_size_mg"}),
    (Medium.DROPS, {"vial_size_mg"}),
    (Medium.SALVE, {"vial_size_mg"}),
])
def test_required_fields_per_medium(medium, expected):
    assert required_fields_for(medium) == expected


def test_field_labels_exist_for_every_required_field():
    for medium in Medium:
        for field in required_fields_for(medium):
            assert field in FIELD_LABELS


@pytest.fixture
def s(tmp_path):
    url = f"sqlite:///{(tmp_path / 'vendors.db').as_posix()}"
    upgrade_db(url)
    engine = make_engine(url)
    with Session(engine) as session:
        u1 = User(username="A", username_key="a", password_hash="x")
        u2 = User(username="B", username_key="b", password_hash="x")
        session.add_all([u1, u2])
        session.commit()
        yield session, u1.id, u2.id
    engine.dispose()


def test_resolve_vendor_creates_new(s):
    session, uid, _ = s
    v = resolve_vendor(session, uid, "Acme Peptides")
    assert v is not None and v.name == "Acme Peptides" and v.created_by_id == uid
    assert session.query(Vendor).count() == 1


def test_resolve_vendor_reuses_case_insensitively(s):
    session, uid, _ = s
    first = resolve_vendor(session, uid, "Acme Peptides")
    second = resolve_vendor(session, uid, "  acme peptides  ")
    assert second.id == first.id
    assert session.query(Vendor).count() == 1


def test_resolve_vendor_blank_returns_none(s):
    session, uid, _ = s
    assert resolve_vendor(session, uid, "") is None
    assert resolve_vendor(session, uid, "   ") is None
    assert session.query(Vendor).count() == 0


def test_resolve_vendor_shared_across_owners(s):
    """Vendors are a shared reference list (like the peptide library), not scoped per owner --
    what a user buys from a vendor is what stays private (via Inventory sharing), not the vendor itself."""
    session, uid1, uid2 = s
    v1 = resolve_vendor(session, uid1, "Acme Peptides")
    v2 = resolve_vendor(session, uid2, "Acme Peptides")
    assert v2.id == v1.id
    assert v2.created_by_id == uid1  # creator is whoever added it first; reuse doesn't reassign it
    assert session.query(Vendor).count() == 1
