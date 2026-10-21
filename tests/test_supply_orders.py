"""Ordering supplies (syringes, alcohol pads, pen needles...): they go on a New Order like anything else, and what arrives is added to the supply's count at
check-in, kept in step if the received amount is later corrected."""

import html
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Category, InventoryItem, Order, OrderItem, SupplyType
from supply_helpers import make_supply

TODAY = date.today()
ORDER_DATE = (TODAY - timedelta(days=5)).isoformat()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Sorder%")).delete(synchronize_session=False)
        s.commit()


def place(client, **lines):
    data = {"order_date": ORDER_DATE, "tracking_number": "SUPPLY1"}
    for i, line in enumerate(lines.values()):
        data.update({f"lines-{i}-{k}": v for k, v in line.items()})
    return client.post("/inventory/orders", data=data, follow_redirects=False)


def item(name="Sorder Alcohol Pads"):
    with SessionLocal() as s:
        row = s.scalar(select(InventoryItem).where(InventoryItem.name == name))
        return None if row is None else (row.id, row.category, row.supply_type, row.count)


def check_in(client, item_id, order_id, **received):
    data = {"arrival_date": (TODAY - timedelta(days=1)).isoformat()}
    data.update({f"received_quantity_{k}": str(v) for k, v in received.items()})
    return client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data=data, follow_redirects=False)


def the_line(item_id):
    with SessionLocal() as s:
        li = s.scalar(select(OrderItem).where(OrderItem.inventory_item_id == item_id))
        return li.id, li.order_id


def test_a_new_supply_can_be_ordered_and_the_count_waits_for_check_in(client, me):
    r = place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "200", "cost": "6.50"})
    assert r.status_code == 303
    item_id, category, kind, count = item()
    assert category == Category.SUPPLY and kind == SupplyType.ALCOHOL_PAD and count == 0          # still on its way
    li, order_id = the_line(item_id)
    assert check_in(client, item_id, order_id, **{str(li): 200}).status_code == 303
    assert item()[3] == 200


def test_a_partial_delivery_adds_only_what_arrived(client, me):
    place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "200"})
    item_id = item()[0]
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 150})
    assert item()[3] == 150


def test_restocking_an_existing_supply_adds_to_what_is_there(client, me):
    existing = make_supply(me, "Sorder Dosing Syringes", SupplyType.DOSING_SYRINGE, 12)
    place(client, a={"mode": "existing", "item_id": str(existing), "quantity": "100"})
    li, order_id = the_line(existing)
    check_in(client, existing, order_id, **{str(li): 100})
    assert item("Sorder Dosing Syringes")[3] == 112


def test_correcting_the_received_amount_later_adjusts_the_count_by_the_difference(client, me):
    place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "200"})
    item_id = item()[0]
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 200})
    r = client.post(f"/inventory/{item_id}/orders/{li}", data={"order_date": ORDER_DATE, "quantity": "200", "received_quantity": "180",
                                                              "arrival_date": (TODAY - timedelta(days=1)).isoformat()}, follow_redirects=False)
    assert r.status_code == 303 and item()[3] == 180


def test_using_supplies_and_then_restocking_keeps_the_count_honest(client, me):
    place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "50"})
    item_id = item()[0]
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 50})
    with SessionLocal() as s:
        s.get(InventoryItem, item_id).count -= 8                                   # shots used 8
        s.commit()
    place(client, a={"mode": "existing", "item_id": str(item_id), "quantity": "20"})
    with SessionLocal() as s:
        second = s.scalars(select(OrderItem).where(OrderItem.inventory_item_id == item_id, OrderItem.id != li)).one()
        order2 = second.order_id
        line2 = second.id
    check_in(client, item_id, order2, **{str(line2): 20})
    assert item()[3] == 62


def test_an_order_that_has_not_arrived_shows_on_the_way(client, me):
    place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "200"})
    page = html.unescape(client.get("/inventory").text)
    assert "SUPPLY1" in page and "Sorder Alcohol Pads" in page


def test_a_new_supply_line_needs_a_name_and_a_quantity(client, me):
    for line in ({"mode": "new", "category": "Supply", "name": "", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "5"},
                 {"mode": "new", "category": "Supply", "name": "Sorder Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "0"},
                 {"mode": "new", "category": "Supply", "name": "Sorder Pads", "supply_type": "bogus", "quantity": "5"}):
        assert place(client, a=line).status_code == 422
    assert item("Sorder Pads") is None


def test_the_new_order_dialog_offers_supplies(client, me):
    make_supply(me, "Sorder Pen Needles", SupplyType.PEN_NEEDLE, 3)
    page = html.unescape(client.get("/inventory").text)
    tpl = page.split('id="new-order-line-template"', 1)[1].split("</template>", 1)[0]
    assert 'value="Supply"' in tpl and 'name="lines-__I__-supply_type"' in tpl and "Sorder Pen Needles" in tpl


def test_medicine_lines_are_unaffected(client, me):
    r = place(client, a={"mode": "new", "category": "Medicine", "name": "Sorder Peptide", "medium": "Lyophilized", "vial_size_mg": "10", "quantity": "3"})
    assert r.status_code == 303
    item_id = item("Sorder Peptide")[0]
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 3})
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).count == 0 and s.get(InventoryItem, item_id).available_count == 3


def test_migration_0068_adds_the_column(tmp_path):
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
    command.upgrade(cfg, "0068")
    with sqlite3.connect(db) as c:
        assert "stock_applied" in {r[1] for r in c.execute("pragma table_info(order_items)")}
    command.downgrade(cfg, "0067")
    with sqlite3.connect(db) as c:
        assert "stock_applied" not in {r[1] for r in c.execute("pragma table_info(order_items)")}


def test_a_supplys_page_shows_its_orders_with_check_in_and_no_sale_form(client, me):
    place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Alcohol Pads", "supply_type": SupplyType.ALCOHOL_PAD.value, "quantity": "200"})
    item_id = item()[0]
    page = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Order History" in page and 'data-action="check-in"' in page and 'id="checkin-dialog"' in page
    assert 'id="sale-dialog"' not in page and 'data-action="add-order"' not in page and "Sale History" not in page
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 200})
    page = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Count: <strong" in page and ">200<" in page.replace(" ", "")


def test_a_supply_with_no_orders_keeps_its_plain_page(client, me):
    existing = make_supply(me, "Sorder Gloves", SupplyType.PEPTIDE_FILTER, 4)
    page = html.unescape(client.get(f"/inventory/{existing}").text)
    assert "Order History" not in page and "Count:" in page


def test_a_supply_can_be_ordered_with_no_type_for_things_that_are_not_consumed(client, me):
    r = place(client, a={"mode": "new", "category": "Supply", "name": "Sorder Sharps Container", "supply_type": "", "quantity": "2", "cost": "9.00"})
    assert r.status_code == 303
    item_id, category, kind, count = item("Sorder Sharps Container")
    assert category == Category.SUPPLY and kind is None
    li, order_id = the_line(item_id)
    check_in(client, item_id, order_id, **{str(li): 2})
    assert item("Sorder Sharps Container")[3] == 2                         # still counted, just never used up by a shot or a reconstitution
    page = html.unescape(client.get('/inventory').text)
    tpl = page.split('id="new-order-line-template"', 1)[1].split('</template>', 1)[0]
    assert '<option value="">— None —</option>' in tpl.split('lines-__I__-supply_type', 1)[1][:200]
