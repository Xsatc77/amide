"""The calculator's syringe drawing: a needle, hub, barrel with the fill and tick marks, flange and plunger, and a scale lined up under the barrel."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def visual(client):
    page = client.get("/calculator").text
    return page.split('class="calc-syringe-visual"')[1].split('<dl class="kv')[0]


def test_the_drawing_has_every_part_of_a_syringe_and_keeps_the_hooks_the_script_uses(client, db):
    html = visual(client)
    for part in ("calc-needle", "calc-hub", "calc-syringe-barrel", "calc-flange", "calc-plunger"):
        assert f'class="{part}"' in html, part
    for hook in ('id="calc-fill"', 'id="calc-ticks"', 'id="calc-scale"'):
        assert hook in html, hook
    assert html.index("calc-needle") < html.index("calc-syringe-barrel") < html.index("calc-plunger")


def test_the_drawing_is_decorative_for_screen_readers(client, db):
    assert 'class="calc-syringe-visual" aria-hidden="true"' in client.get("/calculator").text


def test_the_scale_is_inset_by_the_needle_and_plunger_so_its_numbers_sit_under_the_barrel(client, db):
    needle = int(re.search(r"--syringe-left:\s*(\d+)px", CSS).group(1))
    plunger = int(re.search(r"--syringe-right:\s*(\d+)px", CSS).group(1))
    scale = re.search(r"\.calc-syringe-scale \{[^}]*\}", CSS).group(0)
    assert "margin-left: var(--syringe-left)" in scale and "margin-right: var(--syringe-right)" in scale and needle > 0 and plunger > 0
