"""Red team 2 regression guards: attacks on the web layer, outbound requests, files and shared vendor data that the hardening
resisted. Fast, no real network (only a throw-away server on 127.0.0.1 and literal addresses)."""

import http.server
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app import netguard
from app.backup import load as loader
from app.main import app
from app.models import Vendor
from app.security_headers import safe_redirect
from tests.test_security_cross_user import _signed_in_b

HEADER_NAMES = ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy")


def _has_headers(r):
    return all(h in r.headers for h in HEADER_NAMES)


@pytest.fixture
def vendor(client, me, db):
    v = Vendor(name="Redteam Vendor", created_by_id=me, recommended=True, website="https://redteam.example")
    db.add(v)
    db.commit()
    yield v


@pytest.fixture(scope="module")
def b():
    return _signed_in_b()


# ---------------------------------------------------------------- headers on every kind of answer

def test_headers_on_404_405_static_redirect_and_service_worker(client):
    c = TestClient(app, follow_redirects=False)
    assert _has_headers(c.get("/static/does-not-exist.js"))                    # 404 from the static mount
    assert _has_headers(c.get("/static/js/base.js"))
    assert _has_headers(c.get("/sw.js"))
    assert _has_headers(c.get("/healthz"))
    assert _has_headers(c.get("/dashboard"))                                    # the gate's 303 for an anonymous browser
    assert _has_headers(c.put("/healthz", content=b"x"))                        # 405
    assert _has_headers(client.get("/no/such/page"))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_headers_on_413_for_every_method(client):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        r = client.request(method, "/vendors", content=b"a" * (2 * 1024 * 1024 + 10), headers={"content-type": "application/octet-stream"})
        assert r.status_code == 413 and _has_headers(r), method


def test_a_streamed_body_without_a_length_is_cut_off(client):
    def chunks():
        for _ in range(3):
            yield b"a" * (1024 * 1024)
    r = client.post("/vendors/", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})     # trailing slash, chunked
    assert r.status_code == 413 and _has_headers(r)


def test_a_path_that_looks_like_backup_does_not_get_the_big_cap(client):
    big = b"a" * (3 * 1024 * 1024)
    for path in ("/backupx/open", "/%2fbackup/open", "/Backup/open", "//backup/open"):
        r = client.post(path, content=big, headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code in (404, 413, 405), path


def test_private_files_are_sandboxed_for_the_vendor_qr_and_price_list(client, me, db, vendor, tmp_path):
    from app import config
    from app.models import VendorWallet
    name = "rt2qr.png"
    (config.WALLET_QR_DIR).mkdir(parents=True, exist_ok=True)
    (config.WALLET_QR_DIR / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"<script>alert(1)</script>")
    w = VendorWallet(vendor_id=vendor.id, coin="BTC", address="bc1qredteam", qr_filename=name)
    db.add(w)
    db.commit()
    r = client.get(f"/vendors/{vendor.id}/wallets/{w.id}/qr")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert r.headers["x-content-type-options"] == "nosniff" and "no-store" in r.headers["cache-control"]
    assert r.headers["content-type"] == "image/png"
    assert client.get(f"/vendors/{vendor.id + 999}/wallets/{w.id}/qr").status_code == 404
    assert client.get(f"/vendors/{vendor.id}/wallets/{w.id + 999}/qr").status_code == 404


# ---------------------------------------------------------------- redirects

HOSTILE = ["//evil.test", "/\\evil.test", "https://evil.test", "%2f%2fevil.test", "/%2fevil.test", "/%5cevil.test", "/\t/evil.test",
           "/\r\nSet-Cookie: x=1", "/vendors\r\nX: y", "//∕evil.test", "\\\\evil.test", "javascript:alert(1)", "/vendors/%0d%0aX: y",
           "/%252f%252fevil.test", "http:evil.test", "/vendors/..//evil.test", "⁄⁄evil.test", " //evil.test", "///evil.test"]


@pytest.mark.parametrize("raw", HOSTILE)
@pytest.mark.parametrize("action", ["favorite", "unfavorite", "clear-recommendation"])
def test_vendor_redirect_targets_never_leave_the_site(client, vendor, raw, action):
    c = TestClient(app, follow_redirects=False)
    c.cookies.update(client.cookies)
    r = c.post(f"/vendors/{vendor.id}/{action}", data={"next": raw})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "\r" not in loc and "\n" not in loc
    joined = urllib.parse.urlsplit(urllib.parse.urljoin("https://amide.example/vendors", loc))
    assert joined.netloc == "amide.example", loc


@pytest.mark.parametrize("raw", HOSTILE)
def test_the_label_sheet_back_link_is_a_page_of_the_inventory(client, raw):
    r = client.get("/inventory/orders/999999/labels", params={"next": raw, "auto": "1"})
    assert "evil.test" not in r.text or r.status_code == 404
    assert safe_redirect(raw) == "/" or raw.startswith("/") and "evil" not in safe_redirect(raw).split("/")[1:2]


# ---------------------------------------------------------------- outbound requests (netguard)

BLOCKED = ["http://2130706433/", "http://0x7f.1/", "http://017700000001/", "http://127.1/", "http://0/", "http://0.0.0.0/", "http://[::1]/",
           "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/", "http://[64:ff9b::7f00:1]/", "http://[fe80::1]/", "http://[::]/",
           "http://169.254.169.254/latest/meta-data", "http://[::ffff:169.254.169.254]/", "http://localhost/", "http://localhost./",
           "http://127.0.0.1./", "http://127.0.0.1:80@evil.test/", "http://evil.test@127.0.0.1/", "http://127.0.0.1\\@evil.test/",
           "http://[fc00::1]/", "http://10.0.0.1/", "http://192.168.1.50:3030/", "http://100.100.100.200/", "http://[fd00:ec2::254]/",
           "http://[::ffff:10.0.0.1]/", "http://224.0.0.1/", "http://255.255.255.255/", "http://[64:ff9b::a00:1]/", "ftp://example.test/",
           "file:///etc/passwd", "http://example.test/ x", "http://example.test/\r\nHost: evil", "gopher://127.0.0.1/", "http:///nohost"]


@pytest.mark.parametrize("url", BLOCKED)
def test_netguard_refuses_every_internal_spelling(url):
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url)


