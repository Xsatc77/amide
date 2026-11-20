"""HTTP hardening: security headers, no inline scripts, docs off, one redirect validator, safe tooltips, private-file headers, body caps."""

import re
from pathlib import Path

import pytest

from app import config, uploads
from app.db import SessionLocal
from app.models import Vendor
from app.security_headers import request_cap, safe_redirect

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["/dashboard", "/inventory", "/vendors", "/settings", "/measurements", "/protocols", "/calculator", "/calendar",
         "/library", "/learn", "/workouts", "/food", "/backup", "/account/passkeys", "/links", "/spending", "/today"]


@pytest.fixture(autouse=True)
def clean_vendors(client):
    yield
    with SessionLocal() as s:
        s.query(Vendor).filter(Vendor.name.like("Sechttp%")).delete(synchronize_session=False)
        s.commit()


def _vendor(name="Sechttp Vendor", **kw):
    with SessionLocal() as s:
        v = Vendor(name=name, **kw)
        s.add(v)
        s.commit()
        return v.id


# ---------------------------------------------------------------- S-02

def test_every_kind_of_response_carries_the_security_headers(client):
    for r in (client.get("/dashboard"), client.get("/static/favicon.svg"), client.get("/healthz"),
              client.get("/no-such-page-here"), client.get("/sw.js")):
        csp = r.headers["content-security-policy"]
        for part in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
                     "frame-ancestors 'none'", "connect-src 'self'", "img-src 'self' data: blob:"):
            assert part in csp, (r.url, part)
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"
        assert "camera=(self)" in r.headers["permissions-policy"] and "geolocation=()" in r.headers["permissions-policy"]
        assert r.headers["cross-origin-opener-policy"] == "same-origin"


def test_the_front_door_redirects_get_the_headers_too():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as stranger:
        r = stranger.get("/dashboard", follow_redirects=False)
        assert r.status_code in (303, 307) and "content-security-policy" in r.headers


def test_hsts_only_over_https(client, monkeypatch):
    assert "strict-transport-security" not in client.get("/healthz").headers
    assert client.get("/healthz", headers={"x-forwarded-proto": "https"}).headers["strict-transport-security"] == "max-age=31536000"
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")
    assert "max-age=31536000" in client.get("/healthz").headers["strict-transport-security"]


def test_csp_can_be_switched_to_report_only(client, monkeypatch):
    monkeypatch.setenv("AMIDE_CSP", "report-only")
    r = client.get("/healthz")
    assert "content-security-policy-report-only" in r.headers and "content-security-policy" not in r.headers


def test_templates_have_no_inline_script_or_event_handler():
    script = re.compile(r"<script\b([^>]*)>", re.I)
    handler = re.compile(r"\son(click|change|submit|input|load|error|focus|blur|keydown|keyup|mouse\w+)\s*=", re.I)
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in script.finditer(text):
            attrs = m.group(1)
            assert "src=" in attrs or "application/json" in attrs, f"{path.name}: inline script"
        assert not handler.search(text), f"{path.name}: inline handler"
        assert "javascript:" not in text, path.name


def test_rendered_pages_have_no_inline_script(client):
    seen = 0
    for page in PAGES:
        r = client.get(page)
        if r.status_code != 200:
            continue
        seen += 1
        for m in re.finditer(r"<script\b([^>]*)>", r.text, re.I):
            assert "src=" in m.group(1) or "application/json" in m.group(1), (page, m.group(0))
        assert not re.search(r"\son(click|change|submit)\s*=", r.text, re.I), page
    assert seen >= 5


