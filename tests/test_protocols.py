import html
from datetime import date

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models import (
    DoseUnit, Frequency, InventoryItem, Peptide, Protocol, ProtocolGoal, ProtocolItem, Route, TimeOfDay,
    TitrationStep, User,
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


def tester_id(db) -> int:
    return db.scalar(select(User.id).where(User.username_key == "tester"))


def make_protocol(db, name="Heal", start=date(2026, 9, 1), goals=("muscle-recovery",), **kw) -> Protocol:
    """A protocol owned by the signed-in test user."""
    p = Protocol(name=name, start_date=start, owner_id=tester_id(db), **kw)
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
    assert 'class="ribbon ribbon-active"' in active
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
    """Active, Paused, and Scheduled protocols all get a card in the Active Protocols section
    (each its own banner); only Ended protocols drop to Saved."""
    make_protocol(db, name="Alpha")
    make_protocol(db, name="Sched", start=date(2026, 10, 1))
    make_protocol(db, name="Paws", paused=True)
    make_protocol(db, name="Done", ended_on=date(2026, 9, 20))
    make_protocol(db, name="Lapsed", end_date=date(2026, 9, 21))
    active, saved = sections(page(client))
    for name in ("Alpha", "Sched", "Paws"):
        assert name in active and name not in saved
    for name in ("Done", "Lapsed"):
        assert name in saved and name not in active
    assert saved.count('data-status="ended"') == 2
    assert 'data-status="scheduled"' not in saved and 'data-status="paused"' not in saved
    assert 'data-status="active"' in active and 'class="ribbon ribbon-active"' in active
    assert 'data-status="scheduled"' in active and 'class="ribbon ribbon-scheduled"' in active
    assert 'data-status="paused"' in active and 'class="ribbon ribbon-paused"' in active


def test_active_section_ordering_is_active_then_paused_then_scheduled(client, db):
    make_protocol(db, name="Sched", start=date(2026, 10, 1))
    make_protocol(db, name="Paws", paused=True)
    make_protocol(db, name="Alpha")
    active, _ = sections(page(client))
    assert active.index("Alpha") < active.index("Paws") < active.index("Sched")


def test_paused_card_shows_resume_not_pause(client, db):
    make_protocol(db, name="Paws", paused=True)
    active, _ = sections(page(client))
    card = active[active.index('data-status="paused"'):]
    assert 'action="/protocols/' in card and '/resume"' in card
    assert '/pause"' not in card.split('/resume"')[0]


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
    inv = InventoryItem(name="BPC vial", count=2, owner_id=tester_id(db))
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


# ---------------------------------------------------------------- builder

import json  # noqa: E402
import re  # noqa: E402

from app.protocols.forms import state_from_form  # noqa: E402


def builder_data(text: str) -> dict:
    m = re.search(r'<script type="application/json" id="builder-data">(.*?)</script>', text, re.S)
    assert m, "builder-data script missing"
    return json.loads(m.group(1))


def form_action(text: str) -> str:
    return re.search(r'<form id="builder-form"[^>]*action="([^"]+)"', text).group(1)


def as_form(pairs: dict) -> dict[str, list[str]]:
    return {k: (v if isinstance(v, list) else [v]) for k, v in pairs.items()}


def valid_form(db, **overrides) -> dict:
    fields = {
        "name": "Recomp", "start_date": "2026-09-20", "weeks": "8", "titration": "1",
        "goal": ["fat-loss", "muscle-recovery"],
        "items-0-peptide_id": str(peptide_id(db, "Retatrutide")), "items-0-dose": "2", "items-0-dose_unit": "mg",
        "items-0-frequency": "weekly", "items-0-route": "subq",
        "items-0-steps-0-start_week": "1", "items-0-steps-0-end_week": "4", "items-0-steps-0-dose": "2",
        "items-0-steps-1-start_week": "5", "items-0-steps-1-dose": "4",
        "items-1-peptide_id": str(peptide_id(db, "BPC-157")), "items-1-dose": "250", "items-1-dose_unit": "mcg",
        "items-1-frequency": "weekdays", "items-1-weekdays": ["M", "W", "F"], "items-1-time_of_day": "am",
    }
    return {**fields, **overrides}


def test_new_builder_embeds_merged_suggestions(client, db):
    r = client.get("/protocols/new?goal=fat-loss&goal=glp1-weight&goal=bogus")
    assert r.status_code == 200
    data = builder_data(r.text)
    assert data["state"]["goals"] == ["fat-loss", "glp1-weight"]
    assert data["state"]["items"] == [] and data["is_new"] is True
    assert data["stacks"]["fat-loss"][0] == peptide_id(db, "Retatrutide")
    assert set(data["stacks"]) == {g for g in data["stacks"]} and len(data["stacks"]) == 8
    assert len(data["peptides"]) >= 105 and len(data["goals"]) == 8
    assert form_action(r.text) == "/protocols"


def test_create_protocol(client, db):
    r = client.post("/protocols", data=valid_form(db), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/protocols"

    p = db.scalar(select(Protocol).where(Protocol.name == "Recomp"))
    assert p.goal_slugs == ["fat-loss", "muscle-recovery"]
    assert p.end_date == date(2026, 11, 14) and p.titration_enabled
    assert [it.peptide.name for it in p.items] == ["Retatrutide", "BPC-157"]
    assert [(s.start_week, s.end_week, s.dose) for s in p.items[0].steps] == [(1, 4, 2.0), (5, None, 4.0)]
    assert p.items[1].weekdays == "MWF" and p.items[1].dose_unit is DoseUnit.MCG

    active, _ = sections(page(client))
    assert "Recomp" in active and "Week 1 · step 1: 2 mg" in active


def test_invalid_submit_preserves_state(client, db):
    posted = valid_form(db, name="", **{"items-0-steps-1-dose": "abc"})
    r = client.post("/protocols", data=posted)
    assert r.status_code == 422
    data = builder_data(r.text)
    assert data["state"] == state_from_form(as_form(posted))
    assert "name" in data["errors"] and "items-0-steps-1-dose" in data["errors"]
    assert db.scalar(select(func.count()).select_from(Protocol)) == 0


def test_custom_peptide_reuses_existing_case_insensitive(client, db):
    before = db.scalar(select(func.count()).select_from(Peptide))
    form = valid_form(db, **{"items-1-peptide_id": "", "items-1-new_name": "  bpc-157 "})
    assert client.post("/protocols", data=form, follow_redirects=False).status_code == 303
    p = db.scalar(select(Protocol).where(Protocol.name == "Recomp"))
    assert p.items[1].peptide_id == peptide_id(db, "BPC-157")
    assert db.scalar(select(func.count()).select_from(Peptide)) == before

    form = valid_form(db, name="Second", **{"items-1-peptide_id": "", "items-1-new_name": "Brand New"})
    client.post("/protocols", data=form)
    db.expire_all()
    new = db.scalar(select(Peptide).where(Peptide.name == "Brand New"))
    assert new is not None and new.source.value == "custom"
    assert db.scalar(select(func.count()).select_from(Peptide)) == before + 1


def test_edit_protocol(client, db):
    p = make_protocol(db)
    r = client.get(f"/protocols/{p.id}/edit")
    data = builder_data(r.text)
    assert data["is_new"] is False and data["state"]["name"] == "Heal"
    assert data["state"]["items"][0]["dose"] == "250" and data["state"]["items"][0]["dose_unit"] == "mcg"
    assert form_action(r.text) == f"/protocols/{p.id}"
    assert 'disabled title="Email sharing coming soon"' in r.text

    r = client.post(f"/protocols/{p.id}", data=valid_form(db, name="Heal v2"), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    p = db.get(Protocol, p.id)
    assert p.name == "Heal v2"
    # Items were replaced by the submitted ones (SQLite may reuse ids, so compare content).
    assert [(it.peptide.name, it.dose) for it in p.items] == [("Retatrutide", 2.0), ("BPC-157", 250.0)]
    assert db.scalar(select(func.count()).select_from(ProtocolItem)) == 2


def test_edit_missing_protocol_404(client):
    assert client.get("/protocols/9999/edit").status_code == 404
    assert client.get("/protocols/9999/repeat").status_code == 404


def test_repeat_prefills_and_does_not_save(client, db):
    p = make_protocol(db, name="Cycle", start=date(2026, 6, 1), end_date=date(2026, 7, 26),
                      ended_on=date(2026, 7, 26))
    r = client.get(f"/protocols/{p.id}/repeat")
    assert r.status_code == 200
    state = builder_data(r.text)["state"]
    assert state["name"] == "Cycle (repeat)"
    assert state["start_date"] == "2026-09-22" and state["end_date"] == "2026-11-16"
    assert state["items"][0]["peptide_id"] == str(peptide_id(db, "BPC-157"))
    assert form_action(r.text) == "/protocols"
    assert db.scalar(select(func.count()).select_from(Protocol)) == 1


def test_builder_autocomplete_uses_whole_library(client, db):
    """The add-peptide search draws from the library (names, aliases, class), not from inventory."""
    bpc = db.scalar(select(Peptide).where(Peptide.name == "BPC-157"))
    bpc.aliases = "Body Protection Compound"
    db.commit()
    data = builder_data(client.get("/protocols/new").text)
    assert data["inventory"] == []  # nothing in inventory...
    row = next(p for p in data["peptides"] if p["name"] == "BPC-157")
    assert row["aliases"] == "Body Protection Compound" and "card_class" in row  # ...yet all peptides searchable
    assert len(data["peptides"]) >= 105
    page_html = client.get("/protocols/new").text
    assert 'id="b-add-list"' in page_html and 'role="combobox"' in page_html
    bpc.aliases = None
    db.commit()
