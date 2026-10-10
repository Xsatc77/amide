"""Deleting a library entry a person added: admin only, never a shipped card, and never one that a protocol or dose log still uses."""

from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideDosingTier, DosingTierLevel, PeptideSource, Protocol, ProtocolItem, Route
from photo_helpers import other_client


@pytest.fixture
def entries():
    with SessionLocal() as s:
        mine = Peptide(name="Deltest Mine", source=PeptideSource.CUSTOM)
        mine.dosing_tiers = [PeptideDosingTier(level=DosingTierLevel.BEGINNER, dose_text="1 mg", frequency_text="daily")]
        shipped = Peptide(name="Deltest Shipped", source=PeptideSource.SHEET, summary="a shipped card")
        used = Peptide(name="Deltest Used", source=PeptideSource.CUSTOM)
        s.add_all([mine, shipped, used])
        s.commit()
        ids = {"mine": mine.id, "shipped": shipped.id, "used": used.id}
    yield ids
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name == "Deltest Protocol").delete()
        s.query(Peptide).filter(Peptide.name.like("Deltest %")).delete(synchronize_session=False)
        s.commit()


def test_the_admin_can_delete_an_entry_a_person_added_and_its_rows_go_with_it(client, entries):
    page = client.get(f"/library/{entries['mine']}").text
    assert f'action="/library/{entries["mine"]}/delete"' in page and "data-confirm" in page
    r = client.post(f"/library/{entries['mine']}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/library"
    with SessionLocal() as s:
        assert s.get(Peptide, entries["mine"]) is None
        assert s.scalar(select(PeptideDosingTier).where(PeptideDosingTier.peptide_id == entries["mine"])) is None


def test_a_shipped_card_has_no_delete_button_and_the_route_refuses(client, entries):
    assert f'action="/library/{entries["shipped"]}/delete"' not in client.get(f"/library/{entries['shipped']}").text
    assert client.post(f"/library/{entries['shipped']}/delete", follow_redirects=False).status_code == 403
    with SessionLocal() as s:
        assert s.get(Peptide, entries["shipped"]) is not None


def test_an_entry_a_protocol_uses_is_kept_with_an_explanation(client, entries, me):
    with SessionLocal() as s:
        p = Protocol(name="Deltest Protocol", start_date=date.today(), owner_id=me)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=entries["used"], dose=1, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, position=0))
        s.commit()
    r = client.post(f"/library/{entries['used']}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?delete_blocked=1")
    page = client.get(f"/library/{entries['used']}?delete_blocked=1").text
    assert "was not deleted" in page and "Deltest Protocol" in page
    with SessionLocal() as s:
        assert s.get(Peptide, entries["used"]) is not None


def test_someone_who_is_not_the_admin_cannot_delete_and_a_missing_entry_is_a_404(client, entries):
    with other_client("delnotadmin") as member:
        assert member.post(f"/library/{entries['mine']}/delete", follow_redirects=False).status_code == 403
        assert f'action="/library/{entries["mine"]}/delete"' not in member.get(f"/library/{entries['mine']}").text
    with SessionLocal() as s:
        assert s.get(Peptide, entries["mine"]) is not None
    assert client.post("/library/999999/delete", follow_redirects=False).status_code == 404
