"""Security follow-ups (red team 2): shared vendor flag, headers on errors, garbage form bodies, ASCII-only numbers, the request
deadline, the legacy JSON import, and the AI "own server" address policy. No test touches the network (a loopback server is local)."""

import http.server
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app import ai_summary, netguard
from app.db import SessionLocal
from app.main import app
from app.models import Order, User, Vendor
from app.routers._ints import parse_int
from tests.test_security_cross_user import _signed_in_b


def _quiet(client):
    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.update(client.cookies)
    return c


# ---------------------------------------------------------------- shared vendor recommendation flag

def test_only_the_creator_or_an_admin_clears_a_vendors_recommendation(client, me, db):
    v = Vendor(name="Followup Vendor One", created_by_id=me, recommended=False)
    db.add(v)
    db.commit()
    b = _signed_in_b()
    assert b.post(f"/vendors/{v.id}/clear-recommendation", data={"next": "/vendors"}).status_code == 404
    db.refresh(v)
    assert v.recommended is False
    assert client.post(f"/vendors/{v.id}/clear-recommendation", data={"next": "/vendors"}, follow_redirects=False).status_code == 303
    db.refresh(v)
    assert v.recommended is None


def test_the_remove_control_is_hidden_from_a_non_editor(client, me, db):
    v = Vendor(name="Followup Vendor Two", created_by_id=me, recommended=True)
    db.add(v)
    db.commit()
    b = _signed_in_b()
    assert f"/vendors/{v.id}/clear-recommendation" not in b.get(f"/vendors/{v.id}").text
    assert f"/vendors/{v.id}/clear-recommendation" not in b.get("/vendors").text
    assert f"/vendors/{v.id}/clear-recommendation" in client.get(f"/vendors/{v.id}").text


# ---------------------------------------------------------------- errors carry the headers

def test_an_unhandled_error_has_the_security_headers_and_a_plain_body(client, me, db, monkeypatch, caplog):
    v = Vendor(name="Followup Vendor Four", created_by_id=me)
    db.add(v)
    db.commit()
    import app.routers.vendors as vr
    monkeypatch.setattr(vr, "_detail_context", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="amide.errors"):
        r = _quiet(client).get(f"/vendors/{v.id}?secret=abc")
    assert r.status_code == 500
    assert "content-security-policy" in r.headers and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.text == "Internal Server Error" and "Traceback" not in r.text and "ZeroDivision" not in r.text
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "amide.errors"]
    assert len(lines) == 1 and "ZeroDivisionError" in lines[0] and f"/vendors/{v.id}" in lines[0] and "secret" not in lines[0]


def test_http_errors_are_unchanged(client):
    r = _quiet(client).get("/vendors/999999")
    assert r.status_code == 404 and "content-security-policy" in r.headers and "Vendor not found" in r.text


# ---------------------------------------------------------------- garbage form bodies

GARBAGE = {"content-type": "multipart/form-data; boundary=x"}


@pytest.mark.parametrize("path", ["/login", "/register", "/vendors"])
def test_a_garbage_multipart_body_is_a_400_with_the_headers(client, path):
    if path == "/vendors":
        c = _quiet(client)
    else:
        c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        c.post("/notice", data={"understand": "1"})
    r = c.post(path, content=b"a" * 300, headers=GARBAGE)
    assert 400 <= r.status_code < 500
    assert "content-security-policy" in r.headers


def test_a_multipart_body_with_no_boundary_is_a_4xx(client):
    r = _quiet(client).post("/vendors", content=b"x", headers={"content-type": "multipart/form-data"})
    assert 400 <= r.status_code < 500


# ---------------------------------------------------------------- ASCII digits only

@pytest.mark.parametrize("value,expected", [("12", 12), ("0", 0), ("²", None), ("٣", None), ("1" * 19, None), ("", None), ("-1", None),
                                            (" 1", None), ("1.5", None), (None, None), ("9" * 18, 10**18 - 1)])
def test_parse_int_takes_only_plain_ascii_digits(value, expected):
    assert parse_int(value) == expected


