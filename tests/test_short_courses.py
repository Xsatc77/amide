"""Short courses that repeat: run dates, rolling forward, the countdown reminders, and which days an item of a short course is due."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.calendar.schedule import is_due
from app.db import SessionLocal
from app.models import Frequency, Protocol
from app.protocols import repeat


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Shortcourse%")).delete(synchronize_session=False)
        s.commit()


def make(uid, *, start, days=21, months=12, run=1, name="Shortcourse A", paused=False, ended=None):
    with SessionLocal() as s:
        p = Protocol(name=name, owner_id=uid, start_date=start, end_date=start + timedelta(days=days - 1), short_days=days, repeat_months=months,
                     anchor_start=start, run_number=run, paused=paused, ended_on=ended)
        s.add(p)
        s.commit()
        return p.id


def test_add_months_keeps_month_ends_in_range():
    assert repeat.add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert repeat.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert repeat.add_months(date(2025, 11, 18), 3) == date(2026, 2, 18)


def test_every_run_is_counted_from_the_first_start_so_month_ends_do_not_drift():
    assert repeat.run_dates(date(2025, 1, 18), 12, 21, 1) == (date(2025, 1, 18), date(2025, 2, 7))
    assert repeat.run_dates(date(2025, 1, 18), 12, 21, 2) == (date(2026, 1, 18), date(2026, 2, 7))
    assert repeat.run_dates(date(2025, 1, 31), 3, 10, 2)[0] == date(2025, 4, 30)
    assert repeat.run_dates(date(2025, 1, 31), 3, 10, 3)[0] == date(2025, 7, 31)             # back on the 31st, not stuck on the 30th


def test_a_finished_protocol_rolls_on_to_its_next_run_and_keeps_its_run_count(client, me):
    pid = make(me, start=date(2025, 1, 18))
    with SessionLocal() as s:
        assert repeat.roll_forward(s, me, date(2025, 2, 8)) == 1
        p = s.get(Protocol, pid)
        assert (p.start_date, p.end_date, p.run_number) == (date(2026, 1, 18), date(2026, 2, 7), 2)
        assert repeat.roll_forward(s, me, date(2025, 2, 9)) == 0                          # already on the next run


def test_a_run_still_going_or_not_yet_started_is_left_alone(client, me):
    make(me, start=date(2025, 1, 18))
    with SessionLocal() as s:
        assert repeat.roll_forward(s, me, date(2025, 2, 7)) == 0                          # its last day
        assert repeat.roll_forward(s, me, date(2024, 12, 1)) == 0


def test_a_protocol_nobody_opened_for_years_skips_to_the_current_run(client, me):
    pid = make(me, start=date(2025, 1, 18))
    with SessionLocal() as s:
        repeat.roll_forward(s, me, date(2028, 1, 20))
        p = s.get(Protocol, pid)
        assert (p.start_date, p.run_number) == (date(2028, 1, 18), 4)


def test_paused_and_ended_protocols_do_not_roll(client, me):
    paused = make(me, start=date(2025, 1, 18), paused=True, name="Shortcourse P")
    ended = make(me, start=date(2025, 1, 18), ended=date(2025, 1, 25), name="Shortcourse E")
    with SessionLocal() as s:
        assert repeat.roll_forward(s, me, date(2026, 6, 1)) == 0
        assert s.get(Protocol, paused).run_number == 1 and s.get(Protocol, ended).run_number == 1


def test_the_countdown_reminders_sit_90_60_30_14_and_7_days_before_the_start(client, me):
    make(me, start=date(2026, 1, 18), run=1)
    with SessionLocal() as s:
        marks = repeat.calendar_dates(s, me, date(2025, 10, 1), date(2026, 1, 31))
    start = date(2026, 1, 18)
    assert marks[start - timedelta(days=90)] == ["Shortcourse A: Check stock (90 days to go)"]
    assert marks[start - timedelta(days=60)] == ["Shortcourse A: Order missing stock (60 days to go)"]
    assert marks[start - timedelta(days=30)] == ["Shortcourse A: Stock check (30 days to go)"]
    assert marks[start - timedelta(days=14)] == ["Shortcourse A: General reminder (14 days to go)"]
    assert marks[start - timedelta(days=7)] == ["Shortcourse A: 7 Day Water Fast (7 days to go)"]


def test_quarterly_drops_the_reminders_that_would_land_inside_the_run_before(client, me):
    make(me, start=date(2026, 1, 18), months=3, days=21, run=1)
    with SessionLocal() as s:
        marks = repeat.calendar_dates(s, me, date(2026, 1, 1), date(2026, 12, 31))
    next_start = date(2026, 4, 18)
    assert next_start - timedelta(days=90) not in marks                                      # that is the first day of this run
    assert marks[next_start - timedelta(days=60)] == ["Shortcourse A: Order missing stock (60 days to go)"]


def test_a_paused_protocol_has_no_reminders(client, me):
    make(me, start=date(2026, 1, 18), paused=True)
    with SessionLocal() as s:
        assert repeat.calendar_dates(s, me, date(2025, 1, 1), date(2027, 12, 31)) == {}


def item(**kw):
    base = dict(frequency=Frequency.DAILY, every_n_days=None, weekdays=None, cycle_offs=[], start_day=None, run_days=None)
    return SimpleNamespace(**{**base, **kw})


def test_an_item_of_a_short_course_is_due_only_on_its_own_days():
    start = date(2026, 1, 18)
    ten_days = item(start_day=1, run_days=10)
    assert [is_due(ten_days, start, start + timedelta(days=n)) for n in (0, 9, 10, 20)] == [True, True, False, False]
    later = item(start_day=11, run_days=10)                                                  # takes over once the first one stops
    assert [is_due(later, start, start + timedelta(days=n)) for n in (9, 10, 19, 20)] == [False, True, True, False]


def test_every_other_day_counts_from_the_items_own_first_day():
    start = date(2026, 1, 18)
    eod = item(frequency=Frequency.EOD, start_day=4, run_days=7)
    assert [is_due(eod, start, start + timedelta(days=n)) for n in (3, 4, 5, 6)] == [True, False, True, False]
