"""Cross-user (BOLA / IDOR) matrix.

User A (the default test account, an administrator) owns a row of every kind of per-user resource. User B is a second, ordinary
account. For EVERY route the app declares that takes an id in its path (found by walking `app.routes`, so a new route is covered
without anyone remembering to add it) B calls it with A's ids, GET and POST as declared, with an empty form and with a generic
filled-in form. After each call:

  * the answer is never a 2xx page, unless the route is on the reviewed SHARED_OK list below (each with a reason);
  * not one row of any table and not one stored file of A changed (a snapshot taken before and after the call).

A route whose id parameter is not described by `kind_for` fails the test ("unmapped"), which is the prompt to teach this file
about the new route. Not established by this file: a route that checks a required form field before it checks ownership could
answer 422 to an empty form and hide a hole; the filled-in second form narrows that, it does not prove it away.
"""

import inspect
import json
import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import config
from app.auth import passwords
from app.db import SessionLocal
from app.main import app
from app.models import naive_utcnow as _accepted_now
from app.models import (
    BodyMeasurement, BodyPhoto, FitnessTestResult, Food, FoodLog, IngestItem, IngestSource, IngestToken, JournalCustomEffect,
    JournalEntry, JournalPhoto, JournalQuickNote, LabPanel, Order, OrderItem, Passkey, PeptideNote, ProtocolItem, SavedLink, User,
    UserMedicine, Vendor, WaterLog, WorkoutExercise, WorkoutLog, WorkoutPlan,
)
from tests.backup_helpers import clean_files, seed_world, wipe_person, write
from tests.route_helpers import RouteInfo, all_routes

TAG = "Qa7"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40 + b"\xff\xd9"
FILE_DIRS = ("COA_DIR", "PRICE_LIST_DIR", "LAB_REPORT_DIR", "WORKOUT_PDF_DIR", "WALLET_QR_DIR", "BODY_PHOTO_DIR", "JOURNAL_PHOTO_DIR", "INGEST_DIR")
SKIP_TABLES = {"sessions", "security_events", "sqlite_sequence", "alembic_version"}

# Routes where a 2xx for another person's id is the correct answer, because the thing is shared by everyone on purpose.
# (method, path template) -> reason
SHARED_OK = {
    ("GET", "/library/{peptide_id}"): "the peptide library is shared by every account",
    ("GET", "/library/{peptide_id}/shot-plan"): "library reference calculation, no personal data",
    ("GET", "/library/{peptide_id}/edit"): "the shared library entry form; every account may suggest edits (see the report: any account can also save them)",
    ("GET", "/library/{peptide_id}/card"): "library card images are shared",
    ("POST", "/library/{peptide_id}"): "shared library entry: any signed-in account may edit it (reported as a design decision, not a per-user hole)",
    ("GET", "/vendors/{vendor_id}"): "vendors are shared by every account; only edits are limited to the creator or an admin",
    ("GET", "/vendors/{vendor_id}/price-list"): "shared vendor price list file",
    ("GET", "/vendors/{vendor_id}/wallets/{wallet_id}/qr"): "shared vendor payment QR",
    ("POST", "/vendors/{vendor_id}/favorite"): "B's own favourite of a shared vendor",
    ("POST", "/vendors/{vendor_id}/unfavorite"): "B's own favourite of a shared vendor",
    ("POST", "/vendors/{vendor_id}/clear-recommendation"): "creator or admin only (404 for others); never reaches 2xx for B",
    ("POST", "/settings/sharing/{grantee_id}/{category}"): "B grants A access to B's OWN data; no row of A's changes",
    ("GET", "/protocols/{protocol_id}/shop"): "never 2xx for another person's protocol (listed only if it turns out shared)",
}
# Of the above, these must still not touch anything of A's (checked for every route anyway). Entries of SHARED_OK that are never
# reached with a 2xx are fine: the list says what MAY answer 2xx, not what does.

