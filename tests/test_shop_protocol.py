"""Shop this protocol: the plan built from a protocol's course totals and every vendor's current price list."""

import html
import math
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DoseUnit, Frequency, Peptide, PeptideSource, PriceList, PriceListItem, Protocol, ProtocolItem, Route, User, Warehouse,
)
from photo_helpers import other_client
from price_helpers import item, make_card, make_list, make_vendor

TODAY = date.today()


@pytest.fixture(autouse=True)
def _reset_shipping(client, me):
    def wipe():
        with SessionLocal() as s:
            user = s.get(User, me)
            user.shop_china_shipping_cents = user.shop_us_shipping_cents = None
            s.commit()
    wipe()
    yield
    wipe()


def card(name, **kw):
    with SessionLocal() as s:
        c = Peptide(name=name, source=PeptideSource.CUSTOM, **kw)
        s.add(c)
        s.commit()
        return c.id


def protocol(uid, items, *, days=40, ended=False):
    """items: (peptide_id, dose, unit, frequency) tuples."""
    with SessionLocal() as s:
        p = Protocol(name="Course", start_date=TODAY, end_date=TODAY + timedelta(days=days - 1), owner_id=uid)
        s.add(p)
        s.flush()
        for peptide_id, dose, unit, frequency in items:
            s.add(ProtocolItem(protocol_id=p.id, peptide_id=peptide_id, dose=dose, dose_unit=unit, frequency=frequency, route=Route.SUBQ))
        s.commit()
        return p.id


def lists(db):
    """Acme has both peptides; Zephyr and Borealis carry one each (cheaper, but needing two shipments)."""
    acme, zephyr, borealis = (make_vendor(db, n) for n in ("Acme Labs", "Zephyr Labs", "Borealis Labs"))
    zorvex, quillamine = make_card(db, "Zorvex"), make_card(db, "Quillamine")
    make_list(db, acme, TODAY, item("Zorvex", 10, 200, card=zorvex), item("Quillamine", 10, 200, card=quillamine))
    make_list(db, zephyr, TODAY, item("Zorvex", 10, 120, card=zorvex))
    make_list(db, borealis, TODAY, item("Quillamine", 10, 110, card=quillamine))
    return zorvex.id, quillamine.id


def two_peptide_protocol(db, me):
    zorvex, quillamine = lists(db)
    return protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY), (quillamine, 250, DoseUnit.MCG, Frequency.DAILY)])    # 10 mg of each


def shop(client, pid, **params):
    return client.get(f"/protocols/{pid}/shop", params=params)


def test_the_best_single_vendor_plan_is_returned_with_its_lines_and_shipping(client, db, me):
    pid = two_peptide_protocol(db, me)
    data = shop(client, pid).json()
    assert data["status"] == "ok" and data["shipping"]["china"] == 60 and data["shipping"]["us"] == 30
    plan = data["plan"]
    assert [s["vendor"] for s in plan["sources"]] == ["Acme Labs"] and plan["total"] == pytest.approx(460.0)
    source = plan["sources"][0]
    assert source["warehouse"] == "china" and source["shipping"] == 60 and source["items_total"] == pytest.approx(400.0)
    line = source["lines"][0]
    assert {"peptide", "size_label", "packs", "pack_size", "vials_needed", "per_vial", "cost", "leftover_vials", "pack_type"} <= set(line)
    assert line["packs"] == 1 and line["vials_needed"] == 2 and line["per_vial"] == pytest.approx(20.0) and line["leftover_vials"] == 8


def test_the_cheaper_two_vendor_split_is_shown_as_an_alternative(client, db, me):
    pid = two_peptide_protocol(db, me)
    alt = shop(client, pid).json()["alternatives"]
    split = next(a for a in alt if len(a["sources"]) == 2)
    assert split["total"] == pytest.approx(350.0) and split["vs_chosen"] == pytest.approx(-110.0)