def test_a_name_that_answers_public_then_private_is_refused(monkeypatch):
    def fake(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("http://rebind.example.test/")


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    answers = iter([("93.184.216.34",), ("127.0.0.1",)])          # a rebinding resolver: public first, loopback after

    def fake(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers)[0], port))]
    seen = []

    def fake_connect(address, *a, **kw):
        seen.append(address)
        raise OSError("no network in tests")
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    with pytest.raises(OSError):
        netguard.safe_open(urllib.request.Request("http://rebind.example.test/x"), 2)
    assert seen == [("93.184.216.34", 80)]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"x" * 5000
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def loopback_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_a_redirect_is_never_followed_and_the_answer_size_is_capped(loopback_server):
    with pytest.raises(urllib.error.HTTPError) as err:
        netguard.safe_open(urllib.request.Request(loopback_server + "/redirect"), 3, allow_loopback=True)
    assert err.value.code == 302
    with pytest.raises(netguard.UnsafeURL):
        with netguard.safe_open(urllib.request.Request(loopback_server + "/big"), 3, allow_loopback=True, max_bytes=1000) as r:
            r.read()
    with pytest.raises(netguard.UnsafeURL):                       # loopback needs an explicit opt-in
        netguard.safe_open(urllib.request.Request(loopback_server + "/big"), 3)


def test_the_own_server_address_is_checked_before_it_is_used():
    from app import ai_summary
    for bad in ("http://user:pw@192.168.1.50/v1", "http://192.168.1.50/v1?x=", "http://192.168.1.50/#x", "ftp://192.168.1.50", "http://host\r\n/v1",
                "http://192.168.1.50\\@evil.test", "", "http:///v1"):
        assert ai_summary.base_url_error(bad), bad


# ---------------------------------------------------------------- shared vendor data: an ordinary account is refused

def test_an_ordinary_account_cannot_change_a_shared_vendor(client, me, db, vendor, b):
    mine = {"name": "Hijacked", "website": "https://evil.example", "notes": "x", "wallets-0-coin": "BTC", "wallets-0-address": "bc1qevil",
            "price_list_url": "https://evil.example/p.pdf"}
    assert b.post(f"/vendors/{vendor.id}", data=mine).status_code == 404
    assert b.post(f"/vendors/{vendor.id}/delete", data={}).status_code == 404
    db.expire_all()
    v = db.get(Vendor, vendor.id)
    assert v is not None and v.name == "Redteam Vendor" and v.website == "https://redteam.example" and not v.wallets and not v.price_list_url