# Routes that only an administrator may use: B must get 404, A must still be able to.
ADMIN_ONLY = [
    ("GET", "/settings/ingest"), ("POST", "/settings/ingest/tokens"), ("POST", "/settings/ingest/tokens/{token_id}/revoke"),
    ("POST", "/settings/ingest/sources/{source_id}"), ("POST", "/settings/ingest/items/{item_id}/approve"),
    ("POST", "/settings/ingest/items/{item_id}/reject"), ("POST", "/settings/ingest/items/{item_id}/undo"),
    ("GET", "/settings/ingest/items/{item_id}/original"),
    ("GET", "/settings/price-correction"), ("POST", "/settings/price-correction"), ("POST", "/settings/price-correction/add"),
    ("POST", "/settings/admin/registration"), ("POST", "/settings/admin/users/new"),
    ("POST", "/settings/admin/users/{user_id}/reset-password"), ("POST", "/settings/admin/users/{user_id}/remove-2fa"),
    ("POST", "/settings/admin/users/{user_id}/delete"), ("POST", "/backup/restore"), ("POST", "/price-alerts/ignore"),
]
# Routes whose code mentions admin rights but are open to everyone with their own rules (a reason each).
ADMIN_MIXED = {
    "/register": "sign-up: the first account becomes the admin",
    "/dashboard": "shows the admin-only notices when the viewer is an admin",
    "/vendors": "the list shows an admin flag; the delete route is creator-or-admin (app/vendors/access.py)",
    "/vendors/{vendor_id}/delete": "creator or admin may delete a shared vendor (404 for anyone else)",
    "/backup/open": "any account may open its own backup file; whole-installation files need an admin",
    "/backup/load": "any account loads its own sections; admin-only sections are refused inside",
    "/library/{peptide_id}": "the page shows a delete button to admins only",
    "/library/{peptide_id}/delete": "answers 403 to anyone but an admin",
}
ADMIN_MARKERS = ("_require_admin", "_admin(", "is_admin", "_target_user")

RICH_FORM = {
    "name": "Intruder", "count": "9", "quantity": "9", "received_quantity": "9", "price": "1", "date": "2026-03-01", "order_date": "2026-03-01",
    "start_date": "2026-03-01", "measured_at": "2026-03-01", "weight_lbs": "150", "ounces": "8", "amount": "8", "notes": "Intruder", "note": "Intruder",
    "text": "Intruder", "title": "Intruder", "url": "https://example.test", "label": "Intruder", "value": "1", "mood": "3", "caption": "Intruder",
    "tracking_number": "X1", "carrier": "ups", "username": "intruder", "password": "Intr0der!", "confirm": "Intr0der!", "on": "1", "action": "disable",
    "code": "000000", "calories": "1", "servings": "1", "eaten_on": "2026-03-01", "meal": "lunch", "when": "2026-03-01", "logged_on": "2026-03-01",
}
QUERY = {"amount": "10", "unit": "mg", "per_week": "7", "next": "/dashboard", "q": "x"}


# ---------------------------------------------------------------- what a path parameter means

def kind_for(path: str, param: str) -> str | None:
    """The kind of A's row a path parameter stands for (None = not described: the test fails on it)."""
    rules = [
        (r"^/active-vials/", {"vial_id": "vial"}), (r"^/links/", {"link_id": "link"}),
        (r"^/(api/)?inventory/orders/", {"order_id": "order", "line_id": "order_item"}),
        (r"^/(api/)?inventory/", {"item_id": "item", "order_id": "order", "order_item_id": "order_item"}),
        (r"^/vendors/", {"vendor_id": "vendor", "wallet_id": "wallet"}),
        (r"^/(api/)?protocols/", {"protocol_id": "protocol", "item_id": "protocol_item"}),
        (r"^/measurements/photos/", {"photo_id": "body_photo"}), (r"^/measurements/", {"measurement_id": "measurement"}),
        (r"^/food/log/", {"log_id": "food_log"}), (r"^/food/foods/", {"food_id": "food"}),
        (r"^/settings/ingest/tokens/", {"token_id": "watcher_token"}), (r"^/settings/ingest/sources/", {"source_id": "ingest_source"}),
        (r"^/settings/ingest/items/", {"item_id": "ingest_item"}), (r"^/settings/apple-health/token/", {"token_id": "health_token"}),
        (r"^/labs/", {"panel_id": "panel"}), (r"^/water/", {"log_id": "water"}),
        (r"^/workouts/exercises/", {"exercise_id": "exercise"}), (r"^/workouts/day/", {"plan_day_id": "plan_day"}),
        (r"^/workouts/(log|free)/", {"log_id": "workout_log"}), (r"^/workouts/", {"plan_id": "plan"}),
        (r"^/settings/medicines/", {"medicine_id": "medicine"}),
        (r"^/settings/(sharing|shared-with-me|admin/users)/", {"grantee_id": "user_a", "owner_id": "user_a", "user_id": "user_a", "category": "category"}),
        (r"^/fitness-test/", {"result_id": "fit_result"}),
        (r"^/journal/custom-effects/", {"effect_id": "custom_effect"}), (r"^/journal/quick-notes/", {"note_id": "quick_note"}),
        (r"^/journal/photos/", {"photo_id": "journal_photo"}), (r"^/journal/entries/", {"entry_date": "entry_date"}),
        (r"^/library/notes/", {"note_id": "peptide_note"}), (r"^/library/", {"peptide_id": "peptide"}),
        (r"^/account/passkeys/", {"passkey_id": "passkey"}),
    ]
    for pattern, params in rules:
        if re.match(pattern, path) and param in params:
            return params[param]
    return None


