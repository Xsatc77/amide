import html
from datetime import date, timedelta

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


# ---------------------------------------------------------------- Protocol page dose history + catch-up

def test_protocol_page_shows_dose_history(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    t = html.unescape(client.get(f"/protocols/{protocol_id}/edit").text)
    assert "Dose history" in t
    assert "On time" in t


def test_protocol_page_shows_catch_up_for_missed_dose(client, db):
    from app.models import Frequency
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        # migrations/versions/0003_protocols.py already seeds a "Retatrutide" Peptide row (and
        # Peptide.name is unique) -- reuse it instead of colliding with a duplicate insert, same
        # as _setup_protocol_with_vial above.
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ)
        s.add(pitem)
        s.commit()
        protocol_id = protocol.id
    t = html.unescape(client.get(f"/protocols/{protocol_id}/edit").text)
    assert 'data-action="log-dose"' in t  # a catch-up log action for a long-overdue day appears


# ---------------------------------------------------------------- Calendar adherence color dots

def test_calendar_shows_on_time_adherence_dot(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "adherence-on_time" in r.text or '"on_time"' in r.text


def test_calendar_shows_missed_adherence_for_past_unlogged_day(client, db):
    from app.models import Frequency
    with SessionLocal() as s:
        # migrations/versions/0003_protocols.py already seeds a "Retatrutide" Peptide row (and
        # Peptide.name is unique) -- reuse it instead of colliding with a duplicate insert, same
        # as _setup_protocol_with_vial above.
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        s.add(ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                           frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
    r = client.get(f"/calendar?view=day&date={(date.today() - timedelta(days=1)).isoformat()}")
    assert r.status_code == 200
    assert "adherence-missed" in r.text or '"missed"' in r.text


def test_calendar_shows_week_view_missed_adherence_for_past_unlogged_day(client, db):
    from app.models import Frequency
    with SessionLocal() as s:
        # Same reuse pattern as test_calendar_shows_missed_adherence_for_past_unlogged_day above --
        # migrations/versions/0003_protocols.py already seeds a "Retatrutide" Peptide row (and
        # Peptide.name is unique), so reuse it instead of colliding with a duplicate insert.
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        s.add(ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                           frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
    r = client.get(f"/calendar?view=week&date={(date.today() - timedelta(days=1)).isoformat()}")
    assert r.status_code == 200
    assert "adherence-missed" in r.text or '"missed"' in r.text


def test_calendar_adherence_is_missed_when_one_of_two_daily_items_unlogged(client, db):
    """A protocol with two DAILY items due the same past day, one logged on time and the other
    never logged, must show 'missed' for that occurrence -- not 'on_time' just because *a* log
    exists for the day. Regression test for a review-caught bug where _adherence() grouped
    DoseLog rows by (protocol_id, scheduled_date) instead of (protocol_item_id, scheduled_date),
    letting one logged item mask a sibling item that was silently missed."""
    from app.models import Frequency
    yesterday = date.today() - timedelta(days=1)
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        # Reuse the seeded peptides from migrations/versions/0003_protocols.py -- Peptide.name is
        # unique, so fetch existing rows instead of inserting duplicates (same pattern used
        # throughout this file).
        peptide_a = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide_a is None:
            peptide_a = Peptide(name="Retatrutide")
            s.add(peptide_a)
            s.flush()
        peptide_b = s.scalar(select(Peptide).where(Peptide.name == "Tirzepatide"))
        if peptide_b is None:
            peptide_b = Peptide(name="Tirzepatide")
            s.add(peptide_b)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        item_a = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide_a.id, dose=2.0, dose_unit=DoseUnit.MG,
                              frequency=Frequency.DAILY, route=Route.SUBQ)
        item_b = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide_b.id, dose=5.0, dose_unit=DoseUnit.MG,
                              frequency=Frequency.DAILY, route=Route.SUBQ)
        s.add_all([item_a, item_b])
        s.commit()
        protocol_id, item_a_id = protocol.id, item_a.id

    # Only item_a gets logged, and logged on time; item_b is never logged for yesterday.
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide_a = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        s.add(DoseLog(owner_id=uid, protocol_id=protocol_id, protocol_item_id=item_a_id,
                      peptide_id=peptide_a.id, peptide_name="Retatrutide", dose_value=2.0,
                      dose_unit=DoseUnit.MG, route="subq", scheduled_date=yesterday,
                      scheduled_time_of_day=TimeOfDay.ANY, status=DoseStatus.ON_TIME))
        s.commit()

    r = client.get(f"/calendar?view=day&date={yesterday.isoformat()}")
    assert r.status_code == 200
    assert "adherence-missed" in r.text or '"missed"' in r.text
    assert "adherence-on_time" not in r.text and '"on_time"' not in r.text


# ---------------------------------------------------------------- Fix 1: empty-vial handling + overdraw guard

def test_open_vial_for_item_excludes_float_residual_as_empty(client, db):
    """5 real-world 0.4mL draws from a 2.0mL vial leave a residual of ~1.11e-16 mL in plain float
    arithmetic -- still > 0, but not a real amount of liquid left to draw. _open_vial_for_item must
    treat that as empty, not offer the vial for a 6th dose."""
    from app.routers.dosing import _open_vial_for_item
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=0.4)
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        residual = 2.0
        for _ in range(5):
            residual -= 0.4  # repeated subtraction, same as the app does on each real draw
        vial.volume_remaining_ml = residual
        s.commit()
        item_id = vial.inventory_item_id
        assert vial.volume_remaining_ml > 0  # sanity: the residual really is nonzero

    with SessionLocal() as s:
        assert _open_vial_for_item(s, item_id) is None


