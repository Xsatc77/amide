"""The top menu's order, Nutrition as its own item, Fitness Test inside Workouts."""

import re


def labels(page):
    nav = page.split('aria-label="Main"')[1].split("</nav>")[0]
    return [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r"<(?:a|span)[^>]*>(.*?)</(?:a|span)>", nav)]


def test_menu_order_and_links_placeholder(client, db):
    assert labels(client.get("/dashboard").text) == [
        "Dashboard", "Today", "Protocols", "Calendar", "Body", "Workouts", "Nutrition", "Inventory", "Vendors", "Library", "Calculator", "Links (soon)"]


def test_fitness_test_is_a_workouts_tab_and_not_a_menu_item(client, db):
    page = client.get("/fitness-test").text
    assert 'href="/fitness-test" class="wk-tab active"' in page
    assert 'href="/fitness-test"' not in page.split('aria-label="Main"')[1].split("</nav>")[0]
    assert "Fitness Test" in client.get("/workouts").text


def test_nutrition_highlights_on_the_food_tab_and_body_elsewhere(client, db):
    food = client.get("/measurements?tab=food").text.split('aria-label="Main"')[1].split("</nav>")[0]
    assert re.search(r'class="active"[^>]*>Nutrition<', food) and not re.search(r'class="active"[^>]*>Body<', food)
    body = client.get("/measurements").text.split('aria-label="Main"')[1].split("</nav>")[0]
    assert re.search(r'class="active"[^>]*>Body<', body)