def path_params(route: RouteInfo) -> list[str]:
    return re.findall(r"{(\w+)(?::\w+)?}", route.path)


def takes_an_id(route: RouteInfo) -> bool:
    return bool(route.int_params) or "entry_date" in path_params(route)


# ---------------------------------------------------------------- the two people

def _signed_in_b() -> TestClient:
    with SessionLocal() as s:
        if s.scalar(select(User.id).where(User.username_key == "crossuserb")) is None:
            s.add(User(username="CrossUserB", username_key="crossuserb", password_hash=passwords.hash_password("Sec0nd!pw"), is_admin=False))
            s.commit()
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    assert c.post("/login", data={"username": "CrossUserB", "password": "Sec0nd!pw"}).status_code == 303
    return c


def _b_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "crossuserb"))


@pytest.fixture(scope="module")
def user_b(client):
    return _signed_in_b()


@pytest.fixture
def world(client, db, me, user_b):
    """One row of every per-user resource for A. Returns {kind: id}."""
    wipe_person(db, me)
    clean_files()
    db.query(IngestItem).delete()
    db.query(IngestSource).delete()
    db.commit()
    w = seed_world(db, me, TAG)
    ids = {"item": w["item"].id, "order": w["order"].id, "vial": w["vial"].id, "protocol": w["protocol"].id, "plan": w["plan"].id,
           "vendor": w["vendor"].id, "wallet": w["vendor"].wallets[0].id, "peptide": w["peptide"].id, "user_a": me, "category": "inventory",
           "protocol_item": w["protocol"].items[0].id, "plan_day": w["plan"].days[0].id,
           "exercise": w["plan"].days[0].exercises[0].id}
    ids["order_item"] = db.scalar(select(OrderItem.id).where(OrderItem.order_id == ids["order"]))
    ids["measurement"] = db.scalar(select(BodyMeasurement.id).where(BodyMeasurement.owner_id == me))
    ids["water"] = db.scalar(select(WaterLog.id).where(WaterLog.owner_id == me))
    ids["workout_log"] = db.scalar(select(WorkoutLog.id).where(WorkoutLog.owner_id == me))
    ids["fit_result"] = db.scalar(select(FitnessTestResult.id).where(FitnessTestResult.owner_id == me))
    ids["panel"] = db.scalar(select(LabPanel.id).where(LabPanel.owner_id == me))
    entry = db.scalar(select(JournalEntry).where(JournalEntry.owner_id == me))
    ids["entry_date"] = entry.entry_date.isoformat()
    ids["quick_note"] = db.scalar(select(JournalQuickNote.id).where(JournalQuickNote.entry_id == entry.id))
    photo = JournalPhoto(entry_id=entry.id, filename=write(config.JOURNAL_PHOTO_DIR, f"jp-{TAG}.jpg", JPEG), caption=f"cap {TAG}")
    body = BodyPhoto(owner_id=me, taken_on=date(2026, 3, 1), angle="front", note=f"bp {TAG}", filename=write(config.BODY_PHOTO_DIR, f"bp-{TAG}.jpg", JPEG))
    link = SavedLink(owner_id=me, name=f"Link {TAG}", url="https://example.test/a", link_type="other")
    note = PeptideNote(owner_id=me, peptide_id=None, title=f"Note {TAG}", body="private")
    med = UserMedicine(owner_id=me, name=f"Med {TAG}")
    effect = JournalCustomEffect(owner_id=me, name=f"Effect {TAG}")
    food = Food(owner_id=me, source="mine", name=f"Food {TAG}", serving="1 cup", calories=1, protein_g=1, carb_g=1, fat_g=1, fiber_g=1)
    db.add_all([photo, body, link, note, med, effect, food])
    db.flush()
    flog = FoodLog(owner_id=me, eaten_on=date(2026, 3, 1), meal="lunch", food_id=food.id, name=f"Food {TAG}", serving="1 cup", servings=1,
                   calories=1, protein_g=1, carb_g=1, fat_g=1, fiber_g=1)
    src = IngestSource(platform="telegram", chat_id=f"chat-{TAG}", title=f"Source {TAG}", enabled=True)
    wtoken = IngestToken(owner_id=me, label=f"Watcher {TAG}", prefix="amw_qa7", token_hash="a" * 64, scope="watcher")
    htoken = IngestToken(owner_id=me, label=f"Health {TAG}", prefix="amh_qa7", token_hash="b" * 64, scope="health")
    key = Passkey(user_id=me, credential_id=f"cred-{TAG}", public_key=b"k", name=f"Key {TAG}", created_at=datetime(2026, 3, 1))
    db.add_all([flog, src, wtoken, htoken, key])
    db.flush()
    item = IngestItem(source_id=src.id, message_id="1", group_key="g1", received_at=datetime(2026, 3, 1), filename="a.pdf", kind="pdf",
                      file_hash="h" * 8, stored_file=write(config.INGEST_DIR, f"ing-{TAG}.pdf"), status="needs_review")
    db.add(item)
    db.execute(text("UPDATE users SET calendar_token = :t WHERE id = :u"), {"t": "qa7calendartoken" + "0" * 20, "u": me})
    db.commit()
    ids.update(body_photo=body.id, journal_photo=photo.id, link=link.id, peptide_note=note.id, medicine=med.id, custom_effect=effect.id,
               food=food.id, food_log=flog.id, ingest_source=src.id, watcher_token=wtoken.id, health_token=htoken.id, passkey=key.id,
               ingest_item=item.id)
    yield ids
    wipe_person(db, me)
    db.rollback()
    for model in (BodyPhoto, SavedLink, PeptideNote, UserMedicine, JournalCustomEffect, FoodLog, Food):
        db.query(model).filter(model.owner_id == me).delete()
    db.query(IngestItem).delete()
    db.query(IngestSource).delete()
    db.query(Passkey).filter(Passkey.user_id == me).delete()
    db.execute(text("UPDATE users SET calendar_token = NULL WHERE id = :u"), {"u": me})
    db.commit()
    clean_files()


