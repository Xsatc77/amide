"""The Today screen has Quick Exercise and Journal buttons."""

from datetime import date

from app import clock
from app.models import WorkoutLog


def test_today_has_a_quick_exercise_button_and_dialog_and_a_journal_link(client):
    page = client.get("/today").text
    assert 'name="return_to" value="/today"' in page
    assert "Quick Exercise" in page and 'id="quick-activity-dialog"' in page and "workout-quick.js" in page
    assert f'href="/measurements?tab=journal&amp;edit={clock.today().isoformat()}"' in page or f'href="/measurements?tab=journal&edit={clock.today().isoformat()}"' in page


def test_a_quick_exercise_from_today_returns_to_today(client, db):
    r = client.post("/workouts/quick", data={"activity": next(iter(__import__("app.workouts.quick", fromlist=["x"]).ACTIVITIES)), "minutes": "20", "log_date": clock.today().isoformat(), "return_to": "/today"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/today"
    db.query(WorkoutLog).delete()
    db.commit()


def test_return_to_only_accepts_today(client, db):
    activity = next(iter(__import__("app.workouts.quick", fromlist=["x"]).ACTIVITIES))
    r = client.post("/workouts/quick", data={"activity": activity, "minutes": "20", "log_date": clock.today().isoformat(), "return_to": "https://example.com"}, follow_redirects=False)
    assert r.headers["location"] == "/measurements?tab=journal"
    db.query(WorkoutLog).delete()
    db.commit()
