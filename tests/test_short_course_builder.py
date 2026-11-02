"""The short-course builder: the Annual Short Term Protocol goal brings its five suggested peptides, counts in days, gives each item its own days,
drops titration and cycle on/off, and repeats on the cadence picked."""

import html
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import GoalPeptide, Peptide, Protocol, ProtocolItem
from app.protocols.forms import parse_protocol_form
from app.routers.protocols import get_today

TODAY = date(2026, 9, 22)


@pytest.fixture(autouse=True)
def fixed_today(client, me):
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Shortbuild%")).delete(synchronize_session=False)
        s.commit()


def peptide(name: str) -> int:
    with SessionLocal() as s:
        return s.scalar(select(Peptide.id).where(Peptide.name == name))


def form(**over):
    base = {"name": ["Shortbuild A"], "start_date": ["2026-10-05"], "goal": ["annual-short-term"], "short_days": ["21"], "repeat_months": ["12"],
            "items-0-peptide_id": [str(peptide("Epithalon"))], "items-1-peptide_id": [str(peptide("Thymalin"))]}
    base.update({k: v if isinstance(v, list) else [v] for k, v in over.items()})
    return base


def parse(data):
    with SessionLocal() as s:
        return parse_protocol_form(data, peptide_ids=set(s.scalars(select(Peptide.id))), inventory_ids=set())


def test_the_goal_suggests_its_five_peptides_in_order(client):
    with SessionLocal() as s:
        names = [s.get(Peptide, g.peptide_id).name for g in s.scalars(select(GoalPeptide).where(GoalPeptide.goal == "annual-short-term").order_by(GoalPeptide.position))]
    assert names == ["Epithalon", "Thymalin", "Vesugen", "Cartalax", "FOXO4-DRI"]


def test_the_builder_page_offers_the_days_and_repeat_choices(client):
    page = html.unescape(client.get("/protocols/new?goal=annual-short-term").text)
    assert 'id="b-short"' in page and 'name="short_days"' in page
    assert page.count('name="repeat_months"') == 3
    for label in ("Annually", "Semiannually", "Quarterly"):
        assert label in page
    assert '<option value="30">30 days</option>' in page and '<option value="31"' not in page


def test_the_length_in_days_sets_the_end_date_and_every_item_defaults_to_the_whole_course():
    parsed, errors = parse(form())
    assert errors == {}
    assert (parsed.short_days, parsed.repeat_months) == (21, 12)
    assert parsed.end_date == date(2026, 10, 25)                                  # 21 days from Oct 5 counts the start day
    assert [(i.start_day, i.run_days) for i in parsed.items] == [(1, 21), (1, 21)]


def test_an_item_can_start_later_and_run_for_fewer_days():
    parsed, errors = parse(form(**{"items-1-start_day": "11", "items-1-run_days": "10", "items-0-run_days": "10"}))
    assert errors == {}
    assert [(i.start_day, i.run_days) for i in parsed.items] == [(1, 10), (11, 10)]


def test_an_item_cannot_run_past_the_end_of_the_course_or_start_after_it():
    _, errors = parse(form(**{"items-0-start_day": "15", "items-0-run_days": "10", "items-1-start_day": "22"}))
    assert "From day 15 the course has only 7 days left." in errors["items-0-run_days"]
    assert errors["items-1-start_day"] == "The course is only 21 days long."


def test_the_length_and_the_repeat_are_checked():
    assert "short_days" in parse(form(short_days=""))[1]
    assert "at most 30" in parse(form(short_days="31"))[1]["short_days"]
    assert "repeat_months" in parse(form(repeat_months="5"))[1]
    assert parse(form(repeat_months="3"))[0].repeat_months == 3


def test_titration_and_cycle_off_are_dropped_for_a_short_course():
    data = form(titration="1", **{"items-0-steps-0-start_week": "1", "items-0-steps-0-dose": "2", "items-0-cycle_offs-0-start_week": "2", "items-0-cycle_offs-0-weeks": "1"})
    parsed, errors = parse(data)
    assert errors == {} and parsed.titration_enabled is False
    assert parsed.items[0].steps == [] and parsed.items[0].cycle_offs == []


def test_without_the_goal_nothing_changes():
    parsed, errors = parse(form(goal="longevity", weeks="4", short_days="99"))
    assert errors == {} and parsed.short_days is None and parsed.repeat_months is None
    assert parsed.items[0].start_day is None and parsed.items[0].run_days is None
    assert parsed.end_date == date(2026, 11, 1)                                   # four weeks, as before


def test_saving_makes_a_repeating_protocol_that_remembers_its_first_start(client, me):
    data = {"name": "Shortbuild B", "start_date": "2026-10-05", "goal": "annual-short-term", "short_days": "21", "repeat_months": "6",
            "items-0-peptide_id": str(peptide("Epithalon")), "items-0-dose": "10", "items-0-dose_unit": "mg", "items-0-run_days": "10",
            "items-1-peptide_id": str(peptide("Thymalin")), "items-1-start_day": "11", "items-1-run_days": "10"}
    assert client.post("/protocols", data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Shortbuild B"))
        assert (p.short_days, p.repeat_months, p.anchor_start, p.run_number) == (21, 6, date(2026, 10, 5), 1)
        assert p.end_date == date(2026, 10, 25)
        assert [(i.start_day, i.run_days) for i in p.items] == [(1, 10), (11, 10)]
        pid = p.id
    edit = html.unescape(client.get(f"/protocols/{pid}/edit").text)
    state = json.loads(edit.split('id="builder-data">')[1].split("</script>")[0])["state"]
    assert (state["short_days"], state["repeat_months"]) == ("21", "6")
    assert [(i["start_day"], i["run_days"]) for i in state["items"]] == [("1", "10"), ("11", "10")]


def test_items_of_a_short_course_are_due_only_on_their_days(client, me):
    from datetime import timedelta
    from app.calendar.schedule import occurrences
    data = {"name": "Shortbuild C", "start_date": TODAY.isoformat(), "goal": "annual-short-term", "short_days": "21", "repeat_months": "12",
            "items-0-peptide_id": str(peptide("Epithalon")), "items-0-run_days": "10", "items-1-peptide_id": str(peptide("Thymalin")), "items-1-start_day": "11", "items-1-run_days": "10"}
    client.post("/protocols", data=data, follow_redirects=False)
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Shortbuild C"))
        due = {n: [i.peptide for o in occurrences([p], TODAY + timedelta(days=n), TODAY + timedelta(days=n)) for i in o.items] for n in (0, 9, 10, 19, 20)}
    assert due == {0: ["Epithalon"], 9: ["Epithalon"], 10: ["Thymalin"], 19: ["Thymalin"], 20: []}        # days 1-10, then 11-20, then nothing (day 21 is idle)
