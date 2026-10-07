"""The shopping plan as plain text (for a text file or an email): the same data as the dialog, spelled out."""

from datetime import date

from app.shopping.text import shop_text

DAY = date(2026, 10, 7)


def line(peptide="Zorvex", size="10 mg", packs=1, pack_size=10, pack_type="kit", vials=2, per_vial=20.0, cost=200.0, leftover=8):
    return {"peptide": peptide, "size_label": size, "pack_type": pack_type, "pack_size": pack_size, "packs": packs, "vials_needed": vials,
            "per_vial": per_vial, "cost": cost, "leftover_vials": leftover}


def source(vendor="Acme Labs", warehouse="china", shipping=60.0, lines=None):
    lines = lines or [line()]
    items = sum(l["cost"] for l in lines)
    return {"vendor": vendor, "vendor_id": 1, "warehouse": warehouse, "list_date": "2026-10-01", "shipping": shipping, "items_total": items,
            "total": items + shipping, "lines": lines}


def data(sources=None, **extra):
    sources = sources or [source()]
    plan = {"total": sum(s["total"] for s in sources), "items_total": sum(s["items_total"] for s in sources),
            "shipping_total": sum(s["shipping"] for s in sources), "reason": "Acme Labs carries everything", "missing": [], "sources": sources}
    return {"status": "ok", "protocol": {"id": 1, "name": "Spring cut"}, "shipping": {"china": 60.0, "us": 30.0}, "bac": None, "plan": plan,
            "alternatives": [], "unshoppable": []} | extra


def test_one_order_is_spelled_out_with_vendor_items_shipping_and_totals():
    text = shop_text(data(), DAY)
    assert text.splitlines()[0] == "Shopping plan: Spring cut"
    for expected in ("10/07/2026", "Acme Labs", "China warehouse", "price list dated 10/01/2026", "Zorvex 10 mg", "1 x kit of 10", "2 vials needed",
                     "$20.00 per vial", "Cost: $200.00", "8 vials left over", "Items: $200.00", "Shipping: $60.00", "Order total: $260.00", "Grand total: $260.00"):
        assert expected in text, expected
    assert "Acme Labs carries everything" in text


def test_the_shipping_fees_used_are_stated():
    assert "China $60.00, US $30.00" in shop_text(data(), DAY)


def test_two_orders_each_get_their_own_block_and_shipping():
    plan_sources = [source("Acme Labs", "china", 60.0), source("Beta Labs", "us", 30.0, [line("Quillamine", cost=110.0, per_vial=11.0)])]
    text = shop_text(data(plan_sources), DAY)
    assert "Order 1 of 2" in text and "Order 2 of 2" in text and "US warehouse" in text
    assert "Shipping: $30.00" in text and "Grand total: $400.00" in text


def test_one_order_is_not_numbered():
    assert "Order 1" not in shop_text(data(), DAY)


def test_singular_and_none_wording():
    text = shop_text(data([source(lines=[line(vials=1, leftover=0, pack_size=1, pack_type="box", cost=20.0)])]), DAY)
    assert "1 vial needed" in text and "None left over" in text and "1 x box" in text


def test_unavailable_items_and_bac_water_are_listed():
    text = shop_text(data(unshoppable=[{"name": "Rarepeptide", "reason": "No current price list carries it in a usable size"}],
                          bac={"ml": 62.5, "bottles": 3}), DAY)
    assert "Not available on the current price lists" in text and "Rarepeptide: No current price list carries it" in text
    assert "BAC water" in text and "62.5 mL" in text and "3 bottles of 30 mL" in text


def test_items_the_chosen_vendors_do_not_cover_are_listed():
    d = data()
    d["plan"]["missing"] = ["Quillamine"]
    assert "Not covered by these vendors: Quillamine" in shop_text(d, DAY)


def test_alternatives_are_left_out():
    d = data(alternatives=[{"total": 1.0, "sources": [source("Other Labs")], "vs_chosen": -5.0}])
    assert "Other Labs" not in shop_text(d, DAY)


def test_a_protocol_without_an_end_date_says_so():
    assert "no end date" in shop_text({"status": "no_end_date"}, DAY).lower()


def test_nothing_to_buy_says_so():
    text = shop_text(data(status="nothing_to_buy", plan=None, unshoppable=[]), DAY)
    assert "nothing to buy" in text.lower()


def test_no_plan_but_unavailable_items_are_still_listed():
    text = shop_text(data(status="nothing_to_buy", plan=None, unshoppable=[{"name": "Rarepeptide", "reason": "nope"}]), DAY)
    assert "Rarepeptide: nope" in text and "nothing on this protocol could be found" in text.lower()
