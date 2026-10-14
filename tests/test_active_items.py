"""Medicines that are not mixed from powder (pills, drops, salves, autoinjectors, inhalers, liquids) can be marked active, and then show on the Active
section with a picture for what they are, a gauge of what is left, and what the upcoming protocol will use."""

import html
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.inventory.item_art import image_for
from app.models import Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol, ProtocolItem, Route

TODAY = date.today()
ICONS = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "icons"


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Actitem%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Actitem%") | (InventoryItem.name == "B-12 drops")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Actitem%")).delete(synchronize_session=False)
        s.commit()


def make(uid, medium=Medium.DROPS, *, name="Actitem B12", units=60, size=5000.0, unit=DoseUnit.MCG, active=True, lot="L-77", dosed=0.0):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=0, medium=medium, vial_size_mg=size, vial_size_unit=unit,
                             is_active=active, dosed_units=dosed)
        s.add(item)
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=20), arrival_date=TODAY - timedelta(days=10))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=units, received_quantity=units, lot_number=lot))
        s.commit()
        return item.id


def protocol(uid, item_id, dose=5000.0, dose_unit=DoseUnit.MCG):
    with SessionLocal() as s:
        pep = Peptide(name="Actitem Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Actitem Protocol", start_date=TODAY - timedelta(days=5), owner_id=uid)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=dose, dose_unit=dose_unit, frequency=Frequency.DAILY, route=Route.ORAL, inventory_item_id=item_id))
        s.commit()


def card(client, item_id):
    page = html.unescape(client.get("/inventory").text)
    return page.split(f'data-active-item-id="{item_id}"', 1)[1].split("</article>", 1)[0]


@pytest.mark.parametrize("medium,image", [
    (Medium.PILL, "pill-bottle-blank-label.png"), (Medium.DROPS, "dropper-bottle-blank-label.png"), (Medium.SALVE, "salve-tube-blank-label.png"),
    (Medium.AUTOINJECTOR, "auto-injector.png"), (Medium.INHALER, "nasal-spray-blank-label.png"), (Medium.LIQUID, "vial-blank-label.png"),
])
def test_each_form_has_its_own_picture_and_the_file_exists(medium, image):
    assert image_for(medium) == f"img/icons/{image}" and (ICONS / image).is_file()


def test_the_item_dialog_offers_the_checkbox(client, me):
    page = client.get("/inventory").text
    assert 'name="is_active"' in page


def test_an_item_can_be_created_active_only_when_it_is_not_a_powder_vial(client, me):
    base = {"name": "Actitem Pills", "category": "Medicine", "vial_size_mg": "500", "vial_size_unit": "mg", "quantity": "30", "units_per_package": "30", "volume_ml": "30", "order_date": (TODAY - timedelta(days=3)).isoformat(),
            "is_active": "1"}
    assert client.post("/inventory", data={**base, "medium": "Pill"}, follow_redirects=False).status_code == 303
    assert client.post("/inventory", data={**base, "name": "Actitem Powder", "medium": "Lyophilized"}, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.scalar(select(InventoryItem.is_active).where(InventoryItem.name == "Actitem Pills")) is True
        assert s.scalar(select(InventoryItem.is_active).where(InventoryItem.name == "Actitem Powder")) is False


def test_editing_can_turn_it_on_and_off(client, me):
    item_id = make(me, active=False)
    form = {"name": "Actitem B12", "category": "Medicine", "medium": "Drops", "vial_size_mg": "5000", "vial_size_unit": "mcg", "is_active": "1"}
    assert client.post(f"/inventory/{item_id}", data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).is_active is True
    form.pop("is_active")
    client.post(f"/inventory/{item_id}", data=form, follow_redirects=False)
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).is_active is False


def test_the_quick_toggle_marks_active_and_inactive_and_is_owner_only(client, me):
    from photo_helpers import other_client
    item_id = make(me, active=False)
    assert client.post(f"/inventory/{item_id}/active", data={"active": "1"}, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).is_active is True
    with other_client("actitemother") as stranger:
        assert stranger.post(f"/inventory/{item_id}/active", data={"active": ""}, follow_redirects=False).status_code == 404
    client.post(f"/inventory/{item_id}/active", data={"active": ""}, follow_redirects=False)
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).is_active is False


def test_an_active_item_shows_with_its_picture_gauge_and_lot(client, me):
    item_id = make(me, Medium.DROPS, dosed=3.0)
    c = card(client, item_id)
    assert "dropper-bottle-blank-label.png" in c and 'role="meter"' in c and 'aria-valuenow="95"' in c
    assert "57 left of 60" in c
    assert 'class="lot-ribbon"' in c and "L-77" in c