def test_a_hard_to_find_item_allows_the_cheaper_two_vendor_plan(client, db, me):
    zorvex, quillamine = lists(db)
    testosterone = card("Testosterone Test")
    with SessionLocal() as s:
        for vendor_name, price in (("Zephyr Labs", 100), ("Acme Labs", 300)):             # Zephyr carries it cheaply; Acme dearly
            plist = s.scalar(select(PriceList).where(PriceList.vendor_name == vendor_name))
            s.add(PriceListItem(price_list_id=plist.id, code="TT", product_name="Testosterone Test", peptide_id=testosterone, vial_amount=10, vial_unit="mg",
                                pack_size=10, pack_price=price, pack_type="kit"))
        s.commit()
    pid = protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY), (quillamine, 250, DoseUnit.MCG, Frequency.DAILY),
                        (testosterone, 250, DoseUnit.MCG, Frequency.DAILY)])
    plan = shop(client, pid).json()["plan"]
    assert len(plan["sources"]) == 2 and sorted(s["vendor"] for s in plan["sources"]) == ["Borealis Labs", "Zephyr Labs"]      # 100 + 120 + 110 + 120 shipping = 450 beats Acme's 700 + 60
    assert plan["total"] == pytest.approx(450.0) and "hard to find" in plan["reason"].lower()


def test_editing_the_shipping_fees_changes_the_plan(client, db, me):
    pid = two_peptide_protocol(db, me)
    assert shop(client, pid, china="10", us="5").json()["plan"]["total"] == pytest.approx(410.0)
    cheap = shop(client, pid, china="0", us="0").json()
    assert cheap["plan"]["total"] == pytest.approx(400.0) and cheap["shipping"]["china"] == 0


def test_saved_defaults_prefill_the_fees_and_can_be_saved_from_the_dialog(client, db, me):
    pid = two_peptide_protocol(db, me)
    r = client.post("/protocols/shop/defaults", data={"china": "75.50", "us": "20"})
    assert r.status_code == 200 and r.json() == {"ok": True, "china": 75.5, "us": 20.0}
    data = shop(client, pid).json()
    assert data["shipping"]["china"] == 75.5 and data["shipping"]["us"] == 20 and data["plan"]["total"] == pytest.approx(475.5)
    assert shop(client, pid, china="1").json()["shipping"]["china"] == 1                       # an edit overrides the saved value for this look only


@pytest.mark.parametrize("data", [{"china": "-1", "us": "30"}, {"china": "x", "us": "30"}, {"china": "60", "us": ""}, {"china": "99999", "us": "30"}])
def test_bad_shipping_values_are_refused(client, db, me, data):
    assert client.post("/protocols/shop/defaults", data=data).status_code == 422


def test_a_protocol_without_an_end_date_cannot_be_shopped(client, db, me):
    zorvex, quillamine = lists(db)
    with SessionLocal() as s:
        p = Protocol(name="Open", start_date=TODAY, owner_id=me)
        s.add(p)
        s.commit()
        pid = p.id
    assert shop(client, pid).json()["status"] == "no_end_date"


def test_someone_elses_protocol_is_not_found(client, db, me):
    lists(db)
    with other_client("shopother") as member:
        with SessionLocal() as s:
            other = s.scalar(select(User.id).where(User.username_key == "shopother"))
        pid = protocol(other, [(card("Other peptide"), 250, DoseUnit.MCG, Frequency.DAILY)])
        assert shop(client, pid).status_code == 404
        assert member.get(f"/protocols/{pid}/shop").status_code == 200
        with SessionLocal() as s:
            s.query(Protocol).filter_by(owner_id=other).delete()
            s.commit()


def test_a_peptide_no_vendor_sells_is_listed_as_not_available(client, db, me):
    zorvex, quillamine = lists(db)
    rare = card("Rarepeptide")
    pid = protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY), (rare, 250, DoseUnit.MCG, Frequency.DAILY)])
    data = shop(client, pid).json()
    assert [u["name"] for u in data["unshoppable"]] == ["Rarepeptide"] and data["plan"] is not None


def test_bac_water_is_listed_as_a_separate_purchase_with_bottles(client, db, me):
    zorvex, quillamine = lists(db)
    with SessionLocal() as s:
        for pid_ in (zorvex, quillamine):
            c = s.get(Peptide, pid_)
            c.normally_supplied_amount, c.normally_supplied_unit = 10, DoseUnit.MG
        s.commit()
    pid = protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY), (quillamine, 250, DoseUnit.MCG, Frequency.DAILY)])
    bac = shop(client, pid).json()["bac"]
    assert bac["ml"] > 0 and bac["bottles"] == math.ceil(bac["ml"] / 30)


