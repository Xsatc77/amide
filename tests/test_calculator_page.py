import html
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, Medium, Peptide, Protocol

BPC_ID_CACHE = {}


def peptide_id(name):
    if name not in BPC_ID_CACHE:
        with SessionLocal() as s:
            BPC_ID_CACHE[name] = s.scalar(select(Peptide.id).where(Peptide.name == name))
    return BPC_ID_CACHE[name]


def text(r) -> str:
    assert r.status_code == 200, r.text
    return html.unescape(r.text)


def test_nav_link_is_live(client):
    t = text(client.get("/calculator"))
    assert '<a href="/calculator"' in t and 'class="soon"' not in t.split("Calculator")[0][-40:]


def test_default_page_has_a_computed_result(client):
    t = text(client.get("/calculator"))
    # No query params: server still renders a sensible default result (works without JS).
    assert "mg/mL" in t and "units" in t.lower()


def test_compute_api_reproduces_cheat_sheet_example(client):
    r = client.get("/api/calculator/compute", params={"vial_mg": 10, "water_ml": 2, "dose_value": 250,
                                                       "dose_unit": "mcg", "syringe_ml": 1.0})
    assert r.status_code == 200
    body = r.json()
    assert body["concentration_mg_ml"] == pytest.approx(5.0)
    assert body["draw_ml"] == pytest.approx(0.05)
    assert body["units"] == pytest.approx(5.0)
    assert body["doses_per_vial"] == 40
    assert body["problems"] == []


def test_compute_api_reports_problems_without_erroring(client):
    r = client.get("/api/calculator/compute", params={"vial_mg": 0, "water_ml": "", "dose_value": -1,
                                                       "dose_unit": "mg", "syringe_ml": 1.0})
    assert r.status_code == 200
    body = r.json()
    assert set(body["problems"]) == {"vial", "water", "dose"}
    assert body["draw_ml"] is None


def test_compute_api_flags_over_capacity(client):
    r = client.get("/api/calculator/compute", params={"vial_mg": 100, "water_ml": 1, "dose_value": 50,
                                                       "dose_unit": "mg", "syringe_ml": 0.3})
    assert r.json()["over_capacity"] is True


def test_reverse_solver_endpoint(client):
    r = client.get("/api/calculator/target-water", params={"vial_mg": 10, "dose_mg": 0.25, "target_units": 20})
    assert r.status_code == 200
    water = r.json()["water_ml"]
    assert water == pytest.approx(8.0)  # 10 * (20/100) / 0.25


def test_reverse_solver_endpoint_invalid(client):
    r = client.get("/api/calculator/target-water", params={"vial_mg": 0, "dose_mg": 1, "target_units": 20})
    assert r.status_code == 200 and r.json()["water_ml"] is None


def test_inventory_prefill_only_lyophilized_and_own(client, db):
    client.post("/inventory", data={"name": "My Powder", "count": "1", "vial_size_mg": "10", "medium": "Lyophilized"})
    client.post("/inventory", data={"name": "My Liquid", "count": "1", "vial_size_mg": "5", "medium": "Liquid"})
    t = text(client.get("/calculator"))
    data = json.loads(t.split('id="calc-data">')[1].split("</script>")[0])
    names = [i["name"] for i in data["inventory"]]
    assert "My Powder" in names and "My Liquid" not in names


def test_protocol_dose_prefill_present(client, db):
    data = {"name": "CalcProto", "start_date": "2026-09-01", "goal": ["muscle-recovery"],
            "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250",
            "items-0-dose_unit": "mcg", "items-0-frequency": "daily", "items-0-time_of_day": "am"}
    client.post("/protocols", data=data)
    t = text(client.get("/calculator"))
    cdata = json.loads(t.split('id="calc-data">')[1].split("</script>")[0])
    assert any(p["peptide"] == "BPC-157" and p["dose"] == 250 and p["unit"] == "mcg" for p in cdata["protocol_doses"])


def test_calculator_privacy(client, db):
    inv = InventoryItem(name="Private Vial", count=1, vial_size_mg=15, medium=Medium.LYOPHILIZED)
    with SessionLocal() as s:
        s.add(inv)
        s.commit()
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "CalcOther", "password": "Calc0ther!", "confirm": "Calc0ther!"})
    t = other.get("/calculator").text
    assert "Private Vial" not in t


def test_syringe_capacities_serialize_as_pairs_not_a_lossy_dict(client):
    """A dict with float keys (0.3/0.5/1.0) becomes a JSON object with string keys ("1.0"), and JS's
    Number(1.0) stringifies to "1" for object-property lookup -- a silent mismatch on the 1.0 mL entry.
    Serializing as [mL, units] pairs lets the page build a real JS Map instead, which has no such trap."""
    t = client.get("/calculator").text
    data = json.loads(t.split('id="calc-data">')[1].split("</script>")[0])
    assert data["syringe_capacities"] == [[0.3, 30], [0.5, 50], [1.0, 100]]
