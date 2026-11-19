"""Protocol files: Export writes one protocol out, Import on the Protocols page builds it again; and the run an item first joins in (first_run)."""

import io
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Peptide, Protocol, ProtocolItem
from app.protocols import repeat
from app.routers.protocols import get_today

TODAY = date(2030, 3, 4)
START = "2030-04-01"


@pytest.fixture(autouse=True)
def fixed_today(client, me):
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Transfer%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name == "Zzfictional Peptide").delete(synchronize_session=False)
        s.commit()


def one(peptide, start, days, every, first, **over):
    return {"peptide": peptide, "dose": 2.0, "dose_unit": "mg", "frequency": "daily", "every_n_days": None, "weekdays": "", "time_of_day": "any",
            "route": "subq", "notes": "", "start_day": start, "run_days": days, "repeat_months": every, "first_run": first, "steps": [], "cycle_offs": [], **over}


def a_file(**over):
    data = {"format": "amide-protocol", "version": 1, "name": "Transfer Course", "goals": ["annual-short-term"], "notes": "", "titration": False,
            "short_days": 90, "length_days": None,
            "items": [one("Epithalon", 1, 10, 3, 1), one("Thymalin", 61, 30, 6, 2), one("Zzfictional Peptide", 31, 20, 12, 4)]}
    data.update(over)
    return data


def upload(client, data, start=START):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return client.post("/protocols/import", data={"start_date": start}, files={"file": ("p.json", io.BytesIO(raw), "application/json")}, follow_redirects=False)


def mine():
    with SessionLocal() as s:
        return s.scalar(select(Protocol).where(Protocol.name == "Transfer Course"))


def test_an_item_joins_the_runs_of_its_own_cycle_from_the_run_it_starts_in():
    runs = lambda every, first: [r for r in range(1, 9) if repeat.joins_run(3, every, r, first)]
    assert runs(3, 1) == list(range(1, 9))                    # quarterly: every run
    assert runs(6, 1) == [1, 3, 5, 7] and runs(6, 2) == [2, 4, 6, 8]
    assert runs(12, 1) == [1, 5] and runs(12, 2) == [2, 6] and runs(12, 4) == [4, 8]


def test_importing_builds_the_protocol_with_each_items_run_and_adds_an_unknown_peptide(client, db, me):
    r = upload(client, a_file())
    assert r.status_code == 303 and r.headers["location"] == "/protocols"
    p = mine()
    assert p.owner_id == me and p.start_date.isoformat() == START and p.short_days == 90 and p.repeat_months == 3 and p.end_date.isoformat() == "2030-06-29"
    with SessionLocal() as s:
        items = {(it.peptide.name, it.first_run): (it.start_day, it.run_days, it.repeat_months)
                 for it in s.scalars(select(ProtocolItem).where(ProtocolItem.protocol_id == p.id))}
        assert items[("Thymalin", 2)] == (61, 30, 6) and items[("Zzfictional Peptide", 4)] == (31, 20, 12)
        assert s.scalar(select(Peptide.source).where(Peptide.name == "Zzfictional Peptide")).value == "custom"


def test_export_then_import_gives_the_same_protocol(client, db, me):
    upload(client, a_file())
    pid = mine().id
    exported = client.get(f"/protocols/{pid}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"] and exported.headers["content-disposition"].endswith('.amide-protocol.json"')
    data = exported.json()
    assert data["format"] == "amide-protocol" and data["name"] == "Transfer Course" and [i["first_run"] for i in data["items"]] == [1, 2, 4]
    assert "start_date" not in data and "owner_id" not in json.dumps(data)                 # a file carries the plan, not dates or people
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.id == pid).delete()
        s.commit()
    assert upload(client, data, "2031-01-10").status_code == 303
    assert mine().start_date.isoformat() == "2031-01-10"


def test_a_first_run_later_than_the_items_own_cycle_is_brought_back_into_it(client, db, me):
    upload(client, a_file(items=[one("Epithalon", 1, 10, 3, 3), one("Thymalin", 61, 30, 6, 9)]))
    with SessionLocal() as s:
        runs = sorted((it.peptide.name, it.first_run) for it in s.scalars(select(ProtocolItem).where(ProtocolItem.protocol_id == mine().id)))
    assert runs == [("Epithalon", 1), ("Thymalin", 2)]


def test_a_file_that_is_not_a_protocol_is_refused_with_a_reason(client, db, me):
    for bad, reason in ((b"not json", "not a protocol file"), (json.dumps({"hello": 1}).encode(), "not an Amide protocol file"),
                        (json.dumps(a_file(version=99)).encode(), "newer version"), (json.dumps(a_file(items=[])).encode(), "no peptides")):
        r = upload(client, bad)
        assert r.status_code == 303 and "import_error=" in r.headers["location"]
        assert reason.replace(" ", "%20") in r.headers["location"]
    assert mine() is None
    assert "Choose a protocol file" in client.get("/protocols?import_error=Choose a protocol file to import.").text


def test_a_file_with_a_problem_opens_in_the_builder_instead_of_saving(client, db, me):
    r = upload(client, a_file(items=[one("Epithalon", 1, 10, 3, 1, dose=-5)]))
    assert r.status_code == 422 and "Dose must be greater than 0" in r.text
    assert mine() is None


def test_the_page_has_an_import_form_and_each_card_an_export_link(client, db, me):
    upload(client, a_file())
    page = client.get("/protocols").text
    assert 'action="/protocols/import"' in page and 'name="file"' in page and f'href="/protocols/{mine().id}/export"' in page