@pytest.mark.parametrize("junk", ["%C2%B2", "%D9%A3", "9" * 40])
def test_unicode_and_huge_numbers_in_query_strings_never_500(client, me, db, junk):
    v = Vendor(name="Followup Vendor Five", created_by_id=me)
    db.add(v)
    db.commit()
    c = _quiet(client)
    for url in (f"/vendors/{v.id}?import={junk}", f"/inventory?labels={junk}"):
        assert c.get(url).status_code < 500


@pytest.mark.parametrize("junk", ["²", "٣", "9" * 40])
def test_unicode_digits_in_form_fields_never_500(client, junk):
    c = _quiet(client)
    assert c.post("/vendors", data={"name": "Followup Vendor Six", "payment_type_ids": junk, "contacts-0-method_type_id": junk,
                                    "contacts-0-value": "x"}).status_code < 500
    assert c.post("/dashboard/dismiss", data={"key": f"lowstock:{junk}", "days": "7"}).status_code < 500


# ---------------------------------------------------------------- the total deadline

class _Trickle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "100")
        self.end_headers()
        try:
            for _ in range(20):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.3)
        except OSError:
            pass

    def log_message(self, *a):
        pass


def test_safe_open_stops_a_server_that_trickles_bytes():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        start = time.monotonic()
        with pytest.raises(netguard.UnsafeURL):
            with netguard.safe_open(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/"), 1.0, allow_loopback=True) as r:
                r.read()
        assert time.monotonic() - start < 2.5
        start = time.monotonic()
        with pytest.raises(netguard.TooSlow):
            with netguard.safe_open(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/"), 30, allow_loopback=True, deadline=0.8) as r:
                r.read()
        assert time.monotonic() - start < 2.5
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- legacy JSON import

def _import(client, payload):
    return client.post("/backup/import", files={"file": ("x.json", json.dumps(payload, allow_nan=True), "application/json")}, follow_redirects=False)


def test_legacy_import_drops_a_bad_tracking_site_and_keeps_a_good_one(client, db):
    orders = [{"order_date": "2026-01-01", "quantity": 1, "tracking_site": site} for site in ("javascript:alert(1)", "data:text/html,x", "https://track.example.test/1")]
    assert _import(client, {"inventory": [{"name": "Followup Peptide A", "orders": orders}]}).status_code == 303
    stored = {o.tracking_site for o in db.query(Order).all()}
    assert "javascript:alert(1)" not in stored and "data:text/html,x" not in stored and "https://track.example.test/1" in stored


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5, 10**30, "x", None, True])
def test_legacy_import_survives_bad_numbers(client, db, bad):
    row = {"name": "Followup Peptide B", "count": bad, "cost": bad, "reconstituted_count": bad, "sold_count": bad, "vial_size_mg": bad, "volume_ml": bad,
           "orders": [{"order_date": "2026-01-01", "quantity": bad, "cost": bad, "tax": bad, "shipping": bad}],
           "sales": [{"quantity": bad, "sale_date": "2026-01-02", "price": bad}]}
    r = _import(client, {"inventory": [row]})
    assert r.status_code in (303, 422)


def test_legacy_import_with_a_broken_row_says_so_and_adds_nothing(client, db):
    before = db.query(Order).count()
    r = _import(client, {"inventory": [{"name": "Followup Peptide C", "orders": [{"order_date": "not a date", "quantity": 1}]}]})
    assert r.status_code == 422 and db.query(Order).count() == before


# ---------------------------------------------------------------- AI "my own server" policy

@pytest.fixture
def clean_ai(client, me):
    yield
    with SessionLocal() as s:
        for u in s.query(User).all():
            u.ai_provider = u.ai_model = u.ai_base_url = u.ai_api_key = None
        s.commit()


def _choose(c, **form):
    return c.post("/settings/ai", data=form, follow_redirects=False)


