"""Upload boundaries on every upload route, and the cross-site POST rule (S-11, the Origin / Referer check at the gate)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.main import app
from app.models import BodyPhoto, JournalPhoto, Vendor, WorkoutPlan

PNG_HEAD = b"\x89PNG\r\n\x1a\n"
PDF_HEAD = b"%PDF-1.4\n"


def files_in(directory) -> int:
    return len([p for p in directory.glob("*") if p.is_file()])


# Each route: how to post a file to it, where a kept file would land, and whether a file with the right header but a broken body is
# refused (images are decoded, so yes; a PDF kept as-is only has its header checked, so only "no server error" is required).
ROUTES = {
    "inventory COA": dict(url=lambda ids: "/inventory", field="coa", data={"name": "Upload Probe", "count": "1", "category": "Supply"},
                          directory=lambda: config.COA_DIR, pdf=True, header_only=True),
    "body photo": dict(url=lambda ids: "/measurements/photos", field="file", data={"taken_on": "2026-03-01"},
                       directory=lambda: config.BODY_PHOTO_DIR, pdf=False, header_only=False),
    "journal photo": dict(url=lambda ids: "/journal/photos", field="file", data={"entry_date": "2026-03-01"},
                          directory=lambda: config.JOURNAL_PHOTO_DIR, pdf=False, header_only=False),
    "lab report import": dict(url=lambda ids: "/labs/import", field="report", data={"lab": "labcorp"},
                              directory=lambda: config.LAB_REPORT_DIR, pdf=True, header_only=False),
    "workout plan PDF": dict(url=lambda ids: "/workouts/upload", field="pdf", data={},
                             directory=lambda: config.WORKOUT_PDF_DIR, pdf=True, header_only=True),
    "vendor price list": dict(url=lambda ids: f"/vendors/{ids['vendor']}", field="price_list_file", data={"name": "Upload Vendor"},
                              directory=lambda: config.PRICE_LIST_DIR, pdf=True, header_only=True),
}


@pytest.fixture(autouse=True)
def empty_upload_folders(client):
    """The shared clean-up does not empty every folder; each test here starts from empty ones."""
    for spec in ROUTES.values():
        for f in spec["directory"]().glob("*"):
            if f.is_file():
                f.unlink()
    yield


@pytest.fixture
def ids(client, db, me):
    v = Vendor(name="Upload Vendor", created_by_id=me)
    db.add(v)
    db.commit()
    return {"vendor": v.id}


def post(client, route, name: str, body: bytes, ids):
    spec = ROUTES[route]
    return client.post(spec["url"](ids), data=spec["data"], files={spec["field"]: (name, body, "application/octet-stream")})


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_wrong_extension_is_refused_and_nothing_is_kept(client, ids, route):
    spec = ROUTES[route]
    body = (PDF_HEAD if spec["pdf"] else PNG_HEAD) + b"x" * 200
    r = post(client, route, "payload.exe", body, ids)
    assert r.status_code < 500
    assert files_in(spec["directory"]()) == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_file_whose_start_does_not_match_its_name_is_refused(client, ids, route):
    spec = ROUTES[route]
    r = post(client, route, "report.pdf" if spec["pdf"] else "photo.png", b"this is plain text, not the type its name claims", ids)
    assert r.status_code < 500
    assert files_in(spec["directory"]()) == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_right_header_on_a_broken_body_never_crashes_and_images_are_refused(client, ids, route, db):
    spec = ROUTES[route]
    name, head = ("report.pdf", PDF_HEAD) if spec["pdf"] else ("photo.png", PNG_HEAD)
    r = post(client, route, name, head + b"\x00garbage" * 50, ids)
    assert r.status_code < 500
    if not spec["header_only"]:
        assert files_in(spec["directory"]()) == 0
        assert db.scalar(select(BodyPhoto.id).limit(1)) is None and db.scalar(select(JournalPhoto.id).limit(1)) is None


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_file_over_the_size_limit_is_refused_and_nothing_is_kept(client, ids, route, monkeypatch):
    spec = ROUTES[route]
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 2048)
    r = post(client, route, "big.pdf" if spec["pdf"] else "big.png", (PDF_HEAD if spec["pdf"] else PNG_HEAD) + b"x" * 5000, ids)
    assert r.status_code < 500
    assert files_in(spec["directory"]()) == 0


def test_the_workout_upload_makes_no_plan_for_a_refused_file(client, db):
    before = db.query(WorkoutPlan).count()
    r = client.post("/workouts/upload", files={"pdf": ("p.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422 and db.query(WorkoutPlan).count() == before


def test_backup_open_refuses_junk_with_a_message(client):
    r = client.post("/backup/open", files={"file": ("x.amidebackup", b"junk" * 50, "application/octet-stream")}, data={"passphrase": "whatever pass"})
    assert r.status_code == 422


def test_backup_open_refuses_a_file_over_the_limit(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_BACKUP_BYTES", 1024)
    r = client.post("/backup/open", files={"file": ("x.amidebackup", b"x" * 4000, "application/octet-stream")}, data={"passphrase": "whatever pass"})
    assert r.status_code == 422 and "larger" in r.text


# ---------------------------------------------------------------- cross-site posts

@pytest.fixture
def signed_in(client):
    """Its own browser signed in as the test account, so the posts below carry a real session."""
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    assert c.post("/login", data={"username": "Tester", "password": "Test1!"}).status_code in (200, 303)
    return c


PROBE_URL, PROBE_DATA = "/vendors/999999/favorite", {"next": "/vendors"}       # past the gate this answers 404 (no such vendor)


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "https://testserver.evil.example"},                                       # a look-alike host
    {"Referer": "https://evil.example/page"},
    {"Origin": "https://evil.example", "Referer": "http://testserver/dashboard"},        # Origin wins: the browser named the sender
])
def test_a_post_named_as_coming_from_another_site_is_refused(signed_in, headers):
    r = signed_in.post(PROBE_URL, data=PROBE_DATA, headers=headers)
    assert r.status_code == 403 and "Cross-site" in r.text


def test_a_post_from_this_site_passes_the_gate(signed_in):
    for headers in ({"Origin": "http://testserver"}, {"Referer": "http://testserver/vendors"}):
        assert signed_in.post(PROBE_URL, data=PROBE_DATA, headers=headers).status_code == 404


def test_a_post_with_neither_origin_nor_referer_is_allowed_by_design(signed_in):
    """Documented rule (app/auth/gate.py, _same_origin): command-line and mobile clients send neither header while a browser always
    sends one on a form post; the session cookie is SameSite=Lax, so a page on another site cannot ride it anyway."""
    assert signed_in.post(PROBE_URL, data=PROBE_DATA).status_code == 404       # not 403: the gate let it through


def test_the_literal_null_origin_is_treated_like_no_origin(signed_in):
    """Browsers send Origin: null for sandboxed frames. The gate treats it like a missing header, which the SameSite=Lax cookie
    covers; this pins the behaviour so a change is a decision."""
    assert signed_in.post(PROBE_URL, data=PROBE_DATA, headers={"Origin": "null"}).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_unsafe_method_gets_the_same_check(signed_in, method):
    r = getattr(signed_in, method)(PROBE_URL, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_the_sign_in_forms_are_checked_too(client):
    fresh = TestClient(app, follow_redirects=False)
    fresh.post("/notice", data={"understand": "1"})
    r = fresh.post("/login", data={"username": "Tester", "password": "Test1!"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert fresh.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403


def test_a_get_is_never_blocked_by_origin(signed_in):
    assert signed_in.get("/dashboard", headers={"Origin": "https://evil.example"}).status_code == 200
