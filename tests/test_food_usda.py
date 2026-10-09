"""Live food search against the USDA FoodData Central (opt-in: needs a free API key set by whoever runs Amide)."""

import pytest

from app import config
from app.food import usda

SAMPLE = {"foods": [
    {"fdcId": 1001, "description": "BANANA, RAW", "dataType": "SR Legacy", "foodNutrients": [
        {"nutrientName": "Energy", "unitName": "KCAL", "value": 89.0}, {"nutrientName": "Protein", "unitName": "G", "value": 1.09},
        {"nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 22.8}, {"nutrientName": "Total lipid (fat)", "unitName": "G", "value": 0.33},
        {"nutrientName": "Fiber, total dietary", "unitName": "G", "value": 2.6}]},
    {"fdcId": 1002, "description": "PROTEIN BAR", "brandOwner": "Acme Foods", "dataType": "Branded", "servingSize": 60.0, "servingSizeUnit": "g",
     "householdServingFullText": "1 bar", "foodNutrients": [
        {"nutrientName": "Energy", "unitName": "KCAL", "value": 400.0}, {"nutrientName": "Protein", "unitName": "G", "value": 30.0},
        {"nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 40.0}, {"nutrientName": "Total lipid (fat)", "unitName": "G", "value": 10.0}]},
    {"fdcId": 1003, "description": "NO ENERGY ENTRY", "dataType": "Branded", "foodNutrients": [{"nutrientName": "Protein", "unitName": "G", "value": 3.0}]},
]}


def test_a_plain_food_is_per_100_g_and_a_branded_one_is_scaled_to_its_serving():
    banana, bar = usda.parse_results(SAMPLE)[:2]
    assert banana["name"] == "Banana, raw" and banana["serving"] == "100 g" and banana["serving_g"] == 100.0
    assert (banana["calories"], banana["protein_g"], banana["carb_g"], banana["fat_g"], banana["fiber_g"]) == (89.0, 1.1, 22.8, 0.3, 2.6)
    assert bar["name"] == "Protein bar (Acme Foods)" and bar["serving"] == "1 bar (60 g)" and bar["serving_g"] == 60.0
    assert (bar["calories"], bar["protein_g"], bar["carb_g"], bar["fat_g"], bar["fiber_g"]) == (240.0, 18.0, 24.0, 6.0, 0.0)


def test_a_result_without_calories_is_dropped_and_names_are_trimmed():
    assert [f["fdc_id"] for f in usda.parse_results(SAMPLE)] == [1001, 1002]
    long = {"foods": [{"fdcId": 5, "description": "X" * 300, "foodNutrients": [{"nutrientName": "Energy", "unitName": "KCAL", "value": 10}]}]}
    assert len(usda.parse_results(long)[0]["name"]) <= 120


def test_the_search_url_carries_the_key_and_query_and_junk_comes_back_empty():
    seen = {}

    def fake(url):
        seen["url"] = url
        return SAMPLE
    assert len(usda.search("banana & oats", "KEY123", fetch=fake)) == 2
    assert "api_key=KEY123" in seen["url"] and "query=banana+%26+oats" in seen["url"] and seen["url"].startswith("https://api.nal.usda.gov/fdc/v1/foods/search")
    assert usda.search("x", "KEY", fetch=lambda url: {"unexpected": True}) == []
    assert usda.search("   ", "KEY", fetch=lambda url: SAMPLE) == []


def test_without_a_key_the_route_says_the_feature_is_off_and_makes_no_call(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "")
    called = []
    monkeypatch.setattr(usda, "fetch_json", lambda url: called.append(url))
    r = client.get("/food/usda", params={"q": "banana"})
    assert r.status_code == 404 and called == []


def test_with_a_key_the_route_returns_results_and_a_down_server_is_a_502(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "KEY")
    monkeypatch.setattr(usda, "fetch_json", lambda url: SAMPLE)
    r = client.get("/food/usda", params={"q": "banana"})
    assert r.status_code == 200 and [f["fdc_id"] for f in r.json()] == [1001, 1002]

    def down(url):
        raise OSError("no network")
    monkeypatch.setattr(usda, "fetch_json", down)
    assert client.get("/food/usda", params={"q": "banana"}).status_code == 502


def test_the_food_dialog_offers_the_usda_search_only_when_a_key_is_set(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "")
    assert "data-usda-search" not in client.get("/measurements", params={"tab": "food"}).text
    monkeypatch.setattr(config, "USDA_API_KEY", "KEY")
    assert "data-usda-search" in client.get("/measurements", params={"tab": "food"}).text


def test_a_blank_query_is_refused_without_calling_out(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "KEY")
    monkeypatch.setattr(usda, "fetch_json", lambda url: pytest.fail("must not be called"))
    assert client.get("/food/usda", params={"q": "  "}).json() == []


def test_a_key_saved_in_settings_turns_the_search_on_and_is_never_shown_again(client, db, monkeypatch):
    monkeypatch.setattr(config, "USDA_API_KEY", "")
    seen = []
    monkeypatch.setattr(usda, "fetch_json", lambda url: seen.append(url) or SAMPLE)
    mine = "A1b2C3d4E5f6G7h8I9j0K1l2"
    assert client.post("/settings/usda-key", data={"usda_api_key": "short"}).status_code == 422
    assert client.post("/settings/usda-key", data={"usda_api_key": mine}, follow_redirects=False).status_code == 303
    assert "data-usda-search" in client.get("/measurements", params={"tab": "food"}).text
    assert client.get("/food/usda", params={"q": "banana"}).status_code == 200 and f"api_key={mine}" in seen[0]
    assert mine not in client.get("/settings").text
    assert client.post("/settings/usda-key", data={"remove": "1"}, follow_redirects=False).status_code == 303
    assert client.get("/food/usda", params={"q": "banana"}).status_code == 404