def test_a_non_admin_may_not_save_a_private_address(client, clean_ai, monkeypatch):
    monkeypatch.delenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", raising=False)
    b = _signed_in_b()
    for base in ("http://192.168.1.5:11434/v1", "http://10.0.0.2/v1", "http://127.0.0.1:11434/v1", "http://localhost:11434/v1", "http://[::1]/v1", "http://ollama/v1"):
        r = _choose(b, ai_provider="local", ai_model="m", ai_base_url=base)
        assert r.status_code == 422 and "AMIDE_AI_LOCAL_ALLOWED_HOSTS" in r.text, base
    assert _choose(client, ai_provider="local", ai_model="m", ai_base_url="http://192.168.1.5:11434/v1").status_code == 303     # the admin may


def test_a_non_admin_may_use_a_public_address(client, clean_ai, monkeypatch):
    monkeypatch.delenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", raising=False)
    b = _signed_in_b()
    assert _choose(b, ai_provider="local", ai_model="m", ai_base_url="http://93.184.216.34/v1").status_code == 303


def test_the_allowed_list_decides_for_every_account(client, clean_ai, monkeypatch):
    monkeypatch.setenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", "192.168.1.0/24")
    b = _signed_in_b()
    assert _choose(b, ai_provider="local", ai_model="m", ai_base_url="http://192.168.1.5/v1").status_code == 303
    assert _choose(b, ai_provider="local", ai_model="m", ai_base_url="http://10.0.0.2/v1").status_code == 422
    assert _choose(client, ai_provider="local", ai_model="m", ai_base_url="http://93.184.216.34/v1").status_code == 422


def test_a_saved_private_address_is_refused_at_send_time_for_a_non_admin(client, clean_ai, monkeypatch):
    monkeypatch.delenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", raising=False)
    sent = []
    monkeypatch.setattr(ai_summary, "post_json", lambda *a: sent.append(a) or {"choices": [{"message": {"content": "ok"}}]})
    with SessionLocal() as s:
        plain = User(username="AiPlain", username_key="aiplain", password_hash="x", is_admin=False,
                     ai_provider="local", ai_model="m", ai_base_url="http://192.168.1.5:11434/v1")
        s.add(plain)
        s.commit()
        try:
            with pytest.raises(ai_summary.AiError, match="AMIDE_AI_LOCAL_ALLOWED_HOSTS"):
                ai_summary.ask(plain, "hello")
            assert sent == []
            plain.is_admin = True
            assert ai_summary.ask(plain, "hello") == "ok"
        finally:
            s.delete(plain)
            s.commit()


def test_the_send_guard_turns_private_addresses_off_for_a_non_admin(monkeypatch):
    """post_json itself (the DNS-pinned connection) refuses a private answer when the account may not reach private addresses."""
    seen = {}

    def fake_open(request, timeout, allow_private=False, **k):
        seen["private"], seen["loopback"] = allow_private, k.get("allow_loopback")
        raise netguard.UnsafeURL("x")

    monkeypatch.setattr(netguard, "safe_open", fake_open)
    token = ai_summary._deny_private.set(True)
    try:
        with pytest.raises(ai_summary.AiError):
            ai_summary.post_json("http://ollama.example.test/v1/chat/completions", {}, {})
    finally:
        ai_summary._deny_private.reset(token)
    assert seen == {"private": False, "loopback": False}


def test_every_failure_to_reach_the_agent_reads_the_same(monkeypatch):
    for error in (netguard.UnsafeURL("an address on a private network is not allowed"), netguard.TooSlow("slow"), ConnectionRefusedError(),
                  TimeoutError(), urllib.error.URLError("no route")):
        def fake_open(*a, _e=error, **k):
            raise _e
        monkeypatch.setattr(netguard, "safe_open", fake_open)
        with pytest.raises(ai_summary.AiError) as caught:
            ai_summary.post_json("http://192.168.1.5/v1/chat/completions", {}, {})
        assert str(caught.value) == "The agent could not be reached."


def test_the_custom_banner_is_served_sandboxed(client):
    from app import config
    config.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    path = config.BRANDING_DIR / "banner.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>', encoding="utf-8")
    try:
        r = client.get("/branding/banner")
        assert r.status_code == 200 and r.headers["content-security-policy"].startswith("sandbox")
        assert r.headers["x-content-type-options"] == "nosniff"
    finally:
        path.unlink(missing_ok=True)