def test_the_new_order_form_does_not_let_an_ordinary_account_replace_a_shared_price_list(client, me, db, vendor, b):
    form = {"order_date": "2026-01-01", "is_new_vendor": "no", "vendor_id": str(vendor.id), "price_list_current": "no",
            "price_list_replace_url": "https://evil.example/list.pdf", "lines-0-name": "Zorvex 10mg", "lines-0-quantity": "1",
            "lines-0-cost": "10"}
    b.post("/inventory/orders", data=form)
    db.expire_all()
    assert db.get(Vendor, vendor.id).price_list_url is None


def test_forged_wallet_ids_cannot_reach_another_vendors_wallet(client, me, db, vendor):
    from app.models import VendorWallet
    other = Vendor(name="Redteam Vendor Other", created_by_id=me)
    db.add(other)
    db.flush()
    w = VendorWallet(vendor_id=other.id, coin="BTC", address="bc1qother")
    db.add(w)
    db.commit()
    client.post(f"/vendors/{vendor.id}", data={"name": "Redteam Vendor", "wallets-0-id": str(w.id), "wallets-0-coin": "ETH", "wallets-0-address": "0xredteam"})
    db.expire_all()
    assert (db.get(VendorWallet, w.id).coin, db.get(VendorWallet, w.id).address) == ("BTC", "bc1qother")


# ---------------------------------------------------------------- tokens and the feed

def test_health_and_watcher_tokens_are_not_interchangeable_and_junk_is_refused(client):
    c = TestClient(app)
    for header in ("", "Bearer ", "Bearer amide_ing_" + "a" * 43, "Bearer amide_hlt_" + "a" * 43, "bearer x", "Bearer " + "a" * 5000, "Basic abc"):
        h = {"authorization": header} if header else {}
        assert c.post("/api/ingest/health", content=b"{}", headers=h).status_code == 401, header
        assert c.get("/api/ingest/ping", headers=h).status_code in (401, 404, 405), header
    assert c.get("/calendar/feed/not-a-token.ics").status_code == 404
    assert c.get("/calendar/feed/%00.ics").status_code in (404, 422)


def test_the_calendar_feed_is_rate_limited_per_address():
    from app.routers import calendar
    calendar.feed_limiter._hits.clear()
    c = TestClient(app)
    codes = [c.get(f"/calendar/feed/guess{i}.ics").status_code for i in range(70)]
    assert codes[0] == 404 and codes[-1] == 429
    calendar.feed_limiter._hits.clear()


# ---------------------------------------------------------------- backup rows from a hostile file

def test_a_hostile_profile_row_cannot_make_anyone_an_administrator(client, db, me):
    from sqlalchemy import text
    from app.backup.archive import read_archive, write_archive
    import json
    from app.backup.export import build_archive
    b_id = db.execute(text("SELECT id FROM users WHERE username_key = 'crossuserb'")).scalar()
    if b_id is None:
        _signed_in_b()
        b_id = db.execute(text("SELECT id FROM users WHERE username_key = 'crossuserb'")).scalar()
    archive = read_archive(build_archive(db, kind="export", uid=me, creator="Tester", keys=["profile"]), max_bytes=50_000_000)
    entries = {n: archive.read(n) for n in archive.names()}
    name = next(n for n in entries if n.endswith("profile.json"))
    payload = json.loads(entries[name])
    for rows in payload["tables"].values():
        for row in rows:
            row.update({"is_admin": True, "password_hash": "x", "id": me, "totp_secret": "AAAA", "username": "pwned"})
    entries[name] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    hostile = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    before = db.execute(text("SELECT is_admin, password_hash, username FROM users WHERE id = :u"), {"u": b_id}).one()
    loader.load(db, hostile, uid=b_id, username_key="crossuserb", is_admin=False, plan={"profile": loader.REPLACE})
    db.commit()
    after = db.execute(text("SELECT is_admin, password_hash, username, totp_secret FROM users WHERE id = :u"), {"u": b_id}).one()
    assert (after.is_admin, after.password_hash, after.username) == tuple(before) and after.totp_secret is None
    assert db.execute(text("SELECT is_admin FROM users WHERE id = :u"), {"u": me}).scalar() == 1
