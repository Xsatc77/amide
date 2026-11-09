"""Today: move one workout session to another day, for that session only."""
import html
import re
from datetime import timedelta

from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import WEEKDAY_LETTERS, WorkoutLog, WorkoutMove, WorkoutPlan, WorkoutPlanDay, WorkoutSource
from app.routers.workouts import scheduled_workout_dates, workouts_due_today


def _plan(me, label, weekdays):
    with SessionLocal() as s:
        for p in s.scalars(select(WorkoutPlan).where(WorkoutPlan.owner_id == me)).all():
            s.delete(p)
        s.commit()
        plan = WorkoutPlan(owner_id=me, name="Move Plan", source=WorkoutSource.MANUAL, started_on=clock.today() - timedelta(days=30))
        plan.days.append(WorkoutPlanDay(position=0, label=label, weekdays=weekdays))
        s.add(plan)
        s.commit()
        return plan.days[0].id


def _letter(offset=0):
    return WEEKDAY_LETTERS[(clock.today() + timedelta(days=offset)).weekday()]


def _move(client, day_id, to, frm=None):
    return client.post(f"/workouts/day/{day_id}/move", data={"from_date": (frm or clock.today()).isoformat(), "to_date": to.isoformat()},
                       follow_redirects=False)


def test_a_move_takes_the_session_off_today_and_puts_it_on_the_chosen_day(client, db, me):
    day = _plan(me, "Mover A", _letter())
    tomorrow = clock.today() + timedelta(days=1)
    assert _move(client, day, tomorrow).status_code == 303
    with SessionLocal() as s:
        assert workouts_due_today(s, me, clock.today()) == []
        assert [d.id for d in workouts_due_today(s, me, tomorrow)] == [day]
        dates = scheduled_workout_dates(s, me, clock.today(), clock.today() + timedelta(days=14))
        assert clock.today() not in dates and tomorrow in dates
        assert clock.today() + timedelta(days=7) in dates                  # next week's session is untouched


def test_the_page_offers_only_days_without_a_workout(client, db, me):
    day = _plan(me, "Mover B", _letter() + _letter(1))                      # today and tomorrow are workout days
    text = html.unescape(client.get("/today").text)
    assert "Move" in text and f"/workouts/day/{day}/move" in text
    options = re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"', text.split(f"/workouts/day/{day}/move")[1].split("</form>")[0])
    free = {(clock.today() + timedelta(days=n)).isoformat() for n in range(2, 8)}
    assert options and set(options) <= {(clock.today() + timedelta(days=n)).isoformat() for n in range(1, 15)}
    assert (clock.today() + timedelta(days=1)).isoformat() not in options  # tomorrow already has a workout
    assert free & set(options)


def test_a_day_that_already_has_a_workout_or_is_not_in_the_future_is_refused(client, db, me):
    day = _plan(me, "Mover C", _letter() + _letter(1))
    assert _move(client, day, clock.today() + timedelta(days=1)).status_code == 422
    assert _move(client, day, clock.today()).status_code == 422
    assert _move(client, day, clock.today() - timedelta(days=1)).status_code == 422
    assert _move(client, day, clock.today() + timedelta(days=200)).status_code == 422
    with SessionLocal() as s:
        assert s.scalars(select(WorkoutMove)).all() == []


def test_a_day_that_is_not_due_cannot_be_moved(client, db, me):
    day = _plan(me, "Mover D", _letter(1))
    assert _move(client, day, clock.today() + timedelta(days=3)).status_code == 422


def test_moving_it_again_keeps_the_original_date(client, db, me):
    day = _plan(me, "Mover E", _letter())
    t = clock.today()
    _move(client, day, t + timedelta(days=2))
    _move(client, day, t + timedelta(days=3), frm=t + timedelta(days=2))
    with SessionLocal() as s:
        [m] = s.scalars(select(WorkoutMove)).all()
        assert (m.from_date, m.to_date) == (t, t + timedelta(days=3))


def test_a_logged_day_is_not_offered(client, db, me):
    day = _plan(me, "Mover F", _letter())
    other = clock.today() + timedelta(days=2)
    with SessionLocal() as s:
        s.add(WorkoutLog(owner_id=me, plan_day_id=None, day_label="Free", log_date=other))
        s.commit()
    assert _move(client, day, other).status_code == 422
