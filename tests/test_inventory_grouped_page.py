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


def test_the_list_has_one_row_for_the_peptide_and_a_direct_link_for_a_single_line(client, stock):
    text = page(client, "/inventory")
    assert len(re.findall(r">Groupr Peptide<", text)) == 1
    assert f'href="/inventory/stock/{stock[0]}"' in text or f'href="/inventory/stock/{stock[1]}"' in text
    assert f'href="/inventory/{stock[2]}"' in text and f'href="/inventory/stock/{stock[2]}"' not in text


def test_the_group_row_totals_the_stock_and_shows_the_earliest_expiry(client, stock):
    row = re.search(r'<tr class="stock-group">(?:(?!</tr>).)*Groupr Peptide(?:(?!</tr>).)*</tr>', page(client, "/inventory"), re.S).group(0)
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    assert cells[1] == "8"                                       # 3 received - 1 used on the 5 mg line, plus 2 + 4 on the 10 mg line
    assert "5 mg" in cells[2] and "10 mg" in cells[2]
    assert "09/01/2026" in " ".join(cells)                       # the earliest expiry across the lines


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
