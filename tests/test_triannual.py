"""Triannual (every 4 months) items in a quarterly short course: their months land inside the runs, so they are counted by month, not by run."""

from datetime import date, timedelta

from app.calendar.schedule import occurrences
from app.models import DoseUnit, Frequency, Peptide, Protocol, ProtocolItem, Route
from app.protocols import repeat


def test_the_choice_is_there_in_order_and_off_beat_items_have_their_months_as_choices():
    assert repeat.REPEAT_CHOICES == ((12, "Annually"), (6, "Semiannually"), (4, "Triannually"), (3, "Quarterly"))
    assert repeat.first_run_span(3, 4) == 4 and repeat.first_run_span(3, 6) == 2 and repeat.first_run_span(3, 12) == 4 and repeat.first_run_span(3, 3) == 1
    assert repeat.first_run_span(4, 4) == 1 and repeat.first_run_span(4, 6) == 6          # a course of triannual items: 6 is not whole runs apart


def test_a_triannual_item_lands_in_months_2_6_and_10_of_a_quarterly_course():
    # months counted from the course's start: run 1 = months 1-3, run 2 = 4-6, run 3 = 7-9, run 4 = 10-12, then the next year again
    shifts = {run: repeat.item_offset(3, 4, run, first_run=2) for run in range(1, 9)}
    assert shifts == {1: 30, 2: 60, 3: None, 4: 0, 5: 30, 6: 60, 7: None, 8: 0}          # month 2 of run 1, month 3 of run 2, month 1 of run 4, ...
    assert [repeat.item_offset(3, 4, run, first_run=1) for run in range(1, 5)] == [0, 30, 60, None]     # starting in month 1: months 1, 5, 9


def test_the_calendar_shows_the_doses_in_those_months_only():
    pep = Peptide(id=1, name="Tri Peptide")
    item = ProtocolItem(id=1, peptide_id=1, dose=2, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, start_day=11, run_days=20,
                        repeat_months=4, first_run=2, steps=[], cycle_offs=[])
    item.peptide = pep
    item.inventory_item = None
    anchor = date(2030, 1, 1)
    seen = {}
    for run in range(1, 5):
        start, end = repeat.run_dates(anchor, 3, 90, run)
        p = Protocol(id=1, name="Tri", start_date=start, end_date=end, short_days=90, repeat_months=3, anchor_start=anchor, run_number=run, paused=False, titration_enabled=False)
        p.items = [item]
        days = [o.date for o in occurrences([p], start, end)]
        if days:
            seen[run] = (days[0] - start).days + 1, len(days)
    # run 1: month 2, days 11-30 -> run day 41; run 2: month 3, run day 71; run 3: none; run 4: month 1, run day 11; each 20 days
    assert seen == {1: (41, 20), 2: (71, 20), 4: (11, 20)}
