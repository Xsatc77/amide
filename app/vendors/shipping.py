"""How long a vendor's orders take to arrive. Orders made up only of local-seller items (picked up in person) are left out."""


def is_local(order) -> bool:
    """True when the order has lines and every one of them is a local-seller item."""
    lines = getattr(order, "items", None) or []
    return bool(lines) and all(getattr(line.inventory_item, "local_seller", False) for line in lines)


def average_delivery(orders) -> tuple[float, int] | None:
    """(average days from order to arrival, number of orders) over the arrived orders, or None when there are none."""
    days = [(o.arrival_date - o.order_date).days for o in orders if o.arrival_date is not None and not is_local(o)]
    return (round(sum(days) / len(days), 1), len(days)) if days else None
