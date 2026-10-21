from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import HealthEvent, HealthMetricChoice, HealthReading, IngestToken, User, naive_utcnow
from photo_helpers import other_client


def enable(db, owner, *metrics):
    db.add_all([HealthMetricChoice(owner_id=owner, metric=m, enabled=True) for m in metrics])
    db.commit()


def test_tab_hidden_without_data_or_token_and_shown_with_either(client, db, me):
    assert "tab=apple" not in client.get("/measurements").text
    db.add(IngestToken(owner_id=me, label="Phone", prefix="amide_hlt_ab", token_hash="h" * 64, scope="health"))
    db.commit()
    assert "tab=apple" in client.get("/measurements").text
    db.query(IngestToken).delete()
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="w", start_at=datetime(2024, 2, 6), name="Walk"))
    db.commit()
    assert "tab=apple" in client.get("/measurements").text


def test_a_watcher_token_does_not_show_the_tab(client, db, me):
    db.add(IngestToken(owner_id=me, label="W", prefix="amide_w_ab", token_hash="g" * 64, scope="watcher"))
    db.commit()
    assert "tab=apple" not in client.get("/measurements").text


def test_tab_lists_workouts_symptoms_and_state_of_mind(client, db, me):
    now = naive_utcnow()
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="w1", start_at=now - timedelta(days=2), name="Zephyr Run",
                       payload={"duration": 1800, "distance": {"qty": 5.2, "units": "km"}, "activeEnergyBurned": {"qty": 310, "units": "kcal"}}))
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="w2", start_at=now - timedelta(days=1), name="Borealis Swim", payload={}))
    db.add(HealthEvent(owner_id=me, kind="symptom", external_id="s1", start_at=now - timedelta(days=1), name="Headache"))
    db.add(HealthEvent(owner_id=me, kind="state_of_mind", external_id="m1", start_at=now - timedelta(days=1), name="Calm"))
    db.commit()
    enable(db, me, "event:symptom", "event:state_of_mind")
    page = client.get("/measurements?tab=apple&range=1mo").text
    assert page.index("Borealis Swim") < page.index("Zephyr Run")          # newest first
    assert "5.2" in page and "30 min" in page and "310" in page
    assert "Headache" in page and "Calm" in page


def test_symptoms_stay_hidden_until_switched_on(client, db, me):
    db.add(HealthEvent(owner_id=me, kind="symptom", external_id="s1", start_at=naive_utcnow(), name="Headache"))
    db.commit()
    assert "Headache" not in client.get("/measurements?tab=apple").text


def test_tab_charts_an_enabled_metric_with_the_range_dropdown(client, db, me):
    db.add(HealthReading(owner_id=me, metric="step_count", start_at=naive_utcnow(), end_at=naive_utcnow(), grain="day", value=4321.0, unit="count", source="Phone"))
    db.commit()
    enable(db, me, "step_count")
    page = client.get("/measurements?tab=apple").text
    assert "Steps" in page and "4321" in page and 'name="range"' in page and 'value="apple"' in page
    assert client.get("/measurements?tab=apple&range=nonsense").status_code == 200


def test_other_persons_data_is_not_shown(client, db, me):
    with other_client("applepal") as other:
        with SessionLocal() as s:
            oid = s.query(User).filter_by(username_key="applepal").one().id
            s.add(HealthEvent(owner_id=oid, kind="workout", external_id="x", start_at=naive_utcnow(), name="Hidden Hike"))
            s.add(HealthReading(owner_id=oid, metric="step_count", start_at=naive_utcnow(), end_at=naive_utcnow(), grain="day", value=77777.0, unit="count", source="P"))
            s.add(HealthMetricChoice(owner_id=oid, metric="step_count", enabled=True))
            s.commit()
        assert "Hidden Hike" in other.get("/measurements?tab=apple").text           # their own page, as a sanity check
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="mine", start_at=naive_utcnow(), name="Mine"))
    db.commit()
    page = client.get("/measurements?tab=apple").text
    assert "Hidden Hike" not in page and "77777" not in page and "Mine" in page


def test_whole_numbers_show_without_a_decimal_and_count_is_not_a_unit(client, db, me):
    now = naive_utcnow()
    db.add(HealthReading(owner_id=me, metric="step_count", start_at=now, end_at=now, grain="day", value=10.0, unit="count", source="Phone"))
    db.add(HealthReading(owner_id=me, metric="weight_body_mass", start_at=now, end_at=now, grain="day", value=170.5, unit="lb", source="Phone"))
    db.commit()
    enable(db, me, "step_count", "weight_body_mass")
    page = client.get("/measurements?tab=apple").text
    assert ">10.0" not in page and "10 count" not in page and "10.0 count" not in page
    assert 'data-label="Today">10<' in page
    assert "170.5 lb" in page
