"""One inventory row per peptide: lines of the same peptide are grouped, and each line's lots are listed soonest-expiry first."""

from datetime import date
from types import SimpleNamespace as NS

from app.inventory.groups import group_items, lots, sort_use_first

D = date


def line(received, expires=None, arrived=D(2026, 1, 1), lot=None):
    return NS(received_quantity=received, expiration_date=expires, lot_number=lot, order=NS(arrival_date=arrived, id=1, vendor=None))


def item(name, *, owner=1, size=5, unit="mg", lines=(), used=0, medium="Lyophilized", storage=None, iid=1):
    it = NS(id=iid, name=name, owner_id=owner, vial_size_mg=size, vial_size_unit=NS(value=unit), medium=NS(value=medium) if medium else None,
            storage=NS(label=storage) if storage else None, order_items=list(lines), reconstituted_count=used, sold_count=0)
    arrived = [li for li in it.order_items if li.order.arrival_date is not None]
    it.available_count = sum(li.received_quantity or 0 for li in arrived) - used
    dated = [li.expiration_date for li in arrived if li.expiration_date and (li.received_quantity or 0) > 0]
    it.next_expiration = min(dated) if dated else None
    it.first_arrival = min((li.order.arrival_date for li in arrived), default=None)
    return it


def test_same_peptide_in_different_sizes_and_cases_is_one_group_with_totals():
    a = item("BPC-157", size=5, lines=[line(3, D(2027, 5, 1))], iid=1)
    b = item("bpc-157", size=10, lines=[line(2, D(2026, 12, 1))], iid=2)
    c = item("TB-500", size=5, lines=[line(1)], iid=3)
    groups = group_items([a, b, c])
    assert [g.name for g in groups] == ["BPC-157", "TB-500"]
    g = groups[0]
    assert g.total == 5 and g.earliest_expiration == D(2026, 12, 1) and g.sizes == ["10 mg", "5 mg"]
    assert [i.id for i in g.items] == [2, 1]                      # the line to use first (nearest expiry) comes first


def test_different_owners_are_never_mixed():
    mine, theirs = item("BPC-157", owner=1, iid=1), item("BPC-157", owner=2, iid=2)
    assert len(group_items([mine, theirs])) == 2


def test_a_single_line_is_a_group_of_one_and_empty_lines_still_group():
    only = item("DSIP", lines=[line(2)])
    gone = item("DSIP", size=10, lines=[line(1)], used=1, iid=2)
    g = group_items([only, gone])[0]
    assert g.total == 2 and len(g.items) == 2
    assert len(group_items([only])[0].items) == 1


def test_lots_are_sorted_use_first_and_use_is_taken_from_the_soonest_lots():
    it = item("BPC-157", lines=[line(3, D(2027, 6, 1), D(2026, 2, 1)), line(2, D(2026, 9, 1), D(2026, 3, 1)), line(2, None, D(2026, 1, 1))], used=3)
    result = lots(it)
    assert [(lot.expiration, lot.received, lot.remaining) for lot in result] == [
        (D(2026, 9, 1), 2, 0), (D(2027, 6, 1), 3, 2), (None, 2, 2)]       # 3 used: the soonest lot (2) is gone, then 1 from the next
    assert sum(lot.remaining for lot in result) == it.available_count


def test_lots_ignore_orders_that_have_not_arrived_and_never_go_negative():
    it = item("X", lines=[line(2, D(2026, 8, 1)), line(5, D(2026, 9, 1), arrived=None)], used=9)
    assert [lot.remaining for lot in lots(it)] == [0]


def test_groups_sort_use_first_with_empty_and_undated_last():
    soon = item("A", lines=[line(1, D(2026, 8, 1))], iid=1)
    later = item("B", lines=[line(1, D(2027, 8, 1))], iid=2)
    undated = item("C", lines=[line(1)], iid=3)
    empty = item("D", lines=[line(1, D(2026, 1, 1))], used=1, iid=4)
    assert [g.name for g in sort_use_first(group_items([empty, undated, later, soon]))] == ["A", "B", "C", "D"]
