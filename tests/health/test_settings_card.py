import re
from datetime import datetime, timedelta

from app.models import HealthEvent, HealthRaw, HealthReading, naive_utcnow
from app.routers import apple_health


def add_raw(db, owner, ago_minutes=3, status="parsed", note=None, size=2048, sha="a"):
    db.add(HealthRaw(owner_id=owner, received_at=naive_utcnow() - timedelta(minutes=ago_minutes), sha256=sha * 64, size_bytes=size,
                     file_name=f"{sha}.json", status=status, note=note))
    db.commit()


def test_card_shows_waiting_then_last_received(client, db, me):
    page = client.get("/settings").text
    assert "Waiting for first data" in page and "Create token" in page
    assert "data-open-health-choices" not in page and "Change what is shown" not in page      # nothing received: the dialog renders nothing, so no button
    add_raw(db, me, ago_minutes=3)
    for i in range(3):
        db.add(HealthReading(owner_id=me, metric="step_count", start_at=datetime(2024, 2, 6, i), end_at=datetime(2024, 2, 6, i + 1),
                             grain="hour", value=1.0, unit="count", source="Phone"))
    db.commit()
    page = client.get("/settings").text
    assert "Waiting for first data" not in page
    assert "Last received 3 minutes ago, 3 readings" in page
    assert "data-open-health-choices" in page and "Change what is shown" in page


def test_change_button_also_shows_when_only_events_arrived(client, db, me):
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="w", start_at=datetime(2024, 2, 6), name="x"))
    db.commit()
    assert "data-open-health-choices" in client.get("/settings").text


def test_new_token_secret_is_shown_once_and_the_address_uses_the_request_host(client, db, me):
    client.post("/settings/apple-health/token", data={"label": "Phone"}, follow_redirects=False)      # the redirect target is the one look
    page = client.get("/settings").text
    secret = re.search(r"Authorization: Bearer (\S+?)<", page).group(1)
    assert secret.startswith("amide_hlt_")
    assert "http://testserver/api/ingest/health" in page
    assert page.count(secret) >= 2        # the shown line and its copy button
    assert client.cookies.get("amide_session")       # the browser session the secret was kept for
    again = client.get("/settings").text
    assert secret not in again
    assert "Phone" in again and "Revoke" in again       # later only the label and prefix remain
    assert apple_health.take_new_token(client.cookies.get("amide_session")) is None


def test_event_counts_listed_for_stored_only_kinds(client, db, me):
    for kind, n in (("workout", 2), ("ecg", 1)):
        for i in range(n):
            db.add(HealthEvent(owner_id=me, kind=kind, external_id=f"{kind}{i}", start_at=datetime(2024, 2, 6), name="x"))
    db.commit()
    data = apple_health.card_data(db, me, "http://testserver/")
    assert data["event_counts"][0] == (2, "workout", "workouts") and data["event_counts"][3] == (1, "ECG", "ECGs") and data["event_total"] == 3
    assert data["ingest_url"] == "http://testserver/api/ingest/health" and data["header_line"] is None
    page = client.get("/settings").text
    assert "workout" in page.lower() and "ecg" in page.lower()


def test_recent_receipts_listed_newest_first_max_ten(db, me):
    for i in range(12):
        add_raw(db, me, ago_minutes=100 - i, sha=f"{i:x}", status="flagged" if i == 11 else "parsed", note="odd" if i == 11 else None)
    data = apple_health.card_data(db, me, "http://testserver")
    assert len(data["recent"]) == 10 and data["recent"][0].status == "flagged"


def test_card_never_contains_a_domain_other_than_the_request_host(client, db, me):
    page = client.get("/settings").text
    start = page.index('id="apple-health"')
    end = page.index("</section>", start) if "</section>" in page[start:] else len(page)
    text = re.sub(r"<[^>]+>", " ", page[start:start + 20000])
    text = text.split("Hume")[0]
    hosts = set(re.findall(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|app|dev|ai)\b", text.lower()))
    assert hosts <= {"example.com"}, hosts
    assert "testserver" in page
    card = page[start:start + 20000].split("Hume")[0]
    urls = re.findall(r"https?://([^/\s\"'<>:]+)", card)          # every address in the card, in text and in attributes alike
    assert urls and set(urls) <= {"testserver"}, urls             # only the request host (the card links to no documentation)


def test_unclaimed_token_secrets_expire_and_are_dropped_on_the_next_write(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(apple_health.time, "monotonic", lambda: clock[0])
    apple_health._new_tokens.clear()
    apple_health._remember("s1", "secret-one", "a")
    clock[0] += apple_health.NEW_TOKEN_TTL + 1
    apple_health._remember("s2", "secret-two", "b")
    assert "s1" not in apple_health._new_tokens                # swept by the write
    assert apple_health.take_new_token("s2") == ("secret-two", "b")
    apple_health._remember("s3", "secret-three", "c")
    clock[0] += apple_health.NEW_TOKEN_TTL + 1
    assert apple_health.take_new_token("s3") is None           # too old to show


def test_card_states_the_64_mb_cap_and_the_setting_that_raises_it(client):
    page = client.get("/settings").text
    card = page[page.index('id="apple-health"'):]
    assert "64 MB" in card and "AMIDE_HEALTH_MAX_MB" in card and "512 MB" not in card


def test_card_counts_every_stored_kind_including_none_yet(client, db, me):
    add_raw(db, me)
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="w", start_at=datetime(2024, 2, 6), name="x"))
    db.add(HealthEvent(owner_id=me, kind="ecg", external_id="e", start_at=datetime(2024, 2, 6)))
    db.commit()
    card = client.get("/settings").text.split('id="apple-health"')[1]
    line = re.search(r"Also kept: ([^<]+)\.", card).group(1)
    assert line == ("1 workout, 0 symptoms, 0 states of mind, 1 ECG, 0 heart rate notifications, 0 cycle tracking entries, 0 medications")


def test_revoke_asks_first(client, db, me):
    client.post("/settings/apple-health/token", data={"label": "Phone"}, follow_redirects=False)
    card = client.get("/settings").text.split('id="apple-health"')[1]
    assert re.search(r'<form method="post" action="/settings/apple-health/token/\d+/revoke"[^>]*data-confirm="[^"]+"', card)


def test_the_card_script_asks_before_a_confirmed_form_is_sent():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "health_card.js").read_text(encoding="utf-8")
    assert 'form[data-confirm]' in js and "window.confirm" in js
