"""The Inventory page lists one row per peptide; opening it shows the stock breakdown, lot by lot, soonest expiry first."""

import html
import re
from datetime import date

import pytest

from app.db import SessionLocal
from app.models import Category, DoseUnit, InventoryItem, Medium, Order, OrderItem
from photo_helpers import other_client


def add_line(uid, name, size, lots, used=0):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED,
                             vial_size_mg=size, vial_size_unit=DoseUnit.MG, reconstituted_count=used)
        s.add(item)
        s.flush()
        for received, expires, arrived in lots:
            order = Order(order_date=arrived, arrival_date=arrived)
            s.add(order)
            s.flush()
            order.items.append(OrderItem(inventory_item_id=item.id, quantity=received, received_quantity=received, expiration_date=expires))
        s.commit()
        return item.id


@pytest.fixture
def stock(me):
    ids = []
    ids.append(add_line(me, "Groupr Peptide", 5, [(3, date(2027, 6, 1), date(2026, 3, 1))], used=1))
    ids.append(add_line(me, "Groupr Peptide", 10, [(2, date(2026, 9, 1), date(2026, 4, 1)), (4, date(2028, 1, 1), date(2026, 5, 1))]))
    ids.append(add_line(me, "Groupr Single", 5, [(2, date(2026, 8, 1), date(2026, 3, 1))]))
    yield ids
    with SessionLocal() as s:
        s.query(InventoryItem).filter(InventoryItem.id.in_(ids)).delete(synchronize_session=False)
        s.query(Order).filter(~Order.items.any()).delete(synchronize_session=False)
        s.commit()


def page(client, path):
    return html.unescape(client.get(path).text)


def test_the_list_has_one_row_per_item_each_linking_to_its_own_page(client, stock):
    text = page(client, "/inventory")
    assert len(re.findall(r">Groupr Peptide<", text)) == 2                  # two items of one name are two rows
    assert f'href="/inventory/{stock[0]}"' in text and f'href="/inventory/{stock[1]}"' in text and f'href="/inventory/{stock[2]}"' in text


def test_each_row_shows_its_own_stock_and_expiry(client, stock):
    rows = re.findall(r"<tr>(?:(?!</tr>).)*>Groupr Peptide<(?:(?!</tr>).)*</tr>", page(client, "/inventory"), re.S)
    cells = [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)] for r in rows]
    assert sorted(c[1] for c in cells) == ["2", "6"]                         # 3 received - 1 used on the 5 mg item; 2 + 4 on the 10 mg item
    assert "09/01/2026" in " ".join(" ".join(c) for c in cells)


def test_the_breakdown_page_lists_every_line_and_lot_soonest_expiry_first(client, stock):
    text = page(client, f"/inventory/stock/{stock[0]}")
    assert "Groupr Peptide" in text and "5 mg" in text and "10 mg" in text
    sep, jun, jan = text.find("09/01/2026"), text.find("06/01/2027"), text.find("01/01/2028")
    assert -1 not in (sep, jun, jan) and sep < jun < jan         # lots from different lines, soonest expiry first
    assert f'href="/inventory/{stock[0]}"' in text and f'href="/inventory/{stock[1]}"' in text      # each line opens its own page
    assert "Groupr Single" not in text


def test_someone_elses_group_is_a_404(client, stock):
    with other_client("grpother") as stranger:
        assert stranger.get(f"/inventory/stock/{stock[0]}").status_code == 404
    assert client.get("/inventory/stock/999999").status_code == 404


def test_the_same_peptide_in_two_mediums_is_two_rows_with_their_own_stock(client, me):
    ids = []
    with SessionLocal() as s:
        for medium, qty in ((Medium.DROPS, 59), (Medium.LIQUID, 1)):
            item = InventoryItem(name="Mediumsplit B12", category=Category.MEDICINE, owner_id=me, count=0, medium=medium,
                                 vial_size_mg=5000 if medium == Medium.DROPS else 10, vial_size_unit=DoseUnit.MCG if medium == Medium.DROPS else DoseUnit.MG)
            s.add(item)
            s.flush()
            order = Order(order_date=date(2026, 10, 1), arrival_date=date(2026, 10, 1))
            s.add(order)
            s.flush()
            order.items.append(OrderItem(inventory_item_id=item.id, quantity=qty, received_quantity=qty))
            ids.append(item.id)
        s.commit()
    try:
        text = page(client, "/inventory")
        assert len(re.findall(r">Mediumsplit B12<", text)) == 2                       # one row each, not "2 lines in stock"
        assert "2 lines in stock" not in text.split("Mediumsplit B12")[1][:400]
        breakdown = page(client, f"/inventory/stock/{ids[0]}")
        assert "Drops" in breakdown and "Liquid" not in breakdown.split("<h1>")[1].split("</section>")[0]
    finally:
        with SessionLocal() as s:
            s.query(InventoryItem).filter(InventoryItem.id.in_(ids)).delete(synchronize_session=False)
            s.query(Order).filter(~Order.items.any()).delete(synchronize_session=False)
            s.commit()
