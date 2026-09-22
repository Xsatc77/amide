from datetime import date

from app.models import DoseUnit, Frequency
from app.protocols.forms import parse_protocol_form, state_from_form

IDS = dict(peptide_ids={1, 2, 3}, inventory_ids={7})


def F(**pairs):
    """Build the submitted-form mapping. `__` in a key becomes `-`; values may be str or list[str]."""
    return {k.replace("__", "-"): (v if isinstance(v, list) else [v]) for k, v in pairs.items()}


def base(**overrides):
    fields = dict(name="Cut", start_date="2026-09-01", goal=["fat-loss"],
                  items__0__peptide_id="1", items__0__dose="2.5", items__0__dose_unit="mg",
                  items__0__frequency="weekly", items__0__time_of_day="am", items__0__route="subq")
    return F(**{**fields, **overrides})


def test_happy_path():
    p, errors = parse_protocol_form(base(
        titration="1", end_date="", weeks="12",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="2.5",
        items__0__steps__1__start_week="5", items__0__steps__1__end_week="", items__0__steps__1__dose="5",
        items__1__peptide_id="2", items__1__frequency="weekdays", items__1__weekdays=["M", "W", "F"],
        items__1__inventory_item_id="7"), **IDS)
    assert errors == {}
    assert p.end_date == date(2026, 11, 23)
    assert p.items[0].dose_unit is DoseUnit.MG and p.items[0].frequency is Frequency.WEEKLY
    assert [(s.start_week, s.end_week, s.dose) for s in p.items[0].steps] == [(1, 4, 2.5), (5, None, 5.0)]
    assert p.items[1].weekdays == "MWF" and p.items[1].inventory_item_id == 7 and p.items[1].dose is None


def test_required_fields():
    _, e = parse_protocol_form(F(name=" ", start_date="2026-09-01"), **IDS)
    assert set(e) >= {"name", "goal", "items"}


def test_unknown_goal_rejected():
    _, e = parse_protocol_form(base(goal=["fat-loss", "nope"]), **IDS)
    assert "goal" in e


def test_dates():
    _, e = parse_protocol_form(base(start_date="nope"), **IDS)
    assert "start_date" in e
    _, e = parse_protocol_form(base(end_date="2026-08-01"), **IDS)
    assert "end_date" in e
    _, e = parse_protocol_form(base(weeks="0"), **IDS)
    assert "weeks" in e


def test_item_rules():
    _, e = parse_protocol_form(base(items__0__dose="0"), **IDS)
    assert "items-0-dose" in e
    _, e = parse_protocol_form(base(items__0__frequency="every_n_days", items__0__every_n_days="1"), **IDS)
    assert "items-0-every_n_days" in e
    _, e = parse_protocol_form(base(items__0__frequency="weekdays"), **IDS)
    assert "items-0-weekdays" in e
    _, e = parse_protocol_form(base(items__0__peptide_id="99"), **IDS)
    assert "items-0-peptide_id" in e
    _, e = parse_protocol_form(base(items__0__inventory_item_id="8"), **IDS)
    assert "items-0-inventory_item_id" in e


def test_new_peptide_name():
    p, e = parse_protocol_form(base(items__1__new_name="  KPV-X  "), **IDS)
    assert e == {} and p.items[1].new_name == "KPV-X" and p.items[1].peptide_id is None
    _, e = parse_protocol_form(base(items__1__new_name="x" * 121), **IDS)
    assert "items-1-new_name" in e


def test_steps_validation_when_titration_on():
    _, e = parse_protocol_form(base(titration="1",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="3", items__0__steps__1__end_week="4", items__0__steps__1__dose="2"), **IDS)
    assert "items-0-steps-0-end_week" in e  # open end only on the last step
    _, e = parse_protocol_form(base(titration="1",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="3", items__0__steps__1__end_week="6", items__0__steps__1__dose="2"), **IDS)
    assert "items-0-steps-1-start_week" in e  # overlap


def test_titration_off_keeps_valid_steps_silently():
    p, e = parse_protocol_form(base(
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="x", items__0__steps__1__dose="2"), **IDS)
    assert e == {} and not p.titration_enabled
    assert [(s.start_week, s.dose) for s in p.items[0].steps] == [(1, 1.0)]


def test_state_round_trip():
    form = base(titration="1", items__0__steps__0__start_week="1", items__0__steps__0__end_week="4",
                items__0__steps__0__dose="bad", items__1__new_name="Thing", items__1__weekdays=["M", "F"])
    s = state_from_form(form)
    assert s["name"] == "Cut" and s["titration"] is True and s["goals"] == ["fat-loss"]
    assert s["items"][0]["steps"][0]["dose"] == "bad"
    assert s["items"][1]["new_name"] == "Thing" and s["items"][1]["weekdays"] == "MF"