def test_an_iu_product_is_priced_in_iu_converting_a_mass_dose(client, db, me):
    vendor = make_vendor(db, "Acme Labs")
    hgh = make_card(db, "HGH Test")
    make_list(db, vendor, TODAY, item("HGH Test", 10, 300, unit="IU", card=hgh), warehouse=Warehouse.US)
    pid = protocol(me, [(hgh.id, 500, DoseUnit.MCG, Frequency.DAILY)], days=10)         # 5 mg = 15 IU at 3 IU per mg
    plan = shop(client, pid).json()["plan"]
    line = plan["sources"][0]["lines"][0]
    assert line["vials_needed"] == 2 and line["packs"] == 1 and plan["sources"][0]["shipping"] == 30


def test_the_protocols_page_has_the_shop_button_and_dialog(client, db, me):
    two_peptide_protocol(db, me)
    page = html.unescape(client.get("/protocols").text)
    assert 'class="btn-icon shop-btn"' in page and 'id="shop-dialog"' in page and "Shop this protocol" in page


def test_settings_has_a_shopping_section_with_the_defaults_and_saves_them(client, db, me):
    page = client.get("/settings").text
    assert 'name="china_shipping"' in page and 'value="60"' in page and 'name="us_shipping"' in page and 'value="30"' in page
    assert client.post("/settings/shopping", data={"china_shipping": "55", "us_shipping": "25"}, follow_redirects=False).status_code == 303
    page = client.get("/settings").text
    assert 'value="55"' in page and 'value="25"' in page
    assert client.post("/settings/shopping", data={"china_shipping": "-5", "us_shipping": "25"}).status_code == 422


# ---------------------------------------------------------------- as-needed items

def as_needed_protocol(me, peptide_id, dose, unit=DoseUnit.MG):
    return protocol(me, [(peptide_id, dose, unit, Frequency.AS_NEEDED)])


def sized_list(db, *sizes):
    vendor = make_vendor(db, "Acme Labs")
    c = make_card(db, "Zorvex")
    make_list(db, vendor, TODAY, *(item("Zorvex", size, price, card=c) for size, price in sizes))
    return c.id


@pytest.mark.parametrize("dose,unit,expected_size", [(250, DoseUnit.MCG, "5 mg"), (5, DoseUnit.MG, "5 mg"), (8, DoseUnit.MG, "10 mg")])
def test_an_as_needed_item_buys_one_5_or_10_mg_vial_chosen_by_the_dose(client, db, me, dose, unit, expected_size):
    peptide = sized_list(db, (5, 100), (10, 150), (2, 20), (30, 400))
    line = shop(client, as_needed_protocol(me, peptide, dose, unit)).json()["plan"]["sources"][0]["lines"][0]
    assert line["size_label"] == expected_size and line["vials_needed"] == 1 and line["packs"] == 1


def test_a_dose_above_10_mg_needs_a_vial_that_holds_it(client, db, me):
    peptide = sized_list(db, (5, 100), (10, 150), (15, 180), (30, 400))
    line = shop(client, as_needed_protocol(me, peptide, 12)).json()["plan"]["sources"][0]["lines"][0]
    assert line["size_label"] == "15 mg"


def test_an_as_needed_item_with_no_dose_set_allows_5_to_10_mg(client, db, me):
    peptide = sized_list(db, (5, 100), (10, 150))
    line = shop(client, as_needed_protocol(me, peptide, None)).json()["plan"]["sources"][0]["lines"][0]
    assert line["size_label"] == "5 mg"


def test_as_needed_items_never_ask_for_a_vial_size(client, db, me):
    peptide = sized_list(db, (5, 100))
    data = shop(client, as_needed_protocol(me, peptide, 250, DoseUnit.MCG)).json()
    assert data["unshoppable"] == [] and data["plan"] is not None


# ---------------------------------------------------------------- the text version (file / email)

