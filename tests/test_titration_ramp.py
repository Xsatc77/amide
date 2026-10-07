"""The titration helper: start dose, increase, weeks per step and a target become the step rows."""

import pytest

from app.protocols.titration import ramp


def test_a_ramp_climbs_in_equal_steps_and_the_last_step_goes_on():
    assert ramp(0.25, 0.25, 4, 1.0) == [
        {"start_week": 1, "end_week": 4, "dose": 0.25}, {"start_week": 5, "end_week": 8, "dose": 0.5},
        {"start_week": 9, "end_week": 12, "dose": 0.75}, {"start_week": 13, "end_week": None, "dose": 1.0}]


def test_the_last_step_is_the_target_even_when_the_increase_overshoots_it():
    assert ramp(2.5, 2.0, 4, 6.0)[-1] == {"start_week": 9, "end_week": None, "dose": 6.0}
    assert [s["dose"] for s in ramp(2.5, 2.0, 4, 6.0)] == [2.5, 4.5, 6.0]


def test_start_equal_to_target_is_one_open_step():
    assert ramp(5, 1, 2, 5) == [{"start_week": 1, "end_week": None, "dose": 5}]


def test_a_single_week_per_step():
    assert [(s["start_week"], s["end_week"]) for s in ramp(1, 1, 1, 3)] == [(1, 1), (2, 2), (3, None)]


@pytest.mark.parametrize("args", [(0, 1, 1, 2), (-1, 1, 1, 2), (1, 0, 1, 2), (1, 1, 0, 2), (1, 1, 1, 0.5), (1, 1.5, 1, 2000), ("x", 1, 1, 2), (1, 1, 2.5, 5)])
def test_nonsense_is_refused(args):
    with pytest.raises(ValueError):
        ramp(*args)


def test_the_endpoint_returns_the_steps_or_a_422(client, db):
    r = client.get("/protocols/titration-steps", params={"start": "0.25", "increase": "0.25", "weeks": "4", "target": "1"})
    assert r.status_code == 200 and [s["dose"] for s in r.json()["steps"]] == [0.25, 0.5, 0.75, 1.0] and r.json()["steps"][-1]["end_week"] is None
    bad = client.get("/protocols/titration-steps", params={"start": "5", "increase": "1", "weeks": "4", "target": "1"})
    assert bad.status_code == 422 and "target" in bad.json()["detail"].lower()
    assert client.get("/protocols/titration-steps", params={"start": "x"}).status_code == 422