def test_the_shipped_page_script_exists():
    for name in ("base.js", "confirm-forms.js"):
        assert (ROOT / "app" / "static" / "js" / name).is_file()
    assert "base.js" in (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- N-10

def test_api_docs_are_off(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


# ---------------------------------------------------------------- S-09

EVIL = ["//evil.test", "/\\evil.test", "https://evil.test", "%2f%2fevil.test", "/%2fevil.test", "/%5cevil.test",
        "/vendors\r\nSet-Cookie: x=1", "/vendors\nX: y", "evil.test", "", "javascript:alert(1)", "/vendors/../../x\x00"]


@pytest.mark.parametrize("raw", EVIL)
def test_the_validator_refuses_off_site_and_unsafe_targets(raw):
    assert safe_redirect(raw, "/fallback") == "/fallback"


def test_the_validator_keeps_a_site_path():
    assert safe_redirect("/vendors?x=1#a", "/f") == "/vendors?x=1#a"
    assert safe_redirect("/vendors/3", "/f", "/vendors") == "/vendors/3"
    assert safe_redirect("/vendorsx", "/f", "/vendors") == "/f"
    assert safe_redirect("/dashboard", "/f", "/vendors") == "/f"


@pytest.mark.parametrize("raw", EVIL)
@pytest.mark.parametrize("action", ["favorite", "unfavorite", "clear-recommendation", "delete"])
def test_vendor_actions_never_redirect_off_site(client, raw, action):
    vid = _vendor()
    # favorite, unfavorite and delete read `next` from the query string; clear-recommendation reads it from the form: send both
    r = client.post(f"/vendors/{vid}/{action}", params={"next": raw}, data={"next": raw}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/vendors"


def test_a_vendor_page_target_is_kept(client):
    vid = _vendor("Sechttp Keep")
    r = client.post(f"/vendors/{vid}/favorite", params={"next": "/vendors?tab=x"}, follow_redirects=False)
    assert r.headers["location"] == "/vendors?tab=x"


# ---------------------------------------------------------------- S-10

def test_tooltip_scripts_do_not_put_data_attributes_into_innerhtml():
    js = ROOT / "app" / "static" / "js"
    for path in js.glob("*.js"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", line):
                assert "dataset" not in line and "getAttribute" not in line and "tooltipHtml" not in line, f"{path.name}:{n}"
    assert "tooltipHtml" not in (js / "measurements.js").read_text(encoding="utf-8")
    assert "innerHTML" not in (js / "charts.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- S-11

def test_private_files_are_served_sandboxed_inline_and_not_cached(client):
    config.ensure_dirs()
    path = uploads.price_list_path("sechttp.pdf")
    path.write_bytes(b"%PDF-1.4 test")
    vid = _vendor("Sechttp File", price_list_filename="sechttp.pdf")
    try:
        r = client.get(f"/vendors/{vid}/price-list")
        assert r.status_code == 200, r.text
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'"          # a PDF: no sandbox, Chrome's viewer refuses one
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "private, no-store"
    finally:
        path.unlink(missing_ok=True)


def test_the_import_forms_that_join_whole_files_get_the_large_cap():
    from app.security_headers import request_cap
    assert request_cap("/measurements/import-pacer", "application/x-www-form-urlencoded") > 8 * 1024 * 1024
    assert request_cap("/measurements/import-scale", "application/x-www-form-urlencoded") > 8 * 1024 * 1024
    assert request_cap("/settings/username", "application/x-www-form-urlencoded") == 2 * 1024 * 1024


def test_file_headers_sandbox_everything_except_pdfs():
    from app.security_headers import FILE_HEADERS, PDF_HEADERS, file_headers
    assert file_headers("application/pdf") is PDF_HEADERS and "sandbox" not in PDF_HEADERS["Content-Security-Policy"]
    for kind in ("image/jpeg", "image/png", "application/msword", "application/octet-stream", ""):
        assert file_headers(kind) is FILE_HEADERS and FILE_HEADERS["Content-Security-Policy"].startswith("sandbox")


def test_body_photo_and_journal_photo_headers_are_sandboxed():
    from app.routers import body_photos, journal
    for headers in (body_photos._NO_STORE, journal._NO_STORE):
        assert headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
        assert headers["Cache-Control"] == "private, no-store" and headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------- body size caps

def test_caps_by_path():
    mb = 1024 * 1024
    assert request_cap("/backup/open") == config.MAX_BACKUP_BYTES + mb
    assert request_cap("/api/ingest/health") == config.HEALTH_MAX_BYTES + mb
    assert request_cap("/api/ingest/messages") == 64 * mb
    assert request_cap("/inventory/1/edit", "multipart/form-data; boundary=x") == 64 * mb
    assert request_cap("/vendors/1/favorite", "application/x-www-form-urlencoded") == 2 * mb
    assert request_cap("/anything", "application/json") == 2 * mb


def test_the_default_upload_cap_can_be_changed(monkeypatch):
    monkeypatch.setenv("AMIDE_MAX_REQUEST_MB", "10")
    assert request_cap("/x", "multipart/form-data") == 10 * 1024 * 1024
    monkeypatch.setenv("AMIDE_MAX_REQUEST_MB", "junk")
    assert request_cap("/x", "multipart/form-data") == 64 * 1024 * 1024


def test_an_oversize_declared_body_gets_413(client):
    r = client.post("/vendors/1/favorite", content=b"a=" + b"x" * (3 * 1024 * 1024),
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert "content-security-policy" in r.headers


def test_an_oversize_streamed_body_gets_413(client):
    def chunks():
        for _ in range(40):
            yield b"x" * 100_000          # no Content-Length: counted as it streams, 4 MB in all
    r = client.post("/vendors", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_a_normal_form_and_a_multi_file_upload_still_pass(client):
    assert client.post("/vendors/999999/favorite", data={"next": "/vendors"}).status_code == 404
    big = b"%PDF" + b"x" * (5 * 1024 * 1024)
    r = client.post("/vendors/999999/favorite", files=[("f", ("a.pdf", big)), ("g", ("b.pdf", big))])
    assert r.status_code != 413
