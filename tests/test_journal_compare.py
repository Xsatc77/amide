"""Journal, Compare: pick a day of the week and see mood, energy, sleep quality and appetite for just that weekday across history."""

import html
from datetime import date, timedelta

import pytest

from app.journal_compare import WEEKDAYS, parse_weekday, weekday_averages, weekday_stats
from app.models import JournalEntry
from photo_helpers import other_client

MONDAY = date(2026, 9, 7)          # a Monday


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(JournalEntry).delete()
    db.commit()


def add(db, me, day, mood=None, energy=None, sleep=None, appetite=None):
    db.add(JournalEntry(owner_id=me, entry_date=day, mood=mood, energy=energy, sleep_quality=sleep, appetite=appetite))
    db.commit()


def seed(db, me):
    """Three Mondays and two Thursdays, with appetite peaking on Thursdays."""
    for n, (mood, appetite) in enumerate([(4, 2), (3, 1), (5, 2)]):
        add(db, me, MONDAY + timedelta(weeks=n), mood=mood, energy=3, sleep=4, appetite=appetite)
    for n, appetite in enumerate([5, 4]):
        add(db, me, MONDAY + timedelta(days=3, weeks=n), mood=3, energy=2, sleep=3, appetite=appetite)


def test_the_weekdays_are_monday_first_and_a_pick_is_read_by_name_or_number():
    assert [d[1] for d in WEEKDAYS] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert parse_weekday("thu", default=0) == 3 and parse_weekday("Thursday", default=0) == 3 and parse_weekday("3", default=0) == 3
    assert parse_weekday("nope", default=5) == 5 and parse_weekday(None, default=6) == 6 and parse_weekday("9", default=2) == 2


def test_stats_for_a_weekday_set_it_against_every_day(db, me):
    seed(db, me)
    entries = db.query(JournalEntry).filter(JournalEntry.owner_id == me).all()
    by_key = {s["key"]: s for s in weekday_stats(entries, 3)}                 # Thursdays
    appetite = by_key["appetite"]
    assert appetite["count"] == 2 and appetite["avg"] == pytest.approx(4.5) and appetite["low"] == 4 and appetite["high"] == 5
    assert appetite["all_avg"] == pytest.approx((2 + 1 + 2 + 5 + 4) / 5) and appetite["diff"] == pytest.approx(4.5 - 2.8)
    assert by_key["mood"]["avg"] == 3 and by_key["mood"]["count"] == 2


def test_a_rating_nobody_logged_on_that_weekday_has_no_numbers(db, me):
    add(db, me, MONDAY, mood=4)
    stats = {s["key"]: s for s in weekday_stats(db.query(JournalEntry).filter(JournalEntry.owner_id == me).all(), 0)}
    assert stats["mood"]["count"] == 1 and stats["energy"]["count"] == 0 and stats["energy"]["avg"] is None and stats["energy"]["diff"] is None


def test_the_weekly_pattern_is_the_average_for_each_weekday(db, me):
    seed(db, me)
    pattern = weekday_averages(db.query(JournalEntry).filter(JournalEntry.owner_id == me).all())
    appetite = pattern["appetite"]
    assert len(appetite) == 7 and appetite[0] == pytest.approx(5 / 3) and appetite[3] == pytest.approx(4.5) and appetite[1] is None


def test_the_page_charts_only_the_chosen_weekday(client, db, me):
    seed(db, me)
    page = html.unescape(client.get("/journal/compare", params={"day": "thu"}).text)
    chart = page.split('class="card rating-chart"')[1].split("</section>")[0]
    assert "Thursday" in page and chart.count("<circle") == 8                    # four ratings x two Thursdays; the Mondays are not drawn
    assert "Appetite (lower is better)" in page


def test_the_page_shows_the_averages_and_the_weekly_pattern(client, db, me):
    seed(db, me)
    page = html.unescape(client.get("/journal/compare", params={"day": "thu"}).text)
    assert "4.5" in page and "2.8" in page and "+1.7" in page                  # Thursday appetite, every-day appetite, the difference
    pattern = page.split("Average By Day Of The Week")[1]
    assert all(d[1] in pattern for d in WEEKDAYS)


def test_with_no_entries_for_that_weekday_it_says_so(client, db, me):
    add(db, me, MONDAY, mood=4)
    page = client.get("/journal/compare", params={"day": "fri"}).text
    assert "No Friday entries yet" in page and 'class="card rating-chart"' not in page


def test_with_no_day_it_opens_on_todays_weekday_and_a_bad_day_does_the_same(client, db, me):
    today_name = WEEKDAYS[date.today().weekday()][1]
    assert today_name in client.get("/journal/compare").text and today_name in client.get("/journal/compare", params={"day": "zzz"}).text


def test_the_range_narrows_the_history(client, db, me):
    same_weekday = date.today() - timedelta(weeks=1)
    add(db, me, same_weekday, mood=5)
    add(db, me, same_weekday - timedelta(weeks=42), mood=1)                      # the same weekday, about ten months earlier
    day = WEEKDAYS[same_weekday.weekday()][0]
    everything = html.unescape(client.get("/journal/compare", params={"day": day}).text)
    recent = html.unescape(client.get("/journal/compare", params={"day": day, "range": "3mo"}).text)
    assert everything.split('class="card rating-chart"')[1].split("</section>")[0].count("<circle") == 2
    assert recent.split('class="card rating-chart"')[1].split("</section>")[0].count("<circle") == 1 and "Last 3 Months" in recent


def test_only_your_own_entries_count(client, db, me):
    add(db, me, MONDAY, mood=4)
    with other_client("comparepartner") as other:
        other.post("/journal/entries", data={"entry_date": MONDAY.isoformat(), "mood": "1", "energy": "1"})
        page = html.unescape(client.get("/journal/compare", params={"day": "mon"}).text)
    chart = page.split('class="card rating-chart"')[1].split("</section>")[0]
    assert chart.count("<circle") == 1                                           # my one Monday mood; the partner's ratings are not mixed in


def test_the_journal_tab_has_a_compare_button(client, db, me):
    assert 'href="/journal/compare"' in client.get("/measurements", params={"tab": "journal"}).text
