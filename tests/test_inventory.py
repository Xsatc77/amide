import html

from fastapi.testclient import TestClient

from app.main import app

from sqlalchemy import select

from app import config
from datetime import date

from app.models import InventoryItem, Medium, Vendor

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


def _items(db):
    db.expire_all()
    return db.scalars(select(InventoryItem)).all()


def test_empty_page_has_add_button(client):
    r = client.get("/inventory")
    assert r.status_code == 200
    assert 'data-action="add"' in html.unescape(r.text)
    assert "No inventory yet" in html.unescape(r.text)


def test_create_full_item_with_coa(client, db):
    r = client.post(
        "/inventory",
        data={"name": "BPC-157", "count": "5", "vial_size_mg": "10", "medium": "Lyophilized",
              "cost": "$1,045.50", "vendor": "Acme Labs"},
        files={"coa": ("coa.png", PNG, "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    [item] = _items(db)
    assert item.name == "BPC-157"
    assert item.count == 5
    assert item.vial_size_mg == 10
    assert item.medium is Medium.LYOPHILIZED
    assert item.cost_cents == 104550
    assert item.vendor == "Acme Labs"
    assert item.coa_filename.endswith(".png")
    assert (config.COA_DIR / item.coa_filename).read_bytes() == PNG

    page = client.get("/inventory").text
    assert "BPC-157" in page and "$1,045.50" in page and "10 mg" in page

    coa = client.get(f"/inventory/{item.id}/coa")
    assert coa.status_code == 200
    assert coa.headers["content-type"] == "image/png"


def test_only_name_is_required(client, db):
    r = client.post("/inventory", data={"name": "Semaglutide pen"}, follow_redirects=False)
    assert r.status_code == 303
    [item] = _items(db)
    assert item.count == 1
    assert item.vial_size_mg is None and item.medium is None and item.cost_cents is None


def test_validation_errors_rerender_form(client, db):
    r = client.post("/inventory", data={"name": " ", "count": "-2", "vial_size_mg": "abc",
                                        "medium": "Smoke", "cost": "lots"})
    assert r.status_code == 422
    for msg in ("Item name is required", "Count can't be negative", "Amount must be a number",
                "Pick a medium", "Cost must be a number"):
        assert msg in html.unescape(r.text)
    assert "data-open-on-load" in html.unescape(r.text)
    assert _items(db) == []


def test_rejects_bad_coa(client, db):
    r = client.post("/inventory", data={"name": "X"}, files={"coa": ("evil.html", b"<script>", "text/html")})
    assert r.status_code == 422 and "COA must be a photo" in html.unescape(r.text)

    # Right extension, wrong contents.
    r = client.post("/inventory", data={"name": "X"}, files={"coa": ("fake.png", b"<script>", "image/png")})
    assert r.status_code == 422 and "don't match" in html.unescape(r.text)
    assert _items(db) == []
    assert list(config.COA_DIR.iterdir()) == []


def test_edit_replaces_and_removes_coa(client, db):
    client.post("/inventory", data={"name": "TB-500"}, files={"coa": ("a.png", PNG, "image/png")})
    [item] = _items(db)
    old = item.coa_filename

    client.post(f"/inventory/{item.id}", data={"name": "TB-500", "count": "3"},
                files={"coa": ("b.pdf", PDF, "application/pdf")})
    [item] = _items(db)
    assert item.count == 3
    assert item.coa_filename.endswith(".pdf")
    assert not (config.COA_DIR / old).exists()

    client.post(f"/inventory/{item.id}", data={"name": "TB-500", "remove_coa": "1"})
    [item] = _items(db)
    assert item.coa_filename is None
    assert list(config.COA_DIR.iterdir()) == []


def test_delete_removes_row_and_file(client, db):
    client.post("/inventory", data={"name": "GHK-Cu"}, files={"coa": ("a.png", PNG, "image/png")})
    [item] = _items(db)
    r = client.post(f"/inventory/{item.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert _items(db) == []
    assert list(config.COA_DIR.iterdir()) == []


def test_order_and_coa_details(client, db):
    r = client.post(
        "/inventory",
        data={"name": "BPC-157", "vial_size_mg": "10", "lot_number": "BX-2291",
              "order_date": "2026-09-01", "shipped_date": "2026-09-03", "arrival_date": "2026-09-08",
              "coa_vial_size_mg": "8.5", "coa_purity_pct": "99.2%"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    [item] = _items(db)
    assert item.lot_number == "BX-2291"
    assert str(item.order_date) == "2026-09-01"
    assert str(item.shipped_date) == "2026-09-03"
    assert str(item.arrival_date) == "2026-09-08"
    assert item.coa_vial_size_mg == 8.5
    assert item.coa_purity_pct == 99.2

    page = html.unescape(client.get("/inventory").text)
    assert "Lot BX-2291" in page
    assert "Sep 8, 2026" in page
    assert "99.2% · 8.5 mg" in page
    assert 'class="small warn"' in page  # 8.5 mg lab vs 10 mg labeled is >10% short

    # Edit pre-fill carries the new fields.
    assert '"order_date": "2026-09-01"' in page and '"coa_purity_pct": "99.2"' in page

    row = client.get(f"/api/inventory/{item.id}").json()
    assert row["lot_number"] == "BX-2291"
    assert row["arrival_date"] == "2026-09-08"
    assert row["coa_purity_pct"] == 99.2


def test_order_and_coa_validation(client, db):
    r = client.post("/inventory", data={"name": "X", "order_date": "2026-09-05", "shipped_date": "2026-09-01",
                                        "arrival_date": "2026-08-30", "coa_vial_size_mg": "0",
                                        "coa_purity_pct": "101"})
    assert r.status_code == 422
    text = html.unescape(r.text)
    for msg in ("Shipped date can't be before the order date",
                "Arrival date can't be before the shipped date",
                "Lab vial size must be greater than 0",
                "Purity must be between 0 and 100%"):
        assert msg in text

    r = client.post("/inventory", data={"name": "X", "order_date": "not-a-date", "coa_purity_pct": "high"})
    assert r.status_code == 422
    text = html.unescape(r.text)
    assert "Enter a valid date" in text and "Purity must be a number" in text
    assert _items(db) == []


def test_json_api(client):
    client.post("/inventory", data={"name": "Retatrutide", "vial_size_mg": "2.5", "medium": "Lyophilized", "cost": "80"})
    [row] = client.get("/api/inventory").json()
    assert row["name"] == "Retatrutide"
    assert row["vial_size_mg"] == 2.5
    assert row["medium"] == "Lyophilized"
    assert row["cost"] == 80.0
    assert row["has_coa"] is False
    assert client.get(f"/api/inventory/{row['id']}").json()["id"] == row["id"]
    assert client.get("/api/inventory/9999").status_code == 404


# ---------------------------------------------------------------- Phase 1: units, required fields, vendors

def test_medium_required_fields_enforced_server_side(client, db):
    cases = [
        ({"name": "X", "medium": "Lyophilized"}, "Amount is required for Lyophilized."),
        ({"name": "X", "medium": "Liquid", "vial_size_mg": "10"}, "Volume (mL) is required for Liquid."),
        ({"name": "X", "medium": "Autoinjector"}, "Doses per pen is required for Autoinjector."),
        ({"name": "X", "medium": "Pill"}, "Amount per pill is required for Pill."),
        ({"name": "X", "medium": "Inhaler"}, "Amount is required for Inhaler."),
    ]
    for data, message in cases:
        r = client.post("/inventory", data=data)
        assert r.status_code == 422, data
        assert message in html.unescape(r.text), (data, message)
    assert _items(db) == []


def test_each_medium_can_be_completed(client, db):
    completions = [
        {"name": "Powder", "medium": "Lyophilized", "vial_size_mg": "10"},
        {"name": "Solution", "medium": "Liquid", "vial_size_mg": "10", "volume_ml": "2"},
        {"name": "Pen", "medium": "Autoinjector", "units_per_package": "4"},
        {"name": "Tablets", "medium": "Pill", "vial_size_mg": "5", "units_per_package": "30"},
        {"name": "Spray", "medium": "Inhaler", "vial_size_mg": "50"},
    ]
    for data in completions:
        r = client.post("/inventory", data=data, follow_redirects=False)
        assert r.status_code == 303, data
    names = {i.name for i in _items(db)}
    assert names == {"Powder", "Solution", "Pen", "Tablets", "Spray"}


def test_vial_size_unit_saves(client, db):
    client.post("/inventory", data={"name": "X", "vial_size_mg": "250", "vial_size_unit": "mcg"})
    [item] = _items(db)
    assert item.vial_size_mg == 250 and item.vial_size_unit.value == "mcg"
    row = client.get(f"/api/inventory/{item.id}").json()
    assert row["vial_size_unit"] == "mcg"


def test_vendor_creates_and_reuses_case_insensitively(client, db):
    client.post("/inventory", data={"name": "A", "vendor": "Acme Peptides"})
    client.post("/inventory", data={"name": "B", "vendor": "acme peptides"})
    items = {i.name: i for i in _items(db)}
    assert items["A"].vendor_id == items["B"].vendor_id
    assert items["A"].vendor == "Acme Peptides" and items["B"].vendor == "Acme Peptides"
    assert db.query(Vendor).count() == 1


def test_vendor_cleared_on_edit(client, db):
    client.post("/inventory", data={"name": "A", "vendor": "Acme"})
    [item] = _items(db)
    client.post(f"/inventory/{item.id}", data={"name": "A", "vendor": ""})
    [item] = _items(db)
    assert item.vendor is None and item.vendor_id is None


def test_expiration_and_storage_round_trip(client, db):
    client.post("/inventory", data={"name": "X", "expiration_date": "2027-06-01", "storage": "fridge"})
    [item] = _items(db)
    assert item.expiration_date == date(2027, 6, 1) and item.storage.value == "fridge"
    page = client.get(f"/inventory")
    assert item.id in [i.id for i in _items(db)]


def test_expiration_date_must_be_valid(client, db):
    r = client.post("/inventory", data={"name": "X", "expiration_date": "not-a-date"})
    assert r.status_code == 422
    assert _items(db) == []


# ---------------------------------------------------------------- Phase 1: search / sort / filter markup

def test_list_has_search_and_filter_and_sort_markup(client, db):
    client.post("/inventory", data={"name": "BPC-157", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "vendor": "Acme"})
    client.post("/inventory", data={"name": "Retatrutide", "medium": "Liquid", "vial_size_mg": "5",
                                    "volume_ml": "2"})
    t = html.unescape(client.get("/inventory").text)
    assert 'id="inv-search"' in t
    for m in Medium:
        assert f'data-filter="{m.value}"' in t
    assert 'data-filter="all"' in t
    assert 'data-sort="name"' in t and 'data-sort="count"' in t
    # rows carry the data the JS needs to search/filter/sort without another request
    assert 'data-search="bpc-157' in t.lower()
    assert 'data-medium="Lyophilized"' in t and 'data-medium="Liquid"' in t


def test_search_filter_scoped_to_owner_only(client, db):
    client.post("/inventory", data={"name": "Mine Only", "medium": "Lyophilized", "vial_size_mg": "10"})
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "InvOther", "password": "Inv0ther!", "confirm": "Inv0ther!"})
    t = other.get("/inventory").text
    assert "Mine Only" not in t