# ---------------------------------------------------------------- snapshots

def snapshot() -> dict:
    """Every row of every table, and every stored file's bytes, as plain data."""
    out = {}
    with SessionLocal() as s:
        for (name,) in s.execute(text("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")):
            if name in SKIP_TABLES:
                continue
            rows = [dict(r) for r in s.execute(text(f'SELECT * FROM "{name}"')).mappings()]
            out[name] = {json.dumps(r, sort_keys=True, default=str) for r in rows}
    files = {}
    for key in FILE_DIRS:
        directory = getattr(config, key)
        if directory.is_dir():
            files.update({f"{key}/{p.name}": p.read_bytes() for p in directory.glob("*") if p.is_file()})
    out["__files__"] = files
    return out


# Tables where the shared library or shared reference data may be edited by any account (reported, not a per-user hole).
SHARED_MUTATIONS = {"peptides": "any signed-in account may edit a shared library entry (see the report)"}
OWNER_COLUMNS = ("owner_id", "user_id")
RECEIVER_COLUMNS = ("grantee_id",)      # B may decline (delete) a share it receives, but may never create one for itself


def changed(before: dict, after: dict, b_id: int) -> list[str]:
    """What the last call did to rows that are not B's: any row that vanished or was rewritten, or any new row that is not B's own."""
    problems = []
    for table, old in before.items():
        new = after.get(table)
        if table == "__files__":
            if old != new:
                problems.append("files")
            continue
        received_ids = set()                   # shares B received: B may accept one (a rewrite of that same row), never create one
        for gone in old - new:                 # a rewritten row shows up as one row gone and one added
            row = json.loads(gone)
            if table == "shares" and row.get("grantee_id") == b_id:
                received_ids.add(row.get("id"))
            if any(row.get(c) == b_id for c in OWNER_COLUMNS + RECEIVER_COLUMNS):
                continue
            if table in SHARED_MUTATIONS:
                continue
            problems.append(f"{table} lost or rewrote {row.get('id')}")
        for added in new - old:
            row = json.loads(added)
            if any(row.get(c) == b_id for c in OWNER_COLUMNS) or table in SHARED_MUTATIONS:
                continue
            if table == "shares" and row.get("id") in received_ids:
                continue
            problems.append(f"{table} gained a row that is not B's")
    return problems


