import html
import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Peptide, Protocol
from app.routers.protocols import get_today

TODAY = date(2026, 9, 22)  # a Tuesday


@pytest.fixture(autouse=True)
def fixed_today():
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)


def peptide_id(name):
    with SessionLocal() as s:
        return s.scalar(select(Peptide.id).where(Peptide.name == name))


def make(client, name="Heal", start="2026-09-01", items=None):
    """Create a protocol through the builder form (owned by the signed-in test user)."""
    data = {"name": name, "start_date": start, "goal": ["muscle-recovery"]}
    data.update(items or {
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
        "items-1-peptide_id": str(peptide_id("TB-500")), "items-1-dose": "2", "items-1-dose_unit": "mg",
        "items-1-frequency": "weekly", "items-1-time_of_day": "pm",
    })
    assert client.post("/protocols", data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        return s.scalar(select(Protocol.id).where(Protocol.name == name))


def page(client, **params) -> str:
    r = client.get("/calendar", params=params)
    assert r.status_code == 200
    return html.unescape(r.text)


def cal_data(text) -> dict:
    return json.loads(re.search(r'<script type="application/json" id="cal-data">(.*?)</script>', text, re.S).group(1))


def test_nav_and_default_month(client):
    t = page(client)
    assert 'href="/calendar"' in t and "September 2026" in t
    assert re.search(r'class="[^"]*cal-today[^"]*"[^>]*data-date="2026-09-22"', t)
    for v in ("month", "week", "day"):
        assert f'<option value="{v}"' in t


def test_month_view(client):
    pid = make(client)
    t = page(client, view="month", date="2026-09-15")
    # Week number column links to the week; day numbers link to the day.
    assert 'href="/calendar?view=week&date=2026-09-20"' in t and ">39<" in t
    assert 'href="/calendar?view=day&date=2026-09-22"' in t
    # Daily protocol gets its own mark every day, never one bar spanning the whole run: "Heal"'s
    # auto-derived initials ("HE") appear on each of two consecutive days, each independently
    # clickable to the same protocol's detail dialog.
    assert t.count(">HE</button>") >= 2 or t.count(">HE<") >= 2
    assert f'data-key="{pid}|2026-09-22"' in t and f'data-key="{pid}|2026-09-23"' in t
    # Prev / next / today navigation.
    assert 'href="/calendar?view=month&date=2026-08-15"' in t and 'href="/calendar?view=month&date=2026-10-15"' in t


def test_month_view_respects_a_cycle_off(client, db):
    from app.models import ProtocolItem, ProtocolItemCycleOff
    pid = make(client, name="Cycled", items={
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
    })
    with SessionLocal() as s:
        item = s.scalar(select(ProtocolItem).where(ProtocolItem.protocol_id == pid))
        s.add(ProtocolItemCycleOff(protocol_item_id=item.id, start_week=1, end_week=52))
        s.commit()
    t = page(client, view="month", date="2026-09-15")
    assert 'data-key="{}|2026-09-15"'.format(pid) not in t


def test_month_view_marks_dont_merge_across_days(client):
    """The old bar-per-protocol layout merged every consecutive due day into one spanning bar --
    the redesign gives each day its own independent mark instead, so a daily protocol shows a
    separate clickable mark on every single day it's due, not one wide bar."""
    make(client, name="Solo", items={
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
    })
    t = page(client, view="month", date="2026-09-15")
    with SessionLocal() as s:
        pid = s.scalar(select(Protocol.id).where(Protocol.name == "Solo"))
    for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
        assert f'data-key="{pid}|{d}"' in t


def test_week_view(client):
    pid = make(client)
    t = page(client, view="week", date="2026-09-22")
    assert "Week 39" in t and "Sep 20" in t and "Sep 26" in t
    am = re.search(r'data-slot="am".*?</div>\s*</div>', t, re.S).group(0)
    assert f'data-key="{pid}|2026-09-22"' in am
    # TB-500 is weekly from Tue Sep 1 -> due Tue Sep 22, in the PM row.
    pm = re.search(r'data-slot="pm".*?</div>\s*</div>', t, re.S).group(0)
    assert f'data-key="{pid}|2026-09-22"' in pm and f'data-key="{pid}|2026-09-23"' not in pm
    assert 'href="/calendar?view=day&date=2026-09-23"' in t


def test_day_view_cards_and_details(client):
    pid = make(client)
    t = page(client, view="day", date="2026-09-22")
    assert "Tuesday, September 22, 2026" in t
    assert "BPC-157" in t and "250 mcg" in t and "TB-500" in t and "2 mg" in t
    details = cal_data(t)["occurrences"][f"{pid}|2026-09-22"]
    assert details["name"] == "Heal" and details["edit_url"] == f"/protocols/{pid}/edit"
    assert [(i["peptide"], i["dose"], i["time"]) for i in details["items"]] == [
        ("BPC-157", "250 mcg", "AM"), ("TB-500", "2 mg", "PM")]


def test_day_view_as_needed_and_empty(client):
    make(client, "PRN", items={"items-0-peptide_id": str(peptide_id("PT-141 (Bremelanotide)")),
                               "items-0-frequency": "as_needed"})
    t = page(client, view="day", date="2026-09-22")
    assert "As needed" in t and "PT-141 (Bremelanotide)" in t
    assert "Nothing due" in page(client, view="day", date="2026-08-01")


def test_bad_params_fall_back(client):
    t = page(client, view="year", date="nope")
    assert "September 2026" in t


def test_paused_protocol_hidden(client):
    pid = make(client, "Resting")
    client.post(f"/protocols/{pid}/pause")
    assert "Resting" not in page(client, view="month", date="2026-09-15")


def test_calendar_privacy(client):
    make(client, "Mine only")
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "CalOther", "password": "Cal0ther!", "confirm": "Cal0ther!"})
    r = other.get("/calendar", params={"view": "month", "date": "2026-09-15"})
    assert r.status_code == 200 and "Mine only" not in r.text and "Mine only" not in json.dumps(cal_data(r.text))


def test_month_view_marks_a_day_with_a_scheduled_workout(client, db, me):
    from app.models import WorkoutPlan, WorkoutPlanDay, WorkoutSource, WEEKDAY_LETTERS
    today_letter = WEEKDAY_LETTERS[date.today().weekday()]
    plan = WorkoutPlan(owner_id=me, name="Test Plan", source=WorkoutSource.MANUAL, started_on=date.today())
    plan.days = [WorkoutPlanDay(position=0, label="Leg Day", weekdays=today_letter)]
    db.add(plan)
    db.commit()

    body = client.get("/calendar").text
    assert "Workout scheduled" in body


def test_month_view_marks_a_day_a_fitness_test_was_completed(client, db, me):
    from app.models import FitnessTestExerciseName, FitnessTestResult
    db.add(FitnessTestResult(owner_id=me, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                             value=20, tested_at=date.today()))
    db.commit()
    body = client.get("/calendar").text
    assert "Fitness Test completed" in body


def test_month_view_overflows_past_four_marks_in_one_day(client):
    for i in range(6):
        make(client, name=f"P{i}", items={
            "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
            "items-0-frequency": "daily", "items-0-time_of_day": "am",
        })
    t = page(client, view="month", date="2026-09-15")
    assert re.search(r'class="cal-more"[^>]*>\+2<', t)
