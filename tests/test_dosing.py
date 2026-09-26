import html
from datetime import date

from app.calendar.schedule import DueItem, Occurrence, missed_items
from app.dosing.site import eligible_sites, recommend
from app.models import InjectionSite, Route


def _item(protocol_item_id=1, route="subq"):
    return DueItem(peptide="Retatrutide", peptide_id=1, protocol_item_id=protocol_item_id, dose=2.0,
                  unit="mg", step=None, time_of_day=None, route=route, inventory=None)


def test_due_item_carries_protocol_item_id():
    item = _item(protocol_item_id=42)
    assert item.protocol_item_id == 42


def test_missed_items_excludes_today_and_logged():
    occ_past_logged = Occurrence(date(2026, 9, 20), 1, "Fat Loss", [_item(protocol_item_id=1)])
    occ_past_unlogged = Occurrence(date(2026, 9, 21), 1, "Fat Loss", [_item(protocol_item_id=2)])
    occ_today = Occurrence(date(2026, 9, 25), 1, "Fat Loss", [_item(protocol_item_id=3)])
    logged = {(1, date(2026, 9, 20))}
    result = missed_items([occ_past_logged, occ_past_unlogged, occ_today], logged, today=date(2026, 9, 25))
    assert len(result) == 1
    occ, item = result[0]
    assert occ.date == date(2026, 9, 21) and item.protocol_item_id == 2


def test_eligible_sites_subq_excludes_glute():
    sites = eligible_sites("subq")
    assert InjectionSite.GLUTE_L not in sites and InjectionSite.GLUTE_R not in sites
    assert InjectionSite.ABDOMEN_L in sites


def test_eligible_sites_im_includes_glute():
    sites = eligible_sites("im")
    assert InjectionSite.GLUTE_L in sites


def test_eligible_sites_non_injection_route_is_empty():
    assert eligible_sites("oral") == []


def test_recommend_mirrors_opposite_side_same_body_part():
    assert recommend(InjectionSite.ABDOMEN_L, "subq") == InjectionSite.ABDOMEN_R
    assert recommend(InjectionSite.THIGH_R, "subq") == InjectionSite.THIGH_L


def test_recommend_none_when_no_prior_site():
    assert recommend(None, "subq") is None


def test_recommend_never_crosses_body_parts():
    # Even though Glute isn't eligible for subq, recommend() must never suggest a DIFFERENT body
    # part just because the mirrored one isn't available -- it returns None in that case, not a guess.
    assert recommend(InjectionSite.GLUTE_L, "subq") is None


# ---------------------------------------------------------------- Today view + log/skip routes

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, Category, DoseLog, DoseStatus, DoseUnit, Frequency, InventoryItem, Medium, Peptide, Protocol, ProtocolItem, Route, TimeOfDay, User


def _setup_protocol_with_vial(client, db, dose=2.0, quantity=2):
    """Module-level helper -- `Frequency` etc. must be imported at this file's top level (above),
    not just inside the test functions that call it; a callee doesn't see a caller's local
    imports."""
    with SessionLocal() as s:
        # A bare select(User.id) picks whichever user row the DB returns first, which is only ever
        # "Tester" when this file runs in isolation -- other test modules register additional users
        # (e.g. test_active_vials.py's "AVShared") that are never cleaned up (conftest's `clean`
        # fixture doesn't delete User rows), so in the full suite this must be pinned to the actual
        # signed-in test user, matching conftest.py's own `me` fixture lookup.
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        # migrations/versions/0003_protocols.py seeds a "Retatrutide" Peptide row already (part of
        # the built-in peptide list), and Peptide.name is unique -- reuse it instead of colliding
        # with a duplicate insert (see tests/test_active_vials.py's `Retatrutide DoseLog Test` for
        # the sibling workaround of using a distinct name; here we keep the brief's exact name
        # "Retatrutide" for its assertions by fetching the existing row instead).
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0,
                          dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5, date_mixed=date.today(),
                          discard_by=date(2099, 1, 1), volume_remaining_ml=2.0)
        s.add(vial)
        protocol = Protocol(name="Fat Loss", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=dose, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item.id)
        s.add(pitem)
        s.commit()
        return protocol.id, pitem.id, vial.id


def test_today_page_lists_due_items(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    t = html.unescape(client.get("/today").text)
    assert "Retatrutide" in t
    assert 'data-action="log-dose"' in t
    assert 'data-action="skip-dose"' in t


def test_log_dose_depletes_vial_and_creates_dose_log(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=2.0)
    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        assert vial.volume_remaining_ml == pytest.approx(2.0 - 0.4)  # 2mg dose / 5mg/mL concentration = 0.4mL
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.ON_TIME and log.peptide_name == "Retatrutide"
        assert log.volume_ml == pytest.approx(0.4)
        assert log.active_vial_id == vial_id


def test_skip_dose_creates_skipped_log_no_vial_change(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    r = client.post("/today/skip", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 2.0  # untouched
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.SKIPPED and log.volume_ml is None


def test_log_dose_picks_oldest_open_vial_when_two_exist(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, old_vial_id = _setup_protocol_with_vial(client, db)
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        newer_vial = ActiveVial(owner_id=uid, inventory_item_id=item_id, concentration_mg_ml=5.0, water_ml=2.0,
                                dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5, date_mixed=date.today(),
                                discard_by=date(2099, 6, 1), volume_remaining_ml=2.0)  # later discard_by
        s.add(newer_vial)
        s.commit()
        newer_vial_id = newer_vial.id

    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    with SessionLocal() as s:
        assert s.get(ActiveVial, old_vial_id).volume_remaining_ml < 2.0  # the older-discard_by one was drawn from
        assert s.get(ActiveVial, newer_vial_id).volume_remaining_ml == 2.0  # untouched


def test_log_dose_requires_ownership(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "DosingOther", "password": "DosingOther1!", "confirm": "DosingOther1!"})
    r = other.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    })
    assert r.status_code == 404
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 2.0


def test_today_page_shows_site_picker_for_subq_route(client, db):
    from app.models import Frequency
    _setup_protocol_with_vial(client, db)
    t = html.unescape(client.get("/today").text)
    assert 'data-injection-site-picker' in t
    # The SVG dialog is one shared, static element with all 8 sites always present as
    # data-site circles (JS paints eligibility at runtime) -- eligibility itself is only
    # expressed in the per-item site_data JSON blob, so assert there instead of on markup.
    assert '"value": "abdomen_l"' in t or '&#34;value&#34;: &#34;abdomen_l&#34;' in t
    assert '"value": "glute_l"' not in t and '&#34;value&#34;: &#34;glute_l&#34;' not in t  # SubQ excludes Glute


def test_log_dose_with_explicit_site_records_it(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "abdomen_r",
    }, follow_redirects=False)
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.injection_site.value == "abdomen_r"


def test_second_log_recommends_mirrored_site(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, quantity=4)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "abdomen_l",
    }, follow_redirects=False)
    t = html.unescape(client.get("/today").text)
    # A second occurrence isn't due again today for a DAILY item, so instead directly check the
    # recommendation surfaces via the API the JS reads -- assert the recommended site is embedded
    # in the page's site data for this peptide.
    assert '"recommended": "abdomen_r"' in t or '&#34;recommended&#34;: &#34;abdomen_r&#34;' in t