def url_for(route: RouteInfo, ids: dict) -> tuple[str, list[str]]:
    """The URL with A's ids filled in, and the names of any parameters this file does not describe."""
    missing = []

    def one(match):
        kind = kind_for(route.path, match.group(1))
        if kind is None or kind not in ids:
            missing.append(match.group(1))
            return "999999"
        return str(ids[kind])

    url = re.sub(r"{(\w+)(?::\w+)?}", one, route.path)
    return url, missing


def id_routes() -> list[RouteInfo]:
    return [r for r in all_routes(app) if takes_an_id(r) and not r.mounted]


# ---------------------------------------------------------------- the matrix

def test_the_matrix_finds_the_id_routes(client):
    routes = id_routes()
    assert len(routes) > 100
    assert {"/inventory/{item_id}", "/protocols/{protocol_id}/delete", "/measurements/photos/{photo_id}/full"} <= {r.path for r in routes}


def test_every_id_parameter_is_described(client, world):
    problems = []
    for r in id_routes():
        _, missing = url_for(r, world)
        if missing:
            problems.append(f"{r.path}: {missing}")
    assert problems == [], "teach kind_for (and the world fixture) about these new id parameters:\n" + "\n".join(problems)


def run_matrix(user_b, world, *, reads_allowed: bool) -> list[str]:
    problems = []
    b_id = _b_id()
    before = snapshot()
    for r in id_routes():
        url, missing = url_for(r, world)
        if missing:
            continue
        for method in r.methods:
            for form in ({}, RICH_FORM):
                kw = {"params": QUERY} if method == "GET" else {"data": form}
                resp = user_b.request(method, url, **kw)
                after = snapshot()
                diff = changed(before, after, b_id)
                if diff:
                    problems.append(f"{method} {r.path} ({'filled' if form else 'empty'} form) changed {diff}")
                    before = after
                if resp.status_code >= 500:
                    problems.append(f"{method} {r.path} gave B a server error {resp.status_code}")
                if reads_allowed or (method, r.path) in SHARED_OK:
                    continue
                if 200 <= resp.status_code < 300:
                    problems.append(f"{method} {r.path} gave B a {resp.status_code} for A's id")
                if 200 <= resp.status_code < 300 and TAG in resp.text:
                    problems.append(f"{method} {r.path} showed B something of A's")
    return problems


def test_another_person_never_reads_or_changes_a_row_of_a(client, user_b, world):
    assert run_matrix(user_b, world, reads_allowed=False) == []


def test_a_person_A_shares_with_may_read_but_still_never_changes_a_row_of_a(client, user_b, world, db, me):
    """The share model: with inventory and personal data shared, B may be allowed to LOOK (so reads are not judged here), but every
    write must still be refused and leave A's rows alone."""
    from app.models import Share, ShareCategory
    b_id = _b_id()
    db.add_all([Share(owner_id=me, grantee_id=b_id, category=ShareCategory.INVENTORY, accepted_at=_accepted_now()), Share(owner_id=me, grantee_id=b_id, category=ShareCategory.PERSONAL_DATA, accepted_at=_accepted_now())])
    db.commit()
    try:
        assert run_matrix(user_b, world, reads_allowed=True) == []
    finally:
        db.query(Share).delete()
        db.commit()


def test_a_can_still_reach_every_one_of_its_own_rows(client, world):
    """The positive control: the same GET routes answer A (not 404) for A's own ids, so a 404 for B means something."""
    reached = 0
    for r in id_routes():
        if "GET" not in r.methods:
            continue
        url, missing = url_for(r, world)
        if missing or r.path in ("/inventory/orders/{order_id}/labels", ) or "docx" in r.path:
            continue
        resp = client.get(url, params=QUERY)
        if resp.status_code in (200, 303):
            reached += 1
    assert reached > 20, reached


# ---------------------------------------------------------------- administrators only

