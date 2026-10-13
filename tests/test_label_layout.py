"""Custom label sheets: the person enters the label maker's measurements (sheet, columns and rows, label size, margins, pitch, shape)."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.inventory.label_layout import Layout, LayoutError, css, parse_layout, per_page
from app.models import User

# 3 x 10 sheet of 2 5/8 x 1 in labels: the shape of an ordinary address-label sheet (typed in, as a person would from their template)
GOOD = {"page": "letter", "unit": "in", "cols": "3", "rows": "10", "width": "2.625", "height": "1", "top": "0.5", "left": "0.1875",
        "h_pitch": "2.75", "v_pitch": "1", "shape": "rect"}


def test_a_sheet_in_inches_parses_and_counts_its_labels():
    layout = parse_layout(GOOD)
    assert (layout.cols, layout.rows, layout.width, layout.height) == (3, 10, 2.625, 1.0) and per_page(layout) == 30


def test_millimetres_are_converted_to_inches():
    mm = {**GOOD, "unit": "mm", "width": "66.675", "height": "25.4", "top": "12.7", "left": "4.7625", "h_pitch": "69.85", "v_pitch": "25.4"}
    layout = parse_layout(mm)
    assert layout.width == pytest.approx(2.625, abs=1e-3) and layout.h_pitch == pytest.approx(2.75, abs=1e-3)


@pytest.mark.parametrize("change,fragment", [
    ({"cols": "0"}, "columns"), ({"rows": "99"}, "rows"), ({"width": "abc"}, "width"), ({"height": "-1"}, "height"),
    ({"page": "tabloid"}, "page"), ({"shape": "star"}, "shape"), ({"unit": "cm"}, "unit"),
    ({"h_pitch": "2"}, "pitch"),                                 # closer together than the label is wide: labels would overlap
    ({"cols": "5"}, "fit"),                                      # 5 across does not fit on the page
    ({"rows": "12"}, "fit"),
])
def test_bad_or_impossible_layouts_are_refused_with_a_reason(change, fragment):
    with pytest.raises(LayoutError) as err:
        parse_layout({**GOOD, **change})
    assert fragment in str(err.value).lower()


def test_the_css_places_the_labels_on_the_page_and_rounds_a_round_label():
    sheet = css(parse_layout(GOOD))
    assert "size: letter" in sheet and "repeat(3, 2.625in)" in sheet and "page-break-after" in sheet and "border-radius" not in sheet
    a4 = css(parse_layout({**GOOD, "page": "a4", "left": "0.1", "cols": "3"}))
    assert "size: A4" in a4
    round_sheet = css(parse_layout({**GOOD, "shape": "round", "cols": "4", "rows": "6", "width": "1.5", "height": "1.5", "h_pitch": "1.75", "v_pitch": "1.75"}))
    assert "border-radius: 50%" in round_sheet and "repeat(4, 1.5in)" in round_sheet


def test_a_layout_round_trips_through_a_dict():
    layout = parse_layout(GOOD)
    assert Layout.from_dict(layout.to_dict()) == layout


def tester():
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


@pytest.fixture(autouse=True)
def reset_labels():
    yield
    with SessionLocal() as s:
        u = s.get(User, tester())
        u.label_size, u.label_layout = "5160", None
        s.commit()


def test_saving_a_custom_sheet_in_settings_and_printing_a_test_sheet(client):
    r = client.post("/settings/labels", data={"label_size": "custom", "auto_print_labels": "1", **GOOD}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        u = s.get(User, tester())
        assert u.label_size == "custom" and u.label_layout["cols"] == 3
    sheet = client.get("/inventory/labels/test").text
    assert sheet.count('class="vial-label') == 30 and "repeat(3, 2.625in)" in sheet and "Test peptide" in sheet


def test_an_invalid_custom_sheet_is_refused_and_nothing_is_saved(client):
    r = client.post("/settings/labels", data={"label_size": "custom", **{**GOOD, "cols": "9"}})
    assert r.status_code == 422 and "fit" in r.text.lower()
    with SessionLocal() as s:
        assert s.get(User, tester()).label_size == "5160"
    assert client.post("/settings/labels", data={"label_size": "custom"}).status_code == 422          # custom with no measurements


def test_settings_offers_the_custom_sheet_fields(client):
    page = client.get("/settings").text
    for name in ("cols", "rows", "width", "height", "top", "left", "h_pitch", "v_pitch", "shape", "unit", "page"):
        assert f'name="{name}"' in page
    assert 'value="custom"' in page
