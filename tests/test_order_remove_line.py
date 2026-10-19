"""Taking one item out of an order that has not arrived yet: the rest stay, shipping is shared between fewer items, and the tax is trimmed by the removed item's share."""

from datetime import timedelta

import pytest

from app import clock
from app.db import SessionLocal
from app.models import Category, InventoryItem, Medium, Order, OrderItem

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Order).filter(Order.vendor == "Acme Test Co").delete(synchronize_session=False)
        s.query(InventoryItem).filter(InventoryItem.name.like("Removeline %")).delete(synchronize_session=False)
        s.commit()


def make_order(uid, costs=(1000, 2000, 3000), tax=600, shipping=300, arrived=False):
    with SessionLocal() as s:
        order = Order(order_date=TODAY - timedelta(days=5), vendor="Acme Test Co", tax_cents=tax, shipping_cents=shipping,
                      arrival_date=TODAY if arrived else None)
        s.add(order)
        s.flush()
        ids = []
        for n, cost in enumerate(costs):
            item = InventoryItem(name=f"Removeline {n}", category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=10)
            s.add(item)
            s.flush()
            line = OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=1, cost_cents=cost, received_quantity=1 if arrived else None)
            s.add(line)
            s.flush()
            ids.append(line.id)
        s.commit()
        return order.id, ids


def remove(client, order_id, line_id, **form):
    return client.post(f"/inventory/orders/{order_id}/lines/{line_id}/remove", data=form, follow_redirects=False)


def shares(order_id):
    with SessionLocal() as s:
        order = s.get(Order, order_id)
        return order.tax_cents, order.shipping_cents, [(li.cost_cents, li.allocated_tax_cents, li.allocated_shipping_cents) for li in order.items]


def test_removing_an_item_keeps_the_others_and_resplits_tax_and_shipping_between_them(client, me):
    order_id, (a, b, c) = make_order(me)                       # costs 10, 20, 30; tax 6.00 and shipping 3.00 across all three
    r = remove(client, order_id, c, tax="3.00", shipping="3.00")      # the $30 item's share of the tax is $3.00
    assert r.status_code == 303
    tax, shipping, lines = shares(order_id)
    assert (tax, shipping) == (300, 300)
    assert lines == [(1000, 100, 100), (2000, 200, 200)]       # tax and shipping now split 1:2 between the two left


def test_blank_tax_and_shipping_clear_them(client, me):
    order_id, (a, b, c) = make_order(me)
    assert remove(client, order_id, c, tax="", shipping="").status_code == 303
    tax, shipping, lines = shares(order_id)
    assert tax is None and shipping is None and len(lines) == 2


def test_the_form_defaults_take_out_the_removed_items_share_of_the_tax_and_keep_the_shipping(client, me):
    order_id, (a, b, c) = make_order(me)
    page = client.get("/inventory/orders").text
    form = page.split(f'/inventory/orders/{order_id}/lines/{c}/remove', 1)[1].split("</form>", 1)[0]
    assert 'name="tax" value="3.00"' in form and 'name="shipping" value="3.00"' in form


def test_an_order_that_has_arrived_cannot_have_an_item_removed(client, me):
    order_id, (a, b, c) = make_order(me, arrived=True)
    page = client.get("/inventory/orders?status=all").text
    assert f"/inventory/orders/{order_id}/lines/{c}/remove" not in page
    assert remove(client, order_id, c, tax="", shipping="").status_code == 422
    assert len(shares(order_id)[2]) == 3


def test_the_only_item_cannot_be_removed(client, me):
    order_id, (a,) = make_order(me, costs=(1000,))
    assert remove(client, order_id, a, tax="", shipping="").status_code == 422
    assert len(shares(order_id)[2]) == 1


def test_a_bad_amount_is_refused_and_nothing_is_removed(client, me):
    order_id, (a, b, c) = make_order(me)
    assert remove(client, order_id, c, tax="abc", shipping="3.00").status_code == 422
    assert len(shares(order_id)[2]) == 3


def test_an_unknown_line_is_not_found(client, me):
    order_id, (a, b, c) = make_order(me)
    assert remove(client, order_id, 999999, tax="", shipping="").status_code == 404


def test_an_order_that_has_shipped_cannot_have_an_item_removed_either(client, me):
    order_id, (a, b, c) = make_order(me)
    with SessionLocal() as s:
        s.get(Order, order_id).shipped_date = TODAY
        s.commit()
    assert f"/inventory/orders/{order_id}/lines/{c}/remove" not in client.get("/inventory/orders").text
    assert remove(client, order_id, c, tax="", shipping="").status_code == 422
    assert len(shares(order_id)[2]) == 3
