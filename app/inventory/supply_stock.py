"""Supplies (syringes, alcohol pads, needles) are counted in the item's own `count`, which shots and reconstitutions draw down. An order of supplies adds to that
count when it is checked in, so the order line remembers how much of it has been added (`stock_applied`) and `sync` brings the count in line with what the line
now says was received. Calling it again changes nothing, and correcting the received amount later moves the count by the difference."""

from app.models import Category, OrderItem


def sync(line: OrderItem) -> None:
    item = line.inventory_item
    if item is None or item.category != Category.SUPPLY:
        return
    arrived = line.order is not None and line.order.arrival_date is not None
    wanted = (line.received_quantity or 0) if arrived else 0
    applied = line.stock_applied or 0
    item.count = max((item.count or 0) + wanted - applied, 0)
    line.stock_applied = wanted
