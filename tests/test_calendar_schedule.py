from datetime import date, timedelta
from types import SimpleNamespace as NS

from app.calendar import layout, schedule
from app.models import DoseUnit, Frequency, Route, TimeOfDay


def item(name="BPC-157", freq=Frequency.DAILY, dose=250.0, unit=DoseUnit.MCG, every_n=None, weekdays=None,
         tod=TimeOfDay.AM, steps=(), inventory=None, pid=1, item_id=None):
    if item_id is None:
        item_id = pid
    return NS(id=item_id, peptide=NS(id=pid, name=name), peptide_id=pid, dose=dose, dose_unit=unit, frequency=freq,
              every_n_days=every_n, weekdays=weekdays, time_of_day=tod, route=Route.SUBQ,
              inventory_item=NS(name=inventory) if inventory else None, steps=list(steps))


def proto(pid=1, name="Heal", start=date(2026, 9, 1), end=None, ended_on=None, paused=False, titration=False,
          items=None):
    return NS(id=pid, name=name, start_date=start, end_date=end, ended_on=ended_on, paused=paused,
              titration_enabled=titration, items=items if items is not None else [item()])


def due_dates(p, first=date(2026, 9, 1), last=date(2026, 9, 30)):
    return [o.date for o in schedule.occurrences([p], first, last)]


# ---------------------------------------------------------------- rules

def test_frequencies():
    s = date(2026, 9, 1)  # a Tuesday
    assert due_dates(proto(start=s, items=[item(freq=Frequency.DAILY)]), s, s + timedelta(days=2)) == [
        s, s + timedelta(days=1), s + timedelta(days=2)]
    # Every other day / every N days count from the protocol start, not the visible range.
    eod = due_dates(proto(start=s, items=[item(freq=Frequency.EOD)]), date(2026, 9, 10), date(2026, 9, 14))
    assert eod == [date(2026, 9, 11), date(2026, 9, 13)]
    every3 = due_dates(proto(start=s, items=[item(freq=Frequency.EVERY_N_DAYS, every_n=3)]),
                       date(2026, 9, 5), date(2026, 9, 12))
    assert every3 == [date(2026, 9, 7), date(2026, 9, 10)]
    weekly = due_dates(proto(start=s, items=[item(freq=Frequency.WEEKLY)]))
    assert weekly == [date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]
    mwf = due_dates(proto(start=s, items=[item(freq=Frequency.WEEKDAYS, weekdays="MWF")]), s, date(2026, 9, 7))
    assert mwf == [date(2026, 9, 2), date(2026, 9, 4), date(2026, 9, 7)]
    assert due_dates(proto(items=[item(freq=Frequency.AS_NEEDED)])) == []


