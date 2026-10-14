"""Active vial cards: a gauge of what is left (from the reconstitution math and the doses taken), what the upcoming protocol will use,
the Hospira BAC picture, and a lot banner in the lower right corner."""

import html
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.inventory import gauge
from app.models import (ActiveVial, Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Protocol, ProtocolItem, Peptide, User)
from test_run_out import make_protocol, make_stock

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(ActiveVial).delete()
        s.query(Protocol).filter(Protocol.name.like("Runout%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Runout%")).delete(synchronize_session=False)
        s.query(InventoryItem).filter(InventoryItem.name.like("Hospira%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Runout%")).delete(synchronize_session=False)
        s.commit()


def open_vial(uid, item_id, *, water=2.0, left=1.8, conc=5.0, discard_in=28, lot=None):
    with SessionLocal() as s:
        v = ActiveVial(owner_id=uid, inventory_item_id=item_id, concentration_mg_ml=conc, water_ml=water, volume_remaining_ml=left, date_mixed=TODAY,
                       discard_by=TODAY + timedelta(days=discard_in), lot_number=lot, doses_total=10)
        s.add(v)
        s.commit()
        return v.id


def page(client):
    return html.unescape(client.get("/inventory").text)


def card(text, vial_id):
    return text.split(f'data-vial-id="{vial_id}"', 1)[1].split("</article>", 1)[0]


# ---- the arithmetic -------------------------------------------------------------------------------------------------------------------

def days(n):
    return [TODAY + timedelta(days=i) for i in range(1, n + 1)]


def test_the_simulation_takes_each_upcoming_dose_until_the_vial_cannot_cover_one():
    doses = [(d, 1.0) for d in days(12)]
    out = gauge.simulate(9.0, doses, TODAY + timedelta(days=60))
    assert out.count == 9 and out.through == days(9)[-1] and out.short_on == days(10)[-1] and out.left_after == pytest.approx(0.0)


def test_titration_doses_that_grow_use_the_vial_faster():
    day = lambda n: TODAY + timedelta(days=n)
    doses = [(day(1), 0.5), (day(8), 1.0), (day(15), 2.0), (day(22), 2.0)]
    out = gauge.simulate(4.0, doses, TODAY + timedelta(days=60))
    assert out.count == 3 and out.left_after == pytest.approx(0.5) and out.short_on == day(22)


def test_doses_after_the_discard_date_do_not_count():
    out = gauge.simulate(50.0, [(d, 1.0) for d in days(40)], TODAY + timedelta(days=10))
    assert out.count == 10 and out.short_on is None and out.left_after == pytest.approx(40.0)


def test_no_upcoming_doses_means_no_projection():
    out = gauge.simulate(5.0, [], TODAY + timedelta(days=10))
    assert out.count == 0 and out.through is None and out.short_on is None


def test_the_level_follows_how_much_is_left():
    assert gauge.level(0.8, 5) == "ok" and gauge.level(0.2, 5) == "ok" and gauge.level(0.1, 5) == "low"
    assert gauge.level(0.6, 2) == "low" and gauge.level(0.5, 1) == "low" and gauge.level(0.0, 0) == "empty"
    assert gauge.level(0.8, None) == "ok" and gauge.level(0.05, None) == "low"


# ---- the cards ------------------------------------------------------------------------------------------------------------------------

def test_a_reconstituted_vial_shows_what_is_left_and_what_the_protocol_will_use(client, me):
    item = make_stock(me, vials=2, size=10.0)
    make_protocol(me, item, dose=1.0, frequency=Frequency.DAILY)
    vid = open_vial(me, item, water=2.0, left=1.8)                                 # 10 mg in 2 mL = 5 mg/mL; 1 mg (0.2 mL) already drawn
    c = card(page(client), vid)
    assert 'role="meter"' in c and 'aria-valuenow="90"' in c
    assert "9 mg left of 10 mg" in c
    assert "9 more scheduled doses" in c


