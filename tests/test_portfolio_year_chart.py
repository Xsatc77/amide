"""The Year Calendar page of the Protocol Portfolio: which months each peptide is taken in."""

from datetime import date
from io import BytesIO
from types import SimpleNamespace as NS

from pypdf import PdfReader

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route
from app.portfolio.timeline import year_chart


def item(name, **kw):
    base = dict(id=id(name), peptide=NS(name=name), run_days=None, start_day=None, repeat_months=None, first_run=1, frequency=Frequency.DAILY,
                every_n_days=None, weekdays=None, cycle_offs=[], steps=[], dose=2.0, dose_unit=DoseUnit.MG)
    return NS(**{**base, **kw})


def course(items, **kw):
    base = dict(items=items, start_date=date(2027, 1, 1), end_date=date(2027, 1, 30), ended_on=None, paused=False, short_days=30, repeat_months=3,
                anchor_start=date(2027, 1, 1), run_number=1, titration_enabled=False)
    return NS(**{**base, **kw})


def months(chart, name):
    row = next(r for r in chart["rows"] if r["name"] == name)
    return sorted({(d.month) for b, e in row["spans"] for d in (b, e) if d})


def test_the_window_is_twelve_months_from_the_month_the_run_starts():
    chart = year_chart(course([item("A")]), date(2027, 1, 5))
    assert [m.strftime("%Y-%m") for m in chart["months"]] == [f"2027-{m:02d}" for m in range(1, 13)]


def test_a_quarterly_course_shows_four_runs_and_a_semiannual_peptide_only_two():
    chart = year_chart(course([item("Every", repeat_months=3), item("Half", repeat_months=6)]), date(2027, 1, 5))
    every = next(r for r in chart["rows"] if r["name"] == "Every")["spans"]
    half = next(r for r in chart["rows"] if r["name"] == "Half")["spans"]
    assert [b.month for b, e in every] == [1, 4, 7, 10]
    assert [b.month for b, e in half] == [1, 7]


def test_a_peptide_with_its_own_start_day_and_run_starts_later_in_the_run():
    chart = year_chart(course([item("Late", start_day=15, run_days=10, repeat_months=3)], short_days=60, end_date=date(2027, 3, 1)), date(2027, 1, 5))
    (b, e), *_ = next(r for r in chart["rows"] if r["name"] == "Late")["spans"]
    assert (b, e) == (date(2027, 1, 15), date(2027, 1, 24))


def test_an_ordinary_protocol_runs_from_its_start_to_its_end_and_an_open_one_has_no_end():
    plain = course([item("Plain")], short_days=None, repeat_months=None, anchor_start=None, start_date=date(2027, 2, 10), end_date=date(2027, 5, 9))
    chart = year_chart(plain, date(2027, 2, 10))
    assert chart["months"][0] == date(2027, 2, 1)
    assert chart["rows"][0]["spans"] == [(date(2027, 2, 10), date(2027, 5, 9))]
    open_ended = course([item("Open")], short_days=None, repeat_months=None, anchor_start=None, start_date=date(2027, 2, 10), end_date=None)
    assert year_chart(open_ended, date(2027, 2, 10))["rows"][0]["spans"][0][1] is None


def test_a_peptide_on_two_lines_is_one_row_and_no_items_is_no_chart():
    chart = year_chart(course([item("Twice", id=1), item("Twice", id=2)]), date(2027, 1, 5))
    assert len(chart["rows"]) == 1
    assert year_chart(course([]), date(2027, 1, 5)) is None


def test_the_portfolio_pdf_carries_the_year_calendar(client, db, me):
    with SessionLocal() as s:
        pep = Peptide(name="Calendar Test Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Year Course", start_date=date(2027, 1, 1), end_date=date(2027, 1, 30), owner_id=me, short_days=30, repeat_months=3,
                     anchor_start=date(2027, 1, 1), run_number=1)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=250, dose_unit=DoseUnit.MCG, frequency=Frequency.DAILY, route=Route.SUBQ,
                           run_days=30, start_day=1, repeat_months=3))
        s.commit()
        pid = p.id
    r = client.get(f"/protocols/{pid}/portfolio.pdf")
    assert r.status_code == 200
    text = "\n".join(pg.extract_text() for pg in PdfReader(BytesIO(r.content)).pages)
    assert "Year Calendar" in text and "Jan '27" in text and "M12" in text


def test_each_row_says_its_days_of_the_run_and_its_dose_how_often_and_how_long():
    chart = year_chart(course([item("Quarterly", run_days=10, repeat_months=3), item("Late", start_day=20, run_days=5, repeat_months=6, dose=3.0),
                               item("Weekdays", run_days=30, frequency=Frequency.WEEKDAYS, weekdays="MTWRF", repeat_months=6, dose=500, dose_unit=DoseUnit.MCG)],
                              short_days=60), date(2027, 1, 5))
    by = {r["name"]: r for r in chart["rows"]}
    assert by["Quarterly"]["tag"] == "D1-10" and by["Quarterly"]["sub"] == "2 mg/day, 4x, 10 days"
    assert by["Late"]["tag"] == "D20-24" and by["Late"]["sub"] == "3 mg/day, 2x, 5 days"
    assert by["Weekdays"]["tag"] == "D1-30 (Mon-Fri)" and by["Weekdays"]["sub"] == "500 mcg Mon-Fri, 2x, 30 days"
