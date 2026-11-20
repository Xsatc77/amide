"""The session cookie's Secure flag behind a TLS-terminating proxy that Amide does not (yet) trust for forwarded headers."""

from fastapi.testclient import TestClient

from app.main import app


def set_cookie(headers=None):
    with TestClient(app) as anon:
        r = anon.post("/notice", data={"understand": "1"}, headers=headers or {}, follow_redirects=False)
        return r.headers.get("set-cookie", "")


def test_a_plain_http_visit_keeps_a_cookie_that_works_on_the_lan():
    assert "secure" not in set_cookie().lower()


def test_the_proxy_saying_https_makes_the_cookie_secure_without_any_trusted_proxy_setting():
    assert "secure" in set_cookie({"X-Forwarded-Proto": "https"}).lower()
    assert "secure" in set_cookie({"X-Forwarded-Proto": "https, http"}).lower()


def test_a_forwarded_http_or_junk_value_does_not_add_secure():
    assert "secure" not in set_cookie({"X-Forwarded-Proto": "http"}).lower()
    assert "secure" not in set_cookie({"X-Forwarded-Proto": "nonsense"}).lower()