def test_log_dose_overdraw_clamps_volume_remaining_to_zero(client, db):
    """A dose whose computed volume exceeds what's left in the vial must not push
    volume_remaining_ml negative -- the dose still happened in real life even if the vial's
    tracked volume was already imprecise, so the log is still created, just clamped at 0."""
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=2.0)  # draws 0.4mL
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        vial.volume_remaining_ml = 0.1  # less than the 0.4mL this dose will draw
        s.commit()

    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 0.0
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.ON_TIME  # logged, not rejected


def test_log_dose_that_exactly_empties_vial_redirects_with_empty_vial_signal(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=2.0)  # draws 0.4mL
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        vial.volume_remaining_ml = 0.4  # exactly what this dose draws
        s.commit()

    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/today?empty_vial={vial_id}"
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 0.0

    t = html.unescape(client.get(r.headers["location"]).text)
    assert "empty" in t.lower()
    assert f"/active-vials/{vial_id}/discard" in t


# ---------------------------------------------------------------- Fix 2: protocol edits preserve DoseLog history

def test_protocol_edit_repoints_doselog_to_new_matching_item(client, db):
    """Editing a saved protocol (even a simple rename) clears and rebuilds all its ProtocolItem
    rows -- a previously-logged dose's protocol_item_id must be re-pointed at whichever NEW item
    represents the same (peptide, time_of_day), not silently left NULL (which would make Calendar,
    the catch-up list, and Today's already-logged-today check all think that day was never logged)."""
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, time_of_day=TimeOfDay.AM, route=Route.SUBQ)
        s.add(pitem)
        s.commit()
        protocol_id, pitem_id, peptide_id = protocol.id, pitem.id, peptide.id

    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.protocol_item_id == pitem_id and log.status == DoseStatus.ON_TIME
        log_id = log.id

    # Edit the protocol -- rename only, same peptide + time_of_day -- via the real HTTP route.
    r = client.post(f"/protocols/{protocol_id}", data={
        "name": "Fat Loss v2", "start_date": date.today().isoformat(), "weeks": "8",
        "goal": ["fat-loss"],
        "items-0-peptide_id": str(peptide_id), "items-0-dose": "2", "items-0-dose_unit": "mg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am", "items-0-route": "subq",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        p = s.get(Protocol, protocol_id)
        assert p.name == "Fat Loss v2"
        [new_item] = p.items
        # save_protocol always clears and rebuilds every ProtocolItem row on save -- SQLite may or
        # may not reuse the old numeric id for the replacement row (an implementation detail, not
        # something to assert on), so the meaningful check is that the log now resolves to a LIVE
        # item in the edited protocol, not that the id is numerically different from before.
        log = s.get(DoseLog, log_id)
        assert log.protocol_item_id == new_item.id  # re-pointed -- not NULL, not orphaned

    # Calendar adherence for that day must still read on_time, not missed.
    r = client.get(f"/calendar?view=day&date={date.today().isoformat()}")
    assert r.status_code == 200
    assert "adherence-on_time" in r.text or '"on_time"' in r.text
    assert "adherence-missed" not in r.text and '"missed"' not in r.text

    # The catch-up list must not resurrect the already-logged day: today's occurrence should show
    # up once, in the dose-history table (status "On time"), and not a second time as a missed/
    # catch-up row.
    t = html.unescape(client.get(f"/protocols/{protocol_id}/edit").text)
    assert "On time" in t
    assert "Log now (late)" not in t


# ---------------------------------------------------------------- Fix 3: titrated dose vs logged/drawn amount

def test_log_dose_uses_titrated_step_dose_not_base_dose(client, db):
    """A titrated protocol's due dose (shown on Today/catch-up, per _due_item()) must be the exact
    same value that gets drawn into volume_ml and stored on the DoseLog -- not the item's untitrated
    base dose."""
    from app.models import TitrationStep
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
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
        # start_date far enough back that "today" falls in titration week 3+, well past the
        # step-1 window, so the effective dose (2.5mg) clearly differs from the base dose (1mg).
        protocol = Protocol(name="Titrated", start_date=date.today() - timedelta(days=30),
                            owner_id=uid, titration_enabled=True)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=1.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item.id,
                             steps=[TitrationStep(start_week=1, end_week=2, dose=1.0),
                                   TitrationStep(start_week=3, end_week=None, dose=2.5)])
        s.add(pitem)
        s.commit()
        protocol_id, pitem_id, vial_id = protocol.id, pitem.id, vial.id

    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.dose_value == pytest.approx(2.5)  # the step's dose, not the base 1mg
        assert log.volume_ml == pytest.approx(2.5 / 5.0)  # 2.5mg / 5mg/mL
        vial = s.get(ActiveVial, vial_id)
        assert vial.volume_remaining_ml == pytest.approx(2.0 - 2.5 / 5.0)


def test_skip_dose_uses_titrated_step_dose_not_base_dose(client, db):
    """skip_dose must store the same titration-adjusted dose_value that log_dose would have --
    otherwise a skipped dose on a titrated protocol permanently records (and displays, in the
    Protocol page's dose-history table) the untitrated base dose."""
    from app.models import TitrationStep
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        # start_date far enough back that "today" falls in titration week 3+, well past the
        # step-1 window, so the effective dose (2.5mg) clearly differs from the base dose (1mg).
        protocol = Protocol(name="Titrated Skip", start_date=date.today() - timedelta(days=30),
                            owner_id=uid, titration_enabled=True)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=1.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ,
                             steps=[TitrationStep(start_week=1, end_week=2, dose=1.0),
                                   TitrationStep(start_week=3, end_week=None, dose=2.5)])
        s.add(pitem)
        s.commit()
        protocol_id, pitem_id = protocol.id, pitem.id

    r = client.post("/today/skip", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.SKIPPED
        assert log.dose_value == pytest.approx(2.5)  # the step's dose, not the base 1mg


# ---------------------------------------------------------------- Fix 5: injection site persists without a vial

def test_log_dose_with_no_vial_still_records_explicit_site(client, db):
    """An item with no linked inventory item (so `vial` stays None the whole time) must not
    silently discard an explicitly posted injection site."""
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="No Vial", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ)  # no inventory_item_id
        s.add(pitem)
        s.commit()
        protocol_id, pitem_id = protocol.id, pitem.id

    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "abdomen_r",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.active_vial_id is None  # confirms the no-vial path was exercised
        assert log.injection_site is not None and log.injection_site.value == "abdomen_r"


