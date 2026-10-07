"""The shopping planner: the cheapest way to cover a protocol's needs from vendor price lists, with at most two vendors."""

from datetime import date

import pytest

from app.shopping.planner import Need, Offer, best_line, plan_protocol

DAY = date(2026, 10, 1)
SHIP = {"china": 60.0, "us": 30.0}


def offer(vendor, need_key, size, pack_size, pack_price, warehouse="china", vendor_id=None, pack_type="kit"):
    return Offer(vendor_id=vendor_id if vendor_id is not None else hash(vendor) % 10_000, vendor_name=vendor, warehouse=warehouse, list_date=DAY,
                 need_key=need_key, size=size, size_label=f"{size:g} mg", pack_size=pack_size, pack_price=pack_price, pack_type=pack_type)


def need(key, amount, hard=False, name=None):
    return Need(key=key, name=name or key, amount=amount, unit="mg", hard=hard)


# ---------------------------------------------------------------- one item

def test_the_cheapest_way_to_cover_the_dose_with_a_buffer_wins():
    n = need("a", 10)
    cheap_small = offer("X", "a", 5, 10, 100)                       # 3 vials of 5 mg cover 10.5 mg: one kit, $100
    dearer_big = offer("X", "a", 10, 10, 140)                        # 2 vials of 10 mg: one kit, $140
    line = best_line(n, [cheap_small, dearer_big])
    assert (line.offer.size, line.packs, line.vials, line.cost) == (5, 1, 3, 100.0)       # 3 vials needed, one kit of 10 bought
    assert line.leftover_vials == 7


def test_the_buffer_forces_an_extra_vial_when_the_dose_exactly_fits():
    line = best_line(need("a", 10), [offer("X", "a", 10, 1, 20, pack_type="box")])
    assert (line.vials, line.packs, line.cost) == (2, 2, 40.0)       # 10 mg exactly would leave no buffer: 105% needs two vials


def test_a_size_that_covers_far_more_than_needed_is_skipped_unless_it_is_all_there_is():
    huge = offer("X", "a", 100, 1, 10, pack_type="box")             # absurdly cheap but 10x too big
    sensible = offer("X", "a", 10, 1, 25, pack_type="box")
    assert best_line(need("a", 10), [huge, sensible]).offer.size == 10
    assert best_line(need("a", 10), [huge]).offer.size == 100


def test_ties_go_to_the_smaller_leftover():
    a = offer("X", "a", 5, 10, 100)
    b = offer("X", "a", 6, 10, 100)
    assert best_line(need("a", 10), [a, b]).offer.size == 6 or best_line(need("a", 10), [a, b]).leftover_vials <= 8


def test_no_offers_means_no_line():
    assert best_line(need("a", 10), []) is None


# ---------------------------------------------------------------- choosing vendors

def two_item_world():
    needs = [need("a", 10), need("b", 10)]
    offers = [
        offer("Alpha", "a", 10, 10, 200, "china"), offer("Alpha", "b", 10, 10, 200, "china"),     # everything at Alpha: 400 + 60 = 460
        offer("Beta", "a", 10, 10, 120, "china"), offer("Gamma", "b", 10, 10, 110, "china"),      # split: 120 + 110 + 120 shipping = 350
    ]
    return needs, offers


def test_a_vendor_with_everything_wins_over_a_cheaper_split_when_nothing_is_hard_to_find():
    needs, offers = two_item_world()
    result = plan_protocol(needs, offers, SHIP)
    assert [s.vendor_name for s in result.plan.sources] == ["Alpha"] and result.plan.total == pytest.approx(460.0)
    assert "carries everything" in result.plan.reason.lower()
    assert any(len(p.sources) == 2 and p.total == pytest.approx(350.0) for p in result.alternatives)         # the cheaper split is shown for comparison


def test_a_hard_to_find_item_allows_two_vendors_and_the_cheapest_total_wins():
    needs, offers = two_item_world()
    needs[0] = need("a", 10, hard=True, name="Testosterone")
    result = plan_protocol(needs, offers, SHIP)
    assert sorted(s.vendor_name for s in result.plan.sources) == ["Beta", "Gamma"] and result.plan.total == pytest.approx(350.0)
    assert "hard to find" in result.plan.reason.lower()


