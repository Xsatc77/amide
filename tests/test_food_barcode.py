"""Barcode lookup: a scanned or typed UPC/EAN is matched exactly against the USDA branded-food records (uses the same optional USDA key)."""

import pytest

from app import config
from app.food import usda


def item(fdc_id, upc, name="Test cereal", brand="ACME FOODS"):
    return {"fdcId": fdc_id, "description": name.upper(), "brandOwner": brand, "gtinUpc": upc, "servingSize": 30, "servingSizeUnit": "g",
            "householdServingFullText": "1 cup", "foodNutrients": [
                {"nutrientName": "Energy", "unitName": "KCAL", "value": 380.0}, {"nutrientName": "Protein", "unitName": "G", "value": 8.0},
                {"nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 80.0},
                {"nutrientName": "Total lipid (fat)", "unitName": "G", "value": 2.0}, {"nutrientName": "Fiber, total dietary", "unitName": "G", "value": 6.0}]}


PAYLOAD = {"foods": [item(1, "00012345678905"), item(2, "00098765432109", name="Other cereal")]}


def test_parsed_results_carry_their_barcode_digits():
    assert [f["gtin"] for f in usda.parse_results(PAYLOAD)] == ["00012345678905", "00098765432109"]


@pytest.mark.parametrize("code", ["012345678905", "12345678905", "0012345678905", "00012345678905"])
def test_a_code_matches_whatever_leading_zeros_it_is_written_with(code):
    seen = {}

    def fake(url):
        seen["url"] = url
        return PAYLOAD
    found = usda.by_barcode(code, "KEY", fetch=fake)
    assert [f["fdc_id"] for f in found] == [1]
    assert "dataType=Branded" in seen["url"] and "api_key=KEY" in seen["url"] and f"query={code}" in seen["url"]


def test_no_exact_match_is_an_empty_list_not_a_near_miss():
    assert usda.by_barcode("012345678900", "KEY", fetch=lambda url: PAYLOAD) == []


@pytest.mark.parametrize("bad", ["", "abc", "1234", "123456789012345678", "0123 4567"])
def test_a_code_that_is_not_a_barcode_is_refused(bad):
    with pytest.raises(ValueError):
        usda.by_barcode(bad, "KEY", fetch=lambda url: PAYLOAD)


def test_the_route_needs_the_key_validates_the_code_and_returns_the_match(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "")
    assert client.get("/food/barcode", params={"code": "012345678905"}).status_code == 404
    monkeypatch.setattr(config, "USDA_API_KEY", "KEY")
    monkeypatch.setattr(usda, "fetch_json", lambda url: PAYLOAD)
    ok = client.get("/food/barcode", params={"code": "012345678905"})
    assert ok.status_code == 200 and [f["fdc_id"] for f in ok.json()] == [1]
    assert client.get("/food/barcode", params={"code": "012345678900"}).json() == []
    assert client.get("/food/barcode", params={"code": "abc"}).status_code == 422

    def down(url):
        raise OSError("no network")
    monkeypatch.setattr(usda, "fetch_json", down)
    assert client.get("/food/barcode", params={"code": "012345678905"}).status_code == 502


def test_the_add_food_dialog_offers_scan_and_type_a_code_only_with_a_key(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "")
    assert "data-barcode" not in client.get("/measurements", params={"tab": "food"}).text
    monkeypatch.setattr(config, "USDA_API_KEY", "KEY")
    page = client.get("/measurements", params={"tab": "food"}).text
    assert "data-barcode-scan" in page and "data-barcode-code" in page and "barcode.js" in page