def test_log_dose_rejects_site_ineligible_for_route(client, db):
    """A posted site that isn't eligible for this item's route (e.g. a forged glute_l on a SubQ
    item, bypassing the UI's own filtering) must be rejected, not stored as-is."""
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)  # SubQ route
    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "glute_l",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.injection_site is None or log.injection_site.value != "glute_l"


# ---------------------------------------------------------------- Fix 6: duplicate-log guard

def test_double_posting_log_dose_creates_only_one_log_and_one_deduction(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=2.0)  # draws 0.4mL
    data = {
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }
    r1 = client.post("/today/log", data=data, follow_redirects=False)
    r2 = client.post("/today/log", data=data, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    with SessionLocal() as s:
        logs = s.scalars(select(DoseLog)).all()
        assert len(logs) == 1
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == pytest.approx(2.0 - 0.4)


def test_double_posting_skip_dose_creates_only_one_log(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    data = {
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }
    r1 = client.post("/today/skip", data=data, follow_redirects=False)
    r2 = client.post("/today/skip", data=data, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    with SessionLocal() as s:
        assert len(s.scalars(select(DoseLog)).all()) == 1


# ---------------------------------------------------------------- Fix 7: catch-up window is bounded

def test_catch_up_list_bounded_to_recent_window_for_old_protocol(client, db):
    """A protocol running since 2020 must not produce one catch-up row per missed day since then --
    the list is bounded to a recent window (see CATCH_UP_WINDOW_DAYS in app.routers.protocols)."""
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name="Ancient", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        s.add(ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                           frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
        protocol_id = protocol.id

    r = client.get(f"/protocols/{protocol_id}/edit")
    assert r.status_code == 200
    # One "Log now (late)" button per missed day -- thousands for a protocol running since 2020
    # with no bound, at most ~15 (CATCH_UP_WINDOW_DAYS + 1) with the fix.
    assert r.text.count('data-action="log-dose"') <= 16
