"""Per-item cadence in a short course: the protocol repeats at its shortest item's pace and each run holds only the items that are due."""

import html
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.calendar.schedule import occurrences
from app.db import SessionLocal
from app.main import app
from app.models import Peptide, Protocol
from app.protocols import repeat
from app.routers.protocols import get_today

TODAY = date(2026, 9, 22)


@pytest.fixture(autouse=True)
def fixed_today(client, me):
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Cadence%")).delete(synchronize_session=False)
        s.commit()


def peptide(name):
    with SessionLocal() as s:
        return s.scalar(select(Peptide.id).where(Peptide.name == name))


def post(name, rows, **over):
    data = {"name": name, "start_date": "2026-10-05", "goal": "annual-short-term", "short_days": "21"}
    for i, (pep, months) in enumerate(rows):
        data[f"items-{i}-peptide_id"] = str(peptide(pep))
        if months:
            data[f"items-{i}-repeat_months"] = months
    data.update(over)
    return data


def test_an_item_joins_the_runs_its_cadence_asks_for():
    assert [repeat.joins_run(3, 3, r) for r in (1, 2, 3)] == [True, True, True]
    assert [repeat.joins_run(3, 6, r) for r in (1, 2, 3, 4, 5)] == [True, False, True, False, True]
    assert [repeat.joins_run(3, 12, r) for r in (1, 2, 3, 4, 5)] == [True, False, False, False, True]
    assert [repeat.joins_run(6, 12, r) for r in (1, 2, 3)] == [True, False, True]
    assert repeat.joins_run(None, None, 7) is True


def test_the_protocol_repeats_as_often_as_its_most_frequent_item(client, me):
    rows = [("Epithalon", "12"), ("Thymalin", "3"), ("Vesugen", "6")]
    assert client.post("/protocols", data=post("Cadence A", rows), follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Cadence A"))
        assert p.repeat_months == 3
        assert [i.repeat_months for i in p.items] == [12, 3, 6]


def test_a_blank_item_takes_the_annual_default_and_a_bad_one_is_refused(client, me):
    assert client.post("/protocols", data=post("Cadence B", [("Epithalon", "")]), follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Cadence B"))
        assert (p.repeat_months, p.items[0].repeat_months) == (12, 12)
    bad = client.post("/protocols", data=post("Cadence C", [("Epithalon", "5")]), follow_redirects=False)
    assert bad.status_code == 422 and "Pick how often this one repeats." in html.unescape(bad.text)


def test_each_run_holds_only_the_items_due_in_it(client, me):
    rows = [("Epithalon", "12"), ("Thymalin", "3"), ("Vesugen", "6")]
    client.post("/protocols", data=post("Cadence D", rows, start_date=TODAY.isoformat()), follow_redirects=False)
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Cadence D"))
        by_run = {}
        for run in (1, 2, 3, 4, 5):
            p.run_number = run
            by_run[run] = sorted(o_i.peptide for o in occurrences([p], TODAY, TODAY) for o_i in o.items)
    assert by_run == {1: ["Epithalon", "Thymalin", "Vesugen"], 2: ["Thymalin"], 3: ["Thymalin", "Vesugen"], 4: ["Thymalin"],
                      5: ["Epithalon", "Thymalin", "Vesugen"]}


def test_the_countdown_names_only_the_items_due_in_that_run(client, me):
    rows = [("Epithalon", "12"), ("Thymalin", "3")]
    client.post("/protocols", data=post("Cadence E", rows, start_date="2026-01-18"), follow_redirects=False)
    start = date(2026, 4, 18)                                                                   # run 2: only the quarterly item
    with SessionLocal() as s:
        marks = repeat.calendar_dates(s, me, date(2026, 1, 1), date(2026, 12, 31))
    assert marks[start - timedelta(days=60)] == ["Cadence E: Order missing stock (60 days to go) for Thymalin"]


def test_editing_the_cadence_counts_the_runs_again_from_this_one(client, me):
    client.post("/protocols", data=post("Cadence F", [("Epithalon", "12")]), follow_redirects=False)
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Cadence F"))
        p.run_number, p.start_date, p.end_date = 3, date(2028, 10, 5), date(2028, 10, 25)
        s.commit()
        pid = p.id
    edit = html.unescape(client.get(f"/protocols/{pid}/edit").text)
    state = json.loads(edit.split('id="builder-data">')[1].split("</script>")[0])["state"]
    assert state["items"][0]["repeat_months"] == "12"
    data = post("Cadence F", [("Epithalon", "12"), ("Thymalin", "3")], start_date="2028-10-05")
    client.post(f"/protocols/{pid}", data=data, follow_redirects=False)
    with SessionLocal() as s:
        p = s.get(Protocol, pid)
        assert (p.repeat_months, p.run_number, p.anchor_start) == (3, 1, date(2028, 10, 5))