def test_every_route_that_mentions_admin_rights_is_classified(client):
    unclassified = []
    admin_paths = {p for _, p in ADMIN_ONLY}
    for r in all_routes(app):
        if r.endpoint is None:
            continue
        try:
            src = inspect.getsource(r.endpoint)
        except (OSError, TypeError):
            continue
        if any(m in src for m in ADMIN_MARKERS) and r.path not in admin_paths and r.path not in ADMIN_MIXED:
            unclassified.append(r.path)
    assert unclassified == [], f"decide for each: admin only (ADMIN_ONLY) or open with its own rules (ADMIN_MIXED): {unclassified}"
    known = {(m, r.path) for r in all_routes(app) for m in r.methods}
    assert all(entry in known for entry in ADMIN_ONLY), [e for e in ADMIN_ONLY if e not in known]


def test_an_ordinary_account_gets_404_on_every_admin_only_route(client, user_b, world):
    by_key = {(m, r.path): r for r in all_routes(app) for m in r.methods}
    before = snapshot()
    for method, path in ADMIN_ONLY:
        url, _ = url_for(by_key[(method, path)], world)
        for form in ({}, RICH_FORM):
            resp = user_b.request(method, url, **({"params": QUERY} if method == "GET" else {"data": form}))
            assert resp.status_code == 404, f"{method} {path} gave an ordinary account {resp.status_code}"
    assert changed(before, snapshot(), _b_id()) == []


def test_the_administrator_still_reaches_the_admin_pages(client, world):
    for url in ("/settings/ingest", "/settings/price-correction"):
        assert client.get(url).status_code == 200
    assert client.get(f"/settings/ingest/items/{world['ingest_item']}/original").status_code in (200, 404)    # 404 only for the missing file
    assert client.post("/settings/admin/registration", data={"allow": "0"}, follow_redirects=False).status_code == 303
    client.post("/settings/admin/registration", data={"allow": "1"})
    r = client.post("/backup/restore", data={"token": "none"})
    assert r.status_code != 404        # an admin gets the page's own message, not "not found"


# ---------------------------------------------------------------- the check itself can fail

def test_the_snapshot_comparison_notices_a_change_to_a_row_of_a(client, world, me, db):
    b_id = _b_id()
    before = snapshot()
    assert changed(before, snapshot(), b_id) == []
    db.execute(text("UPDATE inventory_items SET count = count + 1 WHERE id = :i"), {"i": world["item"]})
    db.commit()
    assert any(p.startswith("inventory_items") for p in changed(before, snapshot(), b_id))
    before = snapshot()
    (config.BODY_PHOTO_DIR / f"bp-{TAG}.jpg").write_bytes(b"changed")
    assert "files" in changed(before, snapshot(), b_id)
    before = snapshot()
    db.execute(text("DELETE FROM saved_links WHERE id = :i"), {"i": world["link"]})
    db.commit()
    assert any(p.startswith("saved_links") for p in changed(before, snapshot(), b_id))


# ---------------------------------------------------------------- sharing needs the grantee's consent

def _consent_surfaces(world: dict, me: int) -> list[tuple[str, str, str | None]]:
    """(category, url, text that proves A's data is on the page; None = the page answering 200 is the proof). Every surface that
    shows another person's data because of a share. Pages that exist for B's own data anyway are judged by A's marker text only."""
    item, oi, protocol, panel = world["item"], world["order_item"], world["protocol"], world["panel"]
    return [
        ("inventory", "/inventory", f"Zorvex {TAG} 10mg"),
        ("inventory", f"/inventory/{item}", f"Zorvex {TAG} 10mg"),
        ("inventory", f"/inventory/stock/{item}", None),
        ("inventory", f"/inventory/{item}/orders/{oi}/coa", None),
        ("inventory", f"/vendors/{world['vendor']}", f"Zorvex {TAG} 10mg"),
        ("inventory", f"/dashboard?viewer_id={me}", f'value="{me}" selected'),
        ("personal_data", "/protocols", f"Stack {TAG}"),
        ("personal_data", f"/protocols/{protocol}/print", None),
        ("personal_data", "/measurements?range=lifetime", "Shared With You"),
        ("personal_data", "/measurements?tab=labs", "Tester"),
        ("personal_data", "/measurements?tab=journal", f"journal {TAG}"),
        ("personal_data", f"/labs/panels/{panel}/report", None),
    ]


