from datetime import date
from types import SimpleNamespace as NS

from app.protocols.status import Status, current_step, current_week, day_number, merge_stacks, protocol_status

T = date(2026, 9, 22)


def P(**kw):
    base = dict(start_date=date(2026, 9, 1), end_date=None, ended_on=None, paused=False)
    return NS(**{**base, **kw})


def test_active_ongoing():
    assert protocol_status(P(), T) is Status.ACTIVE


def test_end_date_today_is_active():
    assert protocol_status(P(end_date=T), T) is Status.ACTIVE


def test_end_date_past_is_ended():
    assert protocol_status(P(end_date=date(2026, 9, 21)), T) is Status.ENDED


def test_ended_on_wins_over_everything():
    assert protocol_status(P(ended_on=T, paused=True, start_date=date(2026, 10, 1)), T) is Status.ENDED


def test_paused_beats_scheduled():
    assert protocol_status(P(paused=True, start_date=date(2026, 10, 1)), T) is Status.PAUSED


def test_scheduled():
    assert protocol_status(P(start_date=date(2026, 9, 23)), T) is Status.SCHEDULED


def test_day_and_week():
    s = date(2026, 9, 1)
    assert day_number(s, s) == 1 and day_number(s, T) == 22
    assert current_week(s, s) == 1 and current_week(s, date(2026, 9, 7)) == 1 and current_week(s, date(2026, 9, 8)) == 2
    assert day_number(T, s) is None and current_week(T, s) is None


def test_current_step():
    steps = [NS(start_week=1, end_week=4, dose=2.5), NS(start_week=5, end_week=None, dose=5)]
    assert current_step(steps, 1).dose == 2.5
    assert current_step(steps, 4).dose == 2.5
    assert current_step(steps, 30).dose == 5
    assert current_step(steps, None) is None
    assert current_step([NS(start_week=3, end_week=4, dose=1)], 1) is None


def test_merge_stacks_dedupes_in_goal_order():
    stacks = {"a": [1, 2, 3], "b": [4, 2, 5]}
    assert merge_stacks(["b", "a"], stacks) == [4, 2, 5, 1, 3]
    assert merge_stacks(["zzz"], stacks) == []
