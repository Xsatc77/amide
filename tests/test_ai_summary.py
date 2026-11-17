"""The Progress Review AI summary: the agent is chosen in Settings, only the page's numbers are sent, the answer is kept per date range."""

from datetime import date, datetime, timedelta

import pytest

from app import ai_summary, units
from app.db import SessionLocal
from app.models import BodyMeasurement, ProgressSummary, User
from app.progress import review

START = date(2030, 1, 1)            # invented dates and numbers throughout
END = START + timedelta(days=28)
RANGE = f"start={START.isoformat()}&end={END.isoformat()}"
FORM = {"start": START.isoformat(), "end": END.isoformat()}


def _weigh(me, day, **v):
    with SessionLocal() as s:
        s.add(BodyMeasurement(owner_id=me, measured_at=day, recorded_at=datetime.combine(day, datetime.min.time()), **v))
        s.commit()


def _seed(me):
    for week, weight in enumerate([200.0, 195.0, 190.0]):
        _weigh(me, START + timedelta(days=7 * week), weight_lbs=weight, scale_fat_mass_lb=60.0 - 3 * week, scale_muscle_mass_lb=120.0 - 0.4 * week)


def _choose(client, **form):
    return client.post("/settings/ai", data=form, follow_redirects=False)


@pytest.fixture
def agent(monkeypatch):
    """Replace the network call: record what would have been sent, answer with a fixed text."""
    calls = []

    def fake(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "Headline\nWeight is down 10 lb."}]}
        return {"choices": [{"message": {"content": "Headline\nWeight is down 10 lb."}}]}
    monkeypatch.setattr(ai_summary, "post_json", fake)
    return calls


@pytest.fixture(autouse=True)
def _clean_agent(client, me):
    yield
    with SessionLocal() as s:
        s.query(ProgressSummary).delete()
        user = s.get(User, me)
        user.ai_provider = user.ai_model = user.ai_base_url = user.ai_api_key = None
        s.query(BodyMeasurement).filter_by(owner_id=me).delete()
        s.commit()


def test_without_an_agent_the_page_points_to_settings_and_has_no_button(client, db, me):
    t = client.get(f"/progress?{RANGE}").text
    assert 'href="/settings#ai"' in t and "Generate AI Summary" not in t


def test_settings_saves_an_agent_and_never_shows_the_key_again(client, db, me):
    assert _choose(client, ai_provider="anthropic", ai_api_key="sk-test-secret-123").status_code == 303
    page = client.get("/settings").text
    assert "sk-test-secret-123" not in page and "A key is saved for your account." in page
    with SessionLocal() as s:
        user = s.get(User, me)
        assert (user.ai_provider, user.ai_api_key) == ("anthropic", "sk-test-secret-123")
    assert "Generate AI Summary" in client.get(f"/progress?{RANGE}").text


def test_settings_asks_for_what_each_agent_needs(client, db, me):
    assert _choose(client, ai_provider="anthropic").status_code == 422                          # no key
    assert _choose(client, ai_provider="openai", ai_api_key="k").status_code == 422             # no model
    assert _choose(client, ai_provider="local", ai_model="llama3").status_code == 422           # no server address
    assert _choose(client, ai_provider="local", ai_model="llama3", ai_base_url="ftp://x").status_code == 422
    assert _choose(client, ai_provider="nonsense").status_code == 422
    assert _choose(client, ai_provider="local", ai_model="llama3", ai_base_url="http://192.168.1.5:11434/v1").status_code == 303


def test_a_key_does_not_carry_over_to_a_different_agent_and_remove_clears_everything(client, db, me):
    _choose(client, ai_provider="anthropic", ai_api_key="sk-test-secret-123")
    assert _choose(client, ai_provider="openai", ai_model="m").status_code == 422               # the Claude key is not reused for OpenAI
    _choose(client, ai_provider="anthropic", ai_model="x", ai_api_key="")                      # same agent: the saved key stays
    with SessionLocal() as s:
        assert s.get(User, me).ai_api_key == "sk-test-secret-123"
    _choose(client, remove="1")
    with SessionLocal() as s:
        user = s.get(User, me)
        assert (user.ai_provider, user.ai_model, user.ai_base_url, user.ai_api_key) == (None, None, None, None)