def _reached(b: TestClient, url: str, marker: str | None) -> bool:
    r = b.get(url)
    return r.status_code == 200 and (marker is None or marker in r.text)


def test_a_pending_share_shows_b_nothing_an_accepted_one_does_and_declining_ends_it(client, user_b, world, db, me):
    """N-09: a share is an offer until the grantee accepts it. While it is pending, B reaches none of A's data on any surface that
    supports sharing; after B accepts, B reaches it; after B declines or stops receiving, it is gone again."""
    from app.models import Share
    b_id = _b_id()
    surfaces = _consent_surfaces(world, me)
    db.query(Share).delete()
    db.commit()
    before = {(c, u): _reached(user_b, u, m) for c, u, m in surfaces}
    assert not [k for k, v in before.items() if v], before   # nothing before any offer
    try:
        for cat in ("inventory", "personal_data"):
            assert client.post(f"/settings/sharing/{b_id}/{cat}", data={"on": "1"}).status_code == 200
        pending = [(c, u) for c, u, m in surfaces if _reached(user_b, u, m)]
        assert pending == [], f"a pending share exposes {pending}"
        assert run_matrix(user_b, world, reads_allowed=False) == []        # and the whole id-route matrix still refuses B
        for cat in ("inventory", "personal_data"):                         # (the matrix answers the offers itself: offer again)
            client.post(f"/settings/sharing/{b_id}/{cat}", data={"on": "1"})

        for cat in ("inventory", "personal_data"):
            assert user_b.post(f"/settings/shared-with-me/{me}/{cat}/accept").status_code == 303
        seen = {(c, u) for c, u, m in surfaces if _reached(user_b, u, m)}
        for c, u, m in surfaces:
            assert (c, u) in seen, f"an accepted {c} share should reach {u}"

        for cat in ("inventory", "personal_data"):
            assert user_b.post(f"/settings/shared-with-me/{me}/{cat}/remove").status_code == 303
        gone = [(c, u) for c, u, m in surfaces if _reached(user_b, u, m)]
        assert gone == [], f"after stopping, still reachable: {gone}"

        # offered again, then declined while pending: also never visible
        client.post(f"/settings/sharing/{b_id}/inventory", data={"on": "1"})
        assert user_b.post(f"/settings/shared-with-me/{me}/inventory/decline").status_code == 303
        assert not any(_reached(user_b, u, m) for c, u, m in surfaces if c == "inventory")
        assert db.scalar(select(Share).where(Share.owner_id == me, Share.grantee_id == b_id)) is None
    finally:
        db.query(Share).delete()
        db.commit()


def test_only_the_grantee_can_accept_or_decline_and_a_second_accept_changes_nothing(client, user_b, world, db, me):
    from app.models import Share, ShareCategory
    b_id = _b_id()
    db.query(Share).delete()
    db.commit()
    client.post(f"/settings/sharing/{b_id}/inventory", data={"on": "1"})
    try:
        for path in ("accept", "decline"):                                     # the owner cannot answer their own offer
            assert client.post(f"/settings/shared-with-me/{b_id}/inventory/{path}", follow_redirects=False).status_code == 404
            assert client.post(f"/settings/shared-with-me/{me}/inventory/{path}", follow_redirects=False).status_code == 404
            assert user_b.post(f"/settings/shared-with-me/{me}/nonsense/{path}").status_code == 404
            assert user_b.post(f"/settings/shared-with-me/9999999/inventory/{path}").status_code == 404
        db.expire_all()
        assert db.scalar(select(Share.accepted_at).where(Share.owner_id == me, Share.grantee_id == b_id)) is None
        assert user_b.post(f"/settings/shared-with-me/{me}/inventory/accept").status_code == 303
        db.expire_all()
        first = db.scalar(select(Share.accepted_at).where(Share.owner_id == me, Share.grantee_id == b_id))
        assert first is not None
        assert user_b.post(f"/settings/shared-with-me/{me}/inventory/accept").status_code == 303
        db.expire_all()
        assert db.scalar(select(Share.accepted_at).where(Share.owner_id == me, Share.grantee_id == b_id)) == first
        assert user_b.post(f"/settings/shared-with-me/{me}/inventory/decline").status_code == 404   # an accepted share is stopped, not declined
    finally:
        db.query(Share).delete()
        db.commit()
