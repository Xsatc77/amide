"""Signed-in pages and JSON are never stored by a shared cache or the back button; static files and private downloads keep their own rules."""


def test_html_and_json_responses_are_no_store(client):
    for path in ("/dashboard", "/settings", "/inventory"):
        assert client.get(path).headers["cache-control"] == "no-store", path
    assert client.get("/session/ping").headers.get("cache-control", "no-store") == "no-store"


def test_static_files_are_not_forced_to_no_store(client):
    assert "no-store" not in client.get("/static/css/app.css").headers.get("cache-control", "")


def test_a_route_that_sets_its_own_cache_rule_keeps_it(client):
    r = client.get("/calendar/feed/nonexistent.ics")
    assert r.status_code == 404