def test_the_plan_is_available_as_plain_text(client, db, me):
    pid = two_peptide_protocol(db, me)
    r = client.get(f"/protocols/{pid}/shop.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "Shopping plan: Course" in r.text and "Acme Labs" in r.text and "Grand total: $460.00" in r.text
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_the_text_can_be_downloaded_as_a_file(client, db, me):
    pid = two_peptide_protocol(db, me)
    r = client.get(f"/protocols/{pid}/shop.txt", params={"download": "1"})
    assert 'attachment; filename="shopping-plan-course.txt"' == r.headers["content-disposition"]


def test_the_text_uses_the_edited_shipping_fees(client, db, me):
    pid = two_peptide_protocol(db, me)
    text = client.get(f"/protocols/{pid}/shop.txt", params={"china": "10", "us": "5"}).text
    assert "China $10.00, US $5.00" in text and "Grand total: $410.00" in text


def test_the_text_refuses_a_bad_fee_and_someone_elses_protocol(client, db, me):
    pid = two_peptide_protocol(db, me)
    assert client.get(f"/protocols/{pid}/shop.txt", params={"china": "x"}).status_code == 422
    assert client.get("/protocols/99999999/shop.txt").status_code == 404


def test_the_shop_dialog_has_the_share_buttons(client, db, me):
    two_peptide_protocol(db, me)
    page = client.get("/protocols").text
    assert 'id="shop-download"' in page and 'id="shop-email"' in page


def test_a_zero_price_line_is_never_offered(client, db, me):
    zorvex, quillamine = lists(db)
    stock = make_vendor(db, "Stockroom Labs")
    make_list(db, stock, TODAY, item("Zorvex", 10, 0, card=make_card(db, "Zorvex Two")), item("Zorvex", 10, 0))
    with SessionLocal() as s:
        plist = s.scalar(select(PriceList).where(PriceList.vendor_name == "Stockroom Labs"))
        for row in plist.items:
            row.peptide_id = zorvex
        s.commit()
    pid = protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY)])
    sources = shop(client, pid).json()["plan"]["sources"]
    assert [s["vendor"] for s in sources] == ["Zephyr Labs"]


# ---------------------------------------------------------------- BAC water from the price lists

def bac_world(db, me, *, ranked=True):
    zorvex, quillamine = lists(db)
    with SessionLocal() as s:
        for pid_ in (zorvex, quillamine):
            c = s.get(Peptide, pid_)
            c.normally_supplied_amount, c.normally_supplied_unit = 10, DoseUnit.MG
        s.commit()
    sterile = make_vendor(db, "Sterile Supply Co")
    make_list(db, sterile, TODAY, item("Acme Hospira Bacteriostatic Water", 30, 18, unit="ml", pack=1, code=None),
              item("Acme Hospira Bacteriostatic Water", 30, 435, unit="ml", pack=25, code=None),
              item("Acme Hospira Bacteriostatic Sodium Chloride", 30, 11, unit="ml", pack=1, code=None), warehouse=Warehouse.US)
    if ranked:
        from supply_helpers import make_bac
        make_bac(me, "Acme Hospira BAC Water", priority=1, bottles=0)
    return protocol(me, [(zorvex, 250, DoseUnit.MCG, Frequency.DAILY), (quillamine, 250, DoseUnit.MCG, Frequency.DAILY)])


def test_a_ranked_brand_of_bac_water_on_a_price_list_is_planned_as_its_own_order(client, db, me):
    pid = bac_world(db, me)
    data = shop(client, pid).json()
    buy = data["bac"]["buy"]
    assert buy["vendor"] == "Sterile Supply Co" and buy["product"] == "Acme Hospira Bacteriostatic Water" and buy["mode"] == "separate"
    assert (buy["size_label"], buy["packs"], buy["cost"], buy["shipping"], buy["extra"]) == ("30 mL", 1, 18.0, 30.0, 48.0)       # the case is dearer; the sodium chloride is not water
    assert data["grand_total"] == pytest.approx(data["plan"]["total"] + 48.0)


def test_without_a_ranked_brand_no_bac_water_is_offered_from_the_lists(client, db, me):
    pid = bac_world(db, me, ranked=False)
    data = shop(client, pid).json()
    assert data["bac"]["buy"] is None and data["bac"]["bottles"] >= 1
    assert data["grand_total"] == pytest.approx(data["plan"]["total"])


def test_the_plain_text_includes_the_bac_water_and_the_grand_total_with_it(client, db, me):
    pid = bac_world(db, me)
    text = client.get(f"/protocols/{pid}/shop.txt").text
    assert "BAC water" in text and "Sterile Supply Co" in text and "Acme Hospira Bacteriostatic Water" in text and "Grand total: $508.00" in text