def test_the_card_says_when_the_next_dose_needs_a_new_vial(client, me):
    item = make_stock(me, vials=2, size=10.0)
    make_protocol(me, item, dose=1.0, frequency=Frequency.DAILY)
    vid = open_vial(me, item, water=2.0, left=0.5)                                 # 2.5 mg left: two full doses, the third is short
    c = card(page(client), vid)
    assert "2 more scheduled doses" in c and "needs a new vial" in c


def test_a_vial_no_protocol_uses_just_shows_the_gauge(client, me):
    item = make_stock(me, vials=2, size=10.0)
    vid = open_vial(me, item, water=2.0, left=1.0)
    c = card(page(client), vid)
    assert 'aria-valuenow="50"' in c and "5 mg left of 10 mg" in c and "No active protocol" in c


def test_bac_water_gauges_millilitres_and_uses_the_hospira_picture(client, me):
    with SessionLocal() as s:
        item = InventoryItem(name="Hospira BAC Water 30 mL", category=Category.BAC_WATER, owner_id=me, count=0)
        s.add(item)
        s.commit()
        item_id = item.id
    vid = open_vial(me, item_id, water=30.0, left=27.0, conc=None, discard_in=20)
    c = card(page(client), vid)
    assert "vial-bac-hospira.png" in c and "27 mL left of 30 mL" in c and 'aria-valuenow="90"' in c


def test_other_bac_water_keeps_the_plain_vial_picture(client, me):
    with SessionLocal() as s:
        item = InventoryItem(name="Acme BAC Water", category=Category.BAC_WATER, owner_id=me, count=0)
        s.add(item)
        s.commit()
        item_id = item.id
    vid = open_vial(me, item_id, water=10.0, left=5.0, conc=None)
    try:
        c = card(page(client), vid)
        assert "vial-bac-hospira" not in c and "vial-blank-label.png" in c
    finally:
        with SessionLocal() as s:
            s.query(ActiveVial).delete()
            s.query(InventoryItem).filter(InventoryItem.name == "Acme BAC Water").delete()
            s.commit()


def test_a_vial_with_a_lot_wears_a_green_banner_with_it(client, me):
    item = make_stock(me, vials=2, size=10.0)
    vid = open_vial(me, item, lot="26-261-DX20P")
    c = card(page(client), vid)
    assert 'class="lot-ribbon"' in c and "26-261-DX20P" in c


def test_no_lot_anywhere_means_no_banner(client, me):
    item = make_stock(me, vials=2, size=10.0)
    vid = open_vial(me, item)
    assert "lot-ribbon" not in card(page(client), vid)


def test_an_older_vial_without_a_stored_lot_shows_the_lot_in_use_marked_as_an_estimate(client, me):
    item = make_stock(me, vials=3, size=10.0)
    with SessionLocal() as s:
        line = s.scalar(select(OrderItem).where(OrderItem.inventory_item_id == item))
        line.lot_number = "EST-9"
        inv = s.get(InventoryItem, item)
        inv.reconstituted_count = 1
        s.commit()
    vid = open_vial(me, item)
    c = card(page(client), vid)
    assert "EST-9" in c and "lot-ribbon" in c


def test_reconstituting_records_the_lot_being_used(client, me):
    from supply_helpers import stock_everything
    stock_everything(me)
    item = make_stock(me, vials=3, size=10.0)
    with SessionLocal() as s:
        s.scalar(select(OrderItem).where(OrderItem.inventory_item_id == item)).lot_number = "REAL-1"
        s.commit()
    r = client.post("/calculator/reconstitute", data={"inventory_item_id": str(item), "water_ml": "2", "dose_value": "1", "dose_unit": "mg",
                                                     "discard_by": str(TODAY + timedelta(days=28))}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.scalar(select(ActiveVial.lot_number).where(ActiveVial.inventory_item_id == item)) == "REAL-1"


def test_migration_0064_adds_the_lot_column(tmp_path):
    import sqlite3
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent
    db = tmp_path / "m.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0064")
    with sqlite3.connect(db) as c:
        assert "lot_number" in {r[1] for r in c.execute("pragma table_info(active_vials)")}
    command.downgrade(cfg, "0063")
    with sqlite3.connect(db) as c:
        assert "lot_number" not in {r[1] for r in c.execute("pragma table_info(active_vials)")}
