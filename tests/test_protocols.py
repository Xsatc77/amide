import html
from datetime import date

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models import (
    DoseUnit, Frequency, InventoryItem, Peptide, Protocol, ProtocolGoal, ProtocolItem, Route, TimeOfDay,
    TitrationStep,
)
from app.routers.protocols import get_today

TODAY = date(2026, 9, 22)


@pytest.fixture(autouse=True)
def fixed_today():
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)


def peptide_id(db, name: str) -> int:
    return db.scalar(select(Peptide.id).where(Peptide.name == name))


def make_protocol(db, name="Heal", start=date(2026, 9, 1), goals=("muscle-recovery",), **kw) -> Protocol:
    p = Protocol(name=name, start_date=start, **kw)
    p.goals = [ProtocolGoal(goal=g) for g in goals]
    p.items = [ProtocolItem(peptide_id=peptide_id(db, "BPC-157"), position=0, dose=250, dose_unit=DoseUnit.MCG,
                            frequency=Frequency.DAILY, time_of_day=TimeOfDay.AM, route=Route.SUBQ)]
    db.add(p)
    db.commit()
    return p


def page(client) -> str:
    r = client.get("/protocols")
    assert r.status_code == 200
    return html.unescape(r.text)


def sections(text: str) -> tuple[str, str]:
    """(active cards section, saved protocols section)."""
    active, _, rest = text.partition('id="goal-cards"')
    _, _, saved = rest.partition('id="saved-protocols"')
    return active, saved


# ---------------------------------------------------------------- page

def test_page_sections_and_goal_cards(client):
    text = page(client)
    for label in ("Fat Loss / Metabolic", "Muscle & Recovery", "Growth Hormone / Performance",
                  "Longevity & Cellular Health", "Skin & Beauty", "Wellness / General Health",
                  "GLP-1 / Weight Management", "Sleep & Recovery"):
        assert label in text
    assert "No active protocols" in text
    assert 'href="/protocols"' in text  # nav link is live


def test_active_card_has_ribbon_and_details(client, db):
    make_protocol(db)
    active, _ = sections(page(client))
    assert 'class="ribbon"' in active
    assert "Heal" in active
    assert "BPC-157" in active and "250 mcg · Daily · AM · SubQ" in active
    assert "Day 22" in active and "Ongoing" in active
    assert 'disabled title="Email sharing coming soon"' in active


def test_card_shows_current_titration_step(client, db):
    p = make_protocol(db, titration_enabled=True)
    p.items[0].steps = [TitrationStep(start_week=1, end_week=2, dose=100), TitrationStep(start_week=3, dose=250)]
    db.commit()
    active, _ = sections(page(client))
    assert "Week 4 · step 2: 250 mcg" in active


def test_status_grouping(client, db):
    make_protocol(db, name="Alpha")
    make_protocol(db, name="Sched", start=date(2026, 10, 1))
    make_protocol(db, name="Paws", paused=True)
    make_protocol(db, name="Done", ended_on=date(2026, 9, 20))
    make_protocol(db, name="Lapsed", end_date=date(2026, 9, 21))
    active, saved = sections(page(client))
    assert "Alpha" in active
    for name in ("Sched", "Paws", "Done", "Lapsed"):
        assert name not in active and name in saved
    assert saved.count('data-status="ended"') == 2
    assert 'data-status="scheduled"' in saved and 'data-status="paused"' in saved
    assert "Alpha" not in saved


# ---------------------------------------------------------------- actions

def test_pause_resume_end_delete(client, db):
    p = make_protocol(db)
    pid = p.id

    assert client.post(f"/protocols/{pid}/pause", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Protocol, pid).paused is True

    client.post(f"/protocols/{pid}/resume")
    db.expire_all()
    assert db.get(Protocol, pid).paused is False

    client.post(f"/protocols/{pid}/end")
    db.expire_all()
    assert db.get(Protocol, pid).ended_on == TODAY

    r = client.post(f"/protocols/{pid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/protocols"
    db.expire_all()
    assert db.get(Protocol, pid) is None
    assert db.scalar(select(func.count()).select_from(ProtocolItem)) == 0


def test_action_can_return_to_edit_page(client, db):
    p = make_protocol(db)
    r = client.post(f"/protocols/{p.id}/pause", data={"next": "edit"}, follow_redirects=False)
    assert r.headers["location"] == f"/protocols/{p.id}/edit"


def test_actions_404_for_missing_protocol(client):
    assert client.post("/protocols/9999/pause").status_code == 404


def test_inventory_delete_unlinks_protocol_item(client, db):
    inv = InventoryItem(name="BPC vial", count=2)
    db.add(inv)
    db.commit()
    p = make_protocol(db)
    p.items[0].inventory_item_id = inv.id
    db.commit()
    item_id = p.items[0].id

    client.post(f"/inventory/{inv.id}/delete")
    db.expire_all()
    item = db.get(ProtocolItem, item_id)
    assert item is not None and item.inventory_item_id is None


# ---------------------------------------------------------------- API

def test_api_shapes(client, db):
    p = make_protocol(db, titration_enabled=False)
    p.items[0].steps = [TitrationStep(start_week=1, dose=100)]
    db.commit()

    [row] = client.get("/api/protocols").json()
    assert row["status"] == "active" and row["goals"] == ["muscle-recovery"]
    assert row["items"][0]["peptide"] == "BPC-157" and row["items"][0]["dose_unit"] == "mcg"
    assert row["items"][0]["steps"] == []  # titration off

    p.titration_enabled = True
    db.commit()
    one = client.get(f"/api/protocols/{p.id}").json()
    assert one["items"][0]["steps"] == [{"start_week": 1, "end_week": None, "dose": 100.0}]
    assert client.get("/api/protocols/9999").status_code == 404

    peptides = client.get("/api/peptides").json()
    assert len(peptides) >= 105
    assert {"id", "name", "card_number", "source"} <= set(peptides[0])