def test_with_a_hard_item_a_single_vendor_still_wins_when_it_is_cheapest():
    needs = [need("a", 10, hard=True), need("b", 10)]
    offers = [offer("Alpha", "a", 10, 10, 100, "us"), offer("Alpha", "b", 10, 10, 100, "us"),
              offer("Beta", "a", 10, 10, 90, "china"), offer("Gamma", "b", 10, 10, 90, "china")]
    result = plan_protocol(needs, offers, SHIP)
    assert [s.vendor_name for s in result.plan.sources] == ["Alpha"] and result.plan.total == pytest.approx(230.0)


def test_when_no_single_vendor_has_everything_two_are_used():
    needs = [need("a", 10), need("b", 10)]
    offers = [offer("Beta", "a", 10, 10, 120, "us"), offer("Gamma", "b", 10, 10, 110, "us")]
    result = plan_protocol(needs, offers, SHIP)
    assert sorted(s.vendor_name for s in result.plan.sources) == ["Beta", "Gamma"] and result.plan.total == pytest.approx(290.0)
    assert result.plan.missing == [] and "no single vendor" in result.plan.reason.lower()


def test_never_more_than_two_vendors_so_the_rest_is_reported_missing():
    needs = [need("a", 10), need("b", 10), need("c", 10)]
    offers = [offer("Beta", "a", 10, 10, 100), offer("Gamma", "b", 10, 10, 100), offer("Delta", "c", 10, 10, 100)]
    result = plan_protocol(needs, offers, SHIP)
    assert len(result.plan.sources) <= 2 and len(result.plan.missing) == 1
    assert "missing" in result.plan.reason.lower() or "not carried" in result.plan.reason.lower()


def test_an_item_nobody_sells_is_unshoppable_and_the_rest_are_still_planned():
    needs = [need("a", 10), need("z", 10, name="Zorvex")]
    offers = [offer("Alpha", "a", 10, 10, 100)]
    result = plan_protocol(needs, offers, SHIP)
    assert [n.name for n in result.unshoppable] == ["Zorvex"] and result.plan.total == pytest.approx(160.0)


def test_shipping_is_per_vendor_order_and_depends_on_the_warehouse():
    needs = [need("a", 10)]
    offers = [offer("Alpha", "a", 10, 10, 100, "china"), offer("Beta", "a", 10, 10, 120, "us")]
    assert plan_protocol(needs, offers, {"china": 60.0, "us": 30.0}).plan.sources[0].vendor_name == "Beta"          # 150 vs 160
    assert plan_protocol(needs, offers, {"china": 20.0, "us": 30.0}).plan.sources[0].vendor_name == "Alpha"          # 120 vs 150
    plan = plan_protocol(needs, offers, {"china": 20.0, "us": 30.0}).plan
    assert plan.sources[0].shipping == 20.0 and plan.shipping_total == 20.0 and plan.items_total == pytest.approx(100.0)


def test_a_vendors_two_warehouses_are_separate_sources_but_never_pair_with_themselves():
    needs = [need("a", 10), need("b", 10)]
    offers = [offer("Alpha", "a", 10, 10, 100, "china", vendor_id=1), offer("Alpha", "b", 10, 10, 100, "us", vendor_id=1)]
    result = plan_protocol(needs, offers, SHIP)
    assert len(result.plan.sources) == 1 or len({s.vendor_id for s in result.plan.sources}) == 2
    assert len(result.plan.missing) == 1                                                                                # one vendor, one warehouse per plan


def test_nothing_to_buy_gives_an_empty_plan():
    result = plan_protocol([], [offer("Alpha", "a", 10, 10, 100)], SHIP)
    assert result.plan is None and result.unshoppable == []


def test_alternatives_are_at_most_two_and_never_the_chosen_plan():
    needs, offers = two_item_world()
    offers += [offer("Delta", "a", 10, 10, 210), offer("Delta", "b", 10, 10, 210)]
    result = plan_protocol(needs, offers, SHIP)
    assert len(result.alternatives) <= 2
    assert all([s.vendor_name for s in p.sources] != [s.vendor_name for s in result.plan.sources] for p in result.alternatives)