def test_the_summary_sends_only_the_pages_numbers_and_is_saved_and_shown(client, db, me, agent):
    _seed(me)
    _choose(client, ai_provider="anthropic", ai_api_key="sk-test-secret-123")
    r = client.post("/progress/summary", data=FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/progress?{RANGE}")
    [call] = agent
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-test-secret-123" and call["body"]["model"] == "claude-sonnet-5-5"
    sent = call["body"]["messages"][0]["content"]
    assert "200" in sent and "190" in sent and "Period: 2030-01-01 to 2030-01-29" in sent
    with SessionLocal() as s:
        user = s.get(User, me)
        assert user.username not in sent and "sk-test-secret-123" not in sent
        if user.email:
            assert user.email not in sent
    page = client.get(f"/progress?{RANGE}").text
    assert "Weight is down 10 lb." in page and "claude-sonnet-5-5" in page and "Regenerate Summary" in page
    assert "Weight is down 10 lb." not in client.get(f"/progress?start={START.isoformat()}&end={(END + timedelta(days=1)).isoformat()}").text   # another range has none


def test_regenerating_replaces_the_saved_summary_for_that_range(client, db, me, agent):
    _seed(me)
    _choose(client, ai_provider="anthropic", ai_api_key="k-12345")
    client.post("/progress/summary", data=FORM)
    client.post("/progress/summary", data=FORM)
    assert len(agent) == 2
    with SessionLocal() as s:
        assert s.query(ProgressSummary).filter_by(owner_id=me).count() == 1


def test_an_openai_style_server_gets_a_chat_request_and_a_local_one_needs_no_key(client, db, me, agent):
    _seed(me)
    _choose(client, ai_provider="local", ai_model="llama3", ai_base_url="http://192.168.1.5:11434/v1")
    assert client.post("/progress/summary", data=FORM, follow_redirects=False).status_code == 303
    [call] = agent
    assert call["url"] == "http://192.168.1.5:11434/v1/chat/completions" and call["headers"] == {}
    assert [m["role"] for m in call["body"]["messages"]] == ["system", "user"] and call["body"]["model"] == "llama3"


def test_a_failed_call_says_why_and_never_shows_the_key(client, db, me, monkeypatch):
    _seed(me)
    _choose(client, ai_provider="anthropic", ai_api_key="sk-test-secret-123")

    def refuse(url, headers, body):
        raise ai_summary.AiError("The agent refused the key. Check it in Settings.")
    monkeypatch.setattr(ai_summary, "post_json", refuse)
    r = client.post("/progress/summary", data=FORM)
    assert r.status_code == 502 and "The agent refused the key." in r.text and "sk-test-secret-123" not in r.text
    with SessionLocal() as s:
        assert s.query(ProgressSummary).count() == 0


def test_the_facts_carry_the_pages_numbers_in_the_persons_units(client, db, me):
    _seed(me)
    with SessionLocal() as s:
        user = s.get(User, me)
        text = ai_summary.facts(review.build(s, user, START, END), units.for_user(user))
    assert "Weight: 200 -> 190 lbs" in text and "Not established:" in text and "Average weight by week:" in text


def test_the_muscle_loss_flag_rounds_its_numbers(client, db, me):
    _weigh(me, START, weight_lbs=200.0, scale_fat_mass_lb=60.0, scale_muscle_mass_lb=120.0)
    _weigh(me, START + timedelta(days=7), weight_lbs=190.0, scale_fat_mass_lb=45.4, scale_muscle_mass_lb=118.8)
    with SessionLocal() as s:
        r = review.build(s, s.get(User, me), START, END)
    text = next(f["text"] for f in r["flags"] if f["key"] == "muscle_loss")
    assert "fell 1.2 lbs while fat mass fell 14.6 lbs" in text
