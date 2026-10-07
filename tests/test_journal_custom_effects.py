"""Side effects you add yourself: a reusable list of your own, ticked per day like the built-in ones."""

import html
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import JournalCustomEffect, JournalEntry, JournalEntryCustomEffect
from photo_helpers import other_client

TODAY = date.today()


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(JournalEntry).filter(JournalEntry.owner_id == me).delete()
            s.query(JournalCustomEffect).filter(JournalCustomEffect.owner_id == me).delete()
            s.commit()
    wipe()
    yield
    wipe()


def mine(me):
    with SessionLocal() as s:
        return sorted(e.name for e in s.scalars(select(JournalCustomEffect).where(JournalCustomEffect.owner_id == me)))


def chosen(me, day=TODAY):
    with SessionLocal() as s:
        entry = s.scalar(select(JournalEntry).where(JournalEntry.owner_id == me, JournalEntry.entry_date == day))
        return sorted(c.name for c in entry.custom_effects) if entry else None


def test_a_new_side_effect_is_added_to_my_list_and_ticked_for_that_day(client, db, me):
    r = client.post("/journal/entries", data={"mood": "3", "new_custom_effect": "Metallic taste"}, follow_redirects=False)
    assert r.status_code == 303 and mine(me) == ["Metallic taste"] and chosen(me) == ["Metallic taste"]


def test_it_is_offered_again_on_later_days_and_can_be_ticked_without_retyping(client, db, me):
    client.post("/journal/entries", data={"new_custom_effect": "Metallic taste"})
    page = html.unescape(client.get("/measurements", params={"tab": "journal", "edit": (TODAY - timedelta(days=1)).isoformat()}).text)
    assert 'name="custom_effects" value="Metallic taste"' in page
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=1)).isoformat(), "custom_effects": "Metallic taste"})
    assert chosen(me, TODAY - timedelta(days=1)) == ["Metallic taste"]
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=1)).isoformat(), "mood": "4"})            # unticked on re-save
    assert chosen(me, TODAY - timedelta(days=1)) == []


def test_the_entries_table_shows_the_custom_effects_with_the_built_in_ones(client, db, me):
    client.post("/journal/entries", data={"side_effects": "Nausea", "custom_effects": "", "new_custom_effect": "Metallic taste"})
    page = html.unescape(client.get("/measurements", params={"tab": "journal"}).text)
    cell = page.split('data-label="Side effects"')[1].split("</td>")[0]
    assert "Nausea" in cell and "Metallic taste" in cell


def test_a_name_is_trimmed_not_repeated_and_limited(client, db, me):
    client.post("/journal/entries", data={"new_custom_effect": "  Dry mouth  "})
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=1)).isoformat(), "new_custom_effect": "dry mouth"})          # same name, any case
    assert mine(me) == ["Dry mouth"]
    assert client.post("/journal/entries", data={"new_custom_effect": "x" * 41}).status_code == 422
    assert client.post("/journal/entries", data={"custom_effects": "Not on my list"}).status_code == 422
    assert mine(me) == ["Dry mouth"]


def test_the_list_is_capped_at_thirty(client, db, me):
    with SessionLocal() as s:
        s.add_all([JournalCustomEffect(owner_id=me, name=f"Effect {n}") for n in range(30)])
        s.commit()
    assert client.post("/journal/entries", data={"new_custom_effect": "One too many"}).status_code == 422


def test_removing_one_from_the_list_keeps_what_past_days_recorded(client, db, me):
    client.post("/journal/entries", data={"new_custom_effect": "Metallic taste"})
    with SessionLocal() as s:
        effect_id = s.scalar(select(JournalCustomEffect.id).where(JournalCustomEffect.owner_id == me))
    r = client.post(f"/journal/custom-effects/{effect_id}/delete", follow_redirects=False)
    assert r.status_code == 303 and mine(me) == [] and chosen(me) == ["Metallic taste"]


def test_the_list_is_private_and_only_the_owner_can_remove_from_it(client, db, me):
    client.post("/journal/entries", data={"new_custom_effect": "Metallic taste"})
    with SessionLocal() as s:
        effect_id = s.scalar(select(JournalCustomEffect.id).where(JournalCustomEffect.owner_id == me))
    with other_client("effectother") as member:
        assert "Metallic taste" not in member.get("/measurements", params={"tab": "journal"}).text
        assert member.post(f"/journal/custom-effects/{effect_id}/delete").status_code == 404
    assert mine(me) == ["Metallic taste"]