def test_window_edges():
    p = proto(start=date(2026, 9, 10), end=date(2026, 9, 12))
    assert due_dates(p) == [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
    ended = proto(start=date(2026, 9, 1), end=date(2026, 12, 1), ended_on=date(2026, 9, 3))
    assert due_dates(ended) == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    future = proto(start=date(2026, 10, 1))  # scheduled protocols show from their start
    assert due_dates(future, date(2026, 9, 28), date(2026, 10, 2)) == [date(2026, 10, 1), date(2026, 10, 2)]
    assert due_dates(proto(paused=True)) == []


def test_occurrence_groups_items_and_titration():
    steps = [NS(start_week=1, end_week=1, dose=100.0), NS(start_week=2, end_week=None, dose=250.0)]
    p = proto(titration=True, items=[
        item("BPC-157", steps=steps, inventory="BPC vial", pid=1),
        item("TB-500", freq=Frequency.WEEKLY, dose=None, unit=DoseUnit.MG, tod=TimeOfDay.PM, pid=2)])
    [first] = schedule.occurrences([p], date(2026, 9, 1), date(2026, 9, 1))
    assert [(i.peptide, i.dose, i.step) for i in first.items] == [("BPC-157", 100.0, 1), ("TB-500", None, None)]
    assert first.items[0].inventory == "BPC vial" and first.items[1].time_of_day is TimeOfDay.PM
    [week2] = schedule.occurrences([p], date(2026, 9, 9), date(2026, 9, 9))
    assert [(i.peptide, i.dose, i.step) for i in week2.items] == [("BPC-157", 250.0, 2)]  # TB-500 not due
    assert first.protocol_id == 1 and first.protocol_name == "Heal"


def test_titration_off_uses_item_dose():
    steps = [NS(start_week=1, end_week=None, dose=100.0)]
    [o] = schedule.occurrences([proto(items=[item(steps=steps)])], date(2026, 9, 1), date(2026, 9, 1))
    assert (o.items[0].dose, o.items[0].step) == (250.0, None)


def test_as_needed_listing():
    p = proto(items=[item("PT-141", freq=Frequency.AS_NEEDED), item()])
    [(prot, items)] = schedule.as_needed([p], date(2026, 9, 5))
    assert prot is p and [i.peptide for i in items] == ["PT-141"]
    assert schedule.as_needed([p], date(2026, 8, 31)) == []  # before start


# ---------------------------------------------------------------- weeks

def test_week_numbers():
    assert schedule.week_number(date(2026, 1, 1)) == 1
    assert schedule.week_number(date(2026, 1, 3)) == 1   # Saturday
    assert schedule.week_number(date(2026, 1, 4)) == 2   # Sunday starts week 2
    assert schedule.week_number(date(2026, 9, 22)) == 39
    assert schedule.week_number(date(2025, 12, 28)) == 1  # week containing Jan 1 2026
    assert schedule.week_number(date(2025, 12, 27)) == 52
    assert schedule.week_start(date(2026, 9, 23)) == date(2026, 9, 20)  # Sunday


def test_month_weeks():
    weeks = layout.month_weeks(date(2026, 9, 15))
    assert weeks[0][0] == date(2026, 8, 30) and weeks[-1][-1] == date(2026, 10, 3) and len(weeks) == 5
    assert all(len(w) == 7 and w[0].weekday() == 6 for w in weeks)  # Sunday first


# ---------------------------------------------------------------- month marks

def test_marks_are_per_day_not_spanning():
    daily = proto(1, "Daily", start=date(2026, 9, 2), end=date(2026, 9, 10))
    weekly = proto(2, "Weekly", items=[item(freq=Frequency.WEEKLY)])
    weeks = layout.month_weeks(date(2026, 9, 1))
    occs = schedule.occurrences([daily, weekly], weeks[0][0], weeks[-1][-1])
    rows = layout.month_rows(weeks, occs, colors={1: 0, 2: 1})
    first_row = rows[0]  # Aug 30 - Sep 5
    # Daily is due Sep 2-5 within this row: one independent day's worth of marks each day, not one
    # bar spanning all four days.
    assert [m.protocol_id for m in first_row.marks[date(2026, 9, 2)]] == [1]
    assert [m.protocol_id for m in first_row.marks[date(2026, 9, 5)]] == [1]
    assert 1 not in [m.protocol_id for m in first_row.marks[date(2026, 9, 1)]]  # Daily hasn't started yet
    # Weekly is due Sep 1 alone this row.
    assert [m.protocol_id for m in first_row.marks[date(2026, 9, 1)]] == [2]
    assert first_row.week_no == 36


def test_marks_sorted_by_time_of_day():
    p = proto(1, "Stack", items=[
        item("PM-dose", tod=TimeOfDay.PM, pid=1, item_id=1),
        item("AM-dose", tod=TimeOfDay.AM, pid=2, item_id=2),
        item("Bedtime-dose", tod=TimeOfDay.BEDTIME, pid=3, item_id=3),
    ])
    weeks = layout.month_weeks(date(2026, 9, 1))
    occs = schedule.occurrences([p], weeks[0][0], weeks[-1][-1])
    rows = layout.month_rows(weeks, occs, colors={1: 0})
    day = date(2026, 9, 1)
    marks = next(r.marks[day] for r in rows if r.marks.get(day))
    assert [m.protocol_item_id for m in marks] == [2, 1, 3]  # AM, PM, Bedtime


def test_marks_overflow_past_the_cap():
    protos = [proto(i, f"P{i}") for i in range(1, 7)]
    weeks = layout.month_weeks(date(2026, 9, 1))
    occs = schedule.occurrences(protos, weeks[0][0], weeks[-1][-1])
    rows = layout.month_rows(weeks, occs, colors={i: i % 8 for i in range(1, 7)}, max_marks=4)
    row = rows[1]
    a_day = row.days[3]
    assert len(row.marks[a_day]) == 4
    assert row.overflow[a_day] == 2


def test_initials():
    assert layout.initials("Weight Loss") == "WL"
    assert layout.initials("Testosterone") == "TE"
    assert layout.initials("Muscle Building Stack") == "MBS"
