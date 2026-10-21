"""The Journal's Trends: one full-width chart, a dotted line in its own colour for mood, energy, sleep quality and appetite, with lines that
land on the same spot nudged apart so each stays visible."""

import html
from datetime import date, timedelta

import pytest

from app.models import JournalEntry
from app.routers.journal import RATING_SERIES, rating_chart

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(JournalEntry).delete()
    db.commit()


def series(key, values, start=date(2026, 10, 1)):
    return {"key": key, "points": [(start + timedelta(days=i), float(v)) for i, v in enumerate(values)]}


def test_each_item_has_its_own_colour_and_label():
    assert [s["key"] for s in RATING_SERIES] == ["mood", "energy", "sleep_quality", "appetite"]
    assert len({s["color"] for s in RATING_SERIES}) == 4
    assert [s["label"] for s in RATING_SERIES][-1].startswith("Appetite") and "lower is better" in RATING_SERIES[-1]["label"]


def test_the_chart_is_wide_and_every_line_is_dotted_in_its_colour():
    chart = rating_chart([series("mood", [3, 4, 5]), series("energy", [2, 2, 3])])
    assert chart["width"] >= 1000
    assert [s["key"] for s in chart["series"]] == ["mood", "energy"]
    for s in chart["series"]:
        assert s["color"] and s["dash"]                      # a dash pattern: the line is dotted
        assert s["poly"].count(",") == 3                      # one pair of coordinates per day
    assert [t["value"] for t in chart["y_axis"]["ticks"]] == ["5", "4", "3", "2", "1"]


def test_two_lines_on_the_same_spot_are_nudged_apart_but_lines_alone_are_not():
    chart = rating_chart([series("mood", [3, 5, 1]), series("energy", [3, 2, 1]), series("sleep_quality", [4, 4, 2])])
    mood, energy, sleep = (s["points"] for s in chart["series"])
    assert mood[0][1] != energy[0][1]                          # both rated 3 on day one: separated
    assert mood[2][1] != energy[2][1]                          # both rated 1 on day three: separated
    assert abs(mood[0][1] - energy[0][1]) < 15                  # but only by a little, still reading as a 3
    other = rating_chart([series("sleep_quality", [4, 4, 2])])["series"][0]["points"]
    assert sleep[1][1] == other[1][1]                           # a point nobody shares keeps its true height
    mood_alone = rating_chart([series("mood", [3, 5, 1])])["series"][0]["points"]
    assert mood_alone[1][1] == mood[1][1]                       # day two: mood 5 is alone, so it is not moved
    assert mood_alone[0][1] != mood[0][1]                       # day one: it was moved because energy shares the spot


def test_four_lines_on_one_spot_are_all_apart_and_stay_in_order():
    chart = rating_chart([series(k, [3, 3]) for k in ("mood", "energy", "sleep_quality", "appetite")])
    ys = [s["points"][0][1] for s in chart["series"]]
    assert len(set(ys)) == 4 and ys == sorted(ys)


def test_the_page_draws_one_chart_with_a_legend(client, db, me):
    for n, (mood, energy, sleep) in enumerate([(2, 3, 4), (4, 3, 2), (5, 4, 3)]):
        client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=n + 1)).isoformat(), "mood": str(mood), "energy": str(energy), "sleep_quality": str(sleep), "appetite": "2"})
    page = html.unescape(client.get("/measurements", params={"tab": "journal"}).text)
    section = page.split('id="journal-trends-heading"')[1].split("</section>")[0]
    assert section.count('class="card rating-chart"') == 1
    assert section.count("<polyline") == 4                      # mood, energy, sleep quality and appetite
    legend = section.split("chart-legend")[1].split("</ul>")[0]
    for label in ("Mood", "Energy", "Sleep quality", "Appetite"):
        assert label in legend
    assert "stroke-dasharray" in section


def test_a_day_with_one_rating_draws_no_line_for_it(client, db, me):
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=2)).isoformat(), "mood": "3"})
    assert "journal-trends-heading" not in client.get("/measurements", params={"tab": "journal"}).text
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=1)).isoformat(), "mood": "4"})
    section = html.unescape(client.get("/measurements", params={"tab": "journal"}).text).split('id="journal-trends-heading"')[1].split("</section>")[0]
    legend = section.split("chart-legend")[1].split("</ul>")[0]
    assert "Mood" in legend and "Energy" not in legend and "Sleep quality" not in legend and "Appetite" not in legend


def test_the_shared_by_column_is_left_out_when_every_entry_is_your_own(client, db, me):
    client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=1)).isoformat(), "mood": "4"})
    page = client.get("/measurements", params={"tab": "journal"}).text
    assert "<th>Day totals</th>" in page and "Shared by" not in page.split('id="journal-entries-heading"')[1]