def test_the_lot_is_the_one_being_used_not_always_the_first(client, me):
    item_id = make(me, units=2, lot="OLD-1", dosed=2.0)                     # the first lot (2 units) is used up
    with SessionLocal() as s:
        order = Order(order_date=TODAY - timedelta(days=5), arrival_date=TODAY - timedelta(days=2))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item_id, quantity=3, received_quantity=3, lot_number="NEW-2"))
        s.commit()
    c = card(client, item_id)
    assert "NEW-2" in c and "OLD-1" not in c


def test_the_card_says_what_the_protocol_will_use(client, me):
    item_id = make(me, units=10, dosed=0.0)
    protocol(me, item_id, dose=5000.0)                                       # one unit a day
    c = card(client, item_id)
    assert "10 more scheduled doses" in c


def test_part_doses_count_in_fractions(client, me):
    item_id = make(me, Medium.PILL, name="Actitem Pills", units=30, size=500.0, unit=DoseUnit.MG, dosed=0.5)
    c = card(client, item_id)
    assert "29.5 left of 30" in c and "pill-bottle-blank-label.png" in c


def test_an_inactive_item_and_a_powder_vial_are_not_in_the_active_section(client, me):
    off = make(me, active=False, name="Actitem Off")
    page = html.unescape(client.get("/inventory").text)
    assert f'data-active-item-id="{off}"' not in page
    on = make(me, active=True, name="Actitem On")
    assert f'data-active-item-id="{on}"' in html.unescape(client.get("/inventory").text)


def test_a_card_can_be_stopped_from_its_own_button(client, me):
    item_id = make(me)
    c = card(client, item_id)
    assert f'/inventory/{item_id}/active' in c and "Stop using" in c


def test_migration_0069_adds_the_column(tmp_path):
    import sqlite3
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent
    db = tmp_path / "m.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0069")
    with sqlite3.connect(db) as c:
        assert "is_active" in {r[1] for r in c.execute("pragma table_info(inventory_items)")}
    command.downgrade(cfg, "0068")
    with sqlite3.connect(db) as c:
        assert "is_active" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}


@pytest.mark.parametrize("name,expected", [("B-12 drops", "B"), ("Vitamin D pills", "Vt"), ("Healing salve", "Hl"), ("Magnesium Glycinate capsules", "Mg"), ("Nasal spray", "Ns"),
                                           ("Semaglutide pen", "Sm"), ("Drops", "Dr")])
def test_the_form_word_in_a_name_is_left_out_of_the_symbol(name, expected):
    from app.inventory.item_art import symbol_for
    assert symbol_for(name) == expected


def _silhouette(path, height):
    """Per row, the leftmost and rightmost visible pixel of the picture drawn `height` px tall (-1 where the row is empty)."""
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    visible = np.array(im)[:, :, 3] > 40
    left, right = [], []
    for row in visible:
        xs = np.where(row)[0]
        left.append(int(xs[0]) if len(xs) else -1)
        right.append(int(xs[-1]) if len(xs) else -1)
    return im.width, left, right


@pytest.mark.parametrize("medium", [Medium.PILL, Medium.DROPS, Medium.SALVE, Medium.AUTOINJECTOR, Medium.INHALER, Medium.LIQUID])
def test_the_tile_sits_wholly_inside_its_item_and_centred_on_it(medium):
    from app.inventory.item_art import tile_spec
    x, y, size, pic_h = tile_spec(medium)
    width, left, right = _silhouette(ICONS / Path(image_for(medium)).name, pic_h)
    cx, cy = x * width, y * pic_h
    top, bottom = int(cy - size / 2), int(cy + size / 2)
    assert top >= 0 and bottom < pic_h
    inner_left = max(left[r] for r in range(top, bottom + 1))
    inner_right = min(right[r] for r in range(top, bottom + 1))
    assert inner_left >= 0
    assert cx - size / 2 >= inner_left + 2 and cx + size / 2 <= inner_right - 2, "the tile hangs off the item"
    assert abs(cx - (inner_left + inner_right) / 2) <= 2, "the tile is not centred on the item"


def test_the_powder_vial_tile_fits_inside_the_glass_wherever_the_script_may_put_it():
    width, left, right = _silhouette(ICONS / "vial-blank-label.png", 140)
    size = 48
    for centre in range(int(0.30 * 140 + size / 2), int(0.77 * 140 - size / 2) + 1):         # the range app/static/js/vial-tile.js keeps the tile within
        top, bottom = centre - size // 2, centre + size // 2
        lo, hi = max(left[top:bottom + 1]), min(right[top:bottom + 1])
        cx = 0.462 * width
        assert cx - size / 2 >= lo + 1 and cx + size / 2 <= hi - 1, f"the tile hangs off the vial at {centre}px"


def test_an_active_item_carries_the_periodic_table_tile(client, me):
    item_id = make(me, Medium.DROPS, name="B-12 drops")
    c = card(client, item_id)
    assert 'class="element-tile on-item"' in c and '<span class="el-symbol">B</span>' in c and '<span class="el-amount">5000 mcg</span>' in c
    assert "--tile:" in c
