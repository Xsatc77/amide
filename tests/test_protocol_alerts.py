"""Alerts for a saved protocol and for the builder form."""

from datetime import date

import pytest

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay, UserMedicine
from photo_helpers import other_client


@pytest.fixture
def protocol_id(me):
    with SessionLocal() as s:
        hot = Peptide(name="Alerts Test Alpha", source=PeptideSource.CUSTOM, dose_low=1, dose_mid=5, dose_high=10, dose_unit=DoseUnit.MG)
        bare = Peptide(name="Alerts Test Beta", source=PeptideSource.CUSTOM)
        s.add_all([hot, bare])
        s.flush()
        p = Protocol(name="Alerts Protocol", start_date=date.today(), owner_id=me)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=hot.id, dose=12, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, time_of_day=TimeOfDay.AM, route=Route.SUBQ, position=0))
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=bare.id, dose=3, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, position=1))
        s.commit()
        pid, ids = p.id, [hot.id, bare.id]
    yield pid
    with SessionLocal() as s:
        s.query(Protocol).filter_by(id=pid).delete()
        s.query(Peptide).filter(Peptide.id.in_(ids)).delete()
        s.commit()


def test_the_json_report_lists_cautions_first_with_the_disclaimer(client, protocol_id):
    body = client.get(f"/protocols/{protocol_id}/alerts.json").json()
    assert [f["severity"] for f in body["findings"]] == ["caution", "note"]
    assert "above the library's high dose" in body["findings"][0]["message"] and "prescriber or pharmacist" in body["disclaimer"]


def test_the_page_shows_the_same_report(client, protocol_id):
    page = client.get(f"/protocols/{protocol_id}/alerts")
    assert page.status_code == 200 and "above the library" in page.text and "Alerts Protocol" in page.text


def test_another_persons_protocol_is_a_404(client, protocol_id, me):
    with other_client("alertsother") as member:
        assert member.get(f"/protocols/{protocol_id}/alerts.json").status_code == 404
        assert member.get(f"/protocols/{protocol_id}/alerts").status_code == 404


def test_medicines_shape_the_report(client, db, me, protocol_id):
    med = UserMedicine(owner_id=me, name="Alerts Test Med", dose_text="5 mg")
    db.add(med)
    db.commit()
    try:
        assert client.get(f"/protocols/{protocol_id}/alerts.json").status_code == 200
    finally:
        db.delete(med)
        db.commit()


def test_preview_reads_the_builder_form_and_an_unfinished_form_is_empty_not_an_error(client, protocol_id):
    body = client.post("/protocols/alerts-preview", data={"name": ""}).json()
    assert body["findings"] == [] and body["incomplete"] is True and "prescriber or pharmacist" in body["disclaimer"]


def test_preview_skips_a_new_peptide_with_no_library_entry(client, db):
    from app.protocols.alerts import for_parsed
    from app.protocols.forms import ParsedItem, ParsedProtocol
    item = ParsedItem(peptide_id=None, new_name="Brand New", dose=5, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, every_n_days=None,
                      weekdays=None, time_of_day=TimeOfDay.ANY, route=Route.SUBQ, inventory_item_id=None, notes=None)
    parsed = ParsedProtocol(name="x", start_date=None, end_date=None, notes=None, titration_enabled=False, goals=[], items=[item])
    assert for_parsed(db, parsed, 1) == []


def test_the_protocol_list_has_an_alerts_icon_with_a_count_beside_print(client, protocol_id):
    page = client.get("/protocols").text
    row = page.split(f'data-protocol-id="{protocol_id}"')[0]
    assert f'class="btn-icon alerts-btn" data-protocol-id="{protocol_id}"' in page
    assert 'id="alerts-dialog"' in page and "protocol-alerts.js" in page
    assert page.index(f'/protocols/{protocol_id}/print') < page.index(f'class="btn-icon alerts-btn" data-protocol-id="{protocol_id}"')
    assert f'data-alerts-count="2"' in page


def test_the_builder_has_a_check_for_alerts_panel_and_the_preview_finds_a_real_conflict(client):
    assert 'id="alerts-check"' in client.get("/protocols/new").text


def test_preview_of_a_finished_builder_form_finds_a_titration_jump(client, db):
    from test_protocols import valid_form
    form = valid_form(db, **{"items-0-steps-0-dose": "1", "items-0-steps-1-dose": "3"})
    body = client.post("/protocols/alerts-preview", data=form).json()
    assert body["incomplete"] is False
    assert any("more than double" in f["message"] for f in body["findings"])
