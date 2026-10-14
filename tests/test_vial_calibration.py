"""A one-time prompt for vials that were open before Amide tracked how much is in them, and the periodic-table style tile on active vials."""

import html
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.inventory import calibrate
from app.inventory.symbol import symbol
from app.models import ActiveVial, Category, InventoryItem, Order, OrderItem, Protocol, Peptide, User
from photo_helpers import other_client
from test_run_out import make_stock

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(ActiveVial).delete()
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Runout%")).delete(synchronize_session=False)
        s.query(InventoryItem).filter(InventoryItem.name.like("Retatrutide%")).delete(synchronize_session=False)
        s.query(InventoryItem).filter(InventoryItem.name.like("Hospira%")).delete(synchronize_session=False)
        s.commit()


def vial(uid, item_id, *, water=2.0, left=2.0, conc=5.0, confirmed=False, prompted=None, dose=None):
    with SessionLocal() as s:
        v = ActiveVial(owner_id=uid, inventory_item_id=item_id, concentration_mg_ml=conc, water_ml=water, volume_remaining_ml=left, date_mixed=TODAY,
                       discard_by=TODAY + timedelta(days=28), doses_total=10, confirmed=confirmed, prompted_at=prompted)
        s.add(v)
        s.commit()
        return v.id


def bac(uid, name="Hospira BAC Water 30 mL"):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.BAC_WATER, owner_id=uid, count=0)
        s.add(item)
        s.commit()
        return item.id


def get(vid):
    with SessionLocal() as s:
        return s.get(ActiveVial, vid)


def answers(vid, **kw):
    base = {f"water_ml[{vid}]": "2", f"doses_taken[{vid}]": "0", f"dose_amount[{vid}]": "", f"dose_unit[{vid}]": "mg", f"ml_left[{vid}]": ""}
    base.update({f"{k}[{vid}]": str(v) for k, v in kw.items()})
    return {"vial_ids": [str(vid)], **base}


# ---- the tile symbol ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Retatrutide", "Rt"), ("BPC-157", "Bp"), ("Tirzepatide", "Tr"), ("Semaglutide", "Sm"), ("Ipamorelin", "Ip"), ("TB-500", "Tb"),
    ("MOTS-c", "Mt"), ("NAD+", "Nd"), ("CJC-1295", "Cj"), ("5-Amino-1MQ", "Am"), ("Epithalon", "Ep"), ("X", "X"), ("", "?"), ("  ", "?"), ("123", "?"),
])
def test_the_symbol_is_the_first_letter_and_the_next_consonant(name, expected):
    assert symbol(name) == expected


# ---- the arithmetic -------------------------------------------------------------------------------------------------------------------

class V:                                     # the parts of a vial and its item that the arithmetic reads
    def __init__(self, size=10.0, unit="mg", water=2.0, left=2.0, bac=False):
        self.vial_size, self.vial_unit, self.water_ml, self.volume_remaining_ml, self.bac, self.iu_per_mg = size, unit, water, left, bac, None


def test_doses_taken_come_out_of_the_mix_at_its_concentration():
    out = calibrate.remaining_after(size=10.0, vial_unit="mg", water_ml=2.0, doses_taken=1, dose_amount=1.0, dose_unit="mg", iu_per_mg=None)
    assert out.concentration == 5.0 and out.volume_remaining_ml == pytest.approx(1.8)


def test_a_dose_in_mcg_is_converted_to_the_vials_unit():
    out = calibrate.remaining_after(size=10.0, vial_unit="mg", water_ml=2.0, doses_taken=4, dose_amount=250.0, dose_unit="mcg", iu_per_mg=None)
    assert out.volume_remaining_ml == pytest.approx(1.8)                     # 4 x 0.25 mg = 1 mg


def test_taking_more_than_the_vial_held_leaves_it_empty_rather_than_negative():
    out = calibrate.remaining_after(size=10.0, vial_unit="mg", water_ml=2.0, doses_taken=20, dose_amount=1.0, dose_unit="mg", iu_per_mg=None)
    assert out.volume_remaining_ml == 0.0


def test_a_measured_volume_overrides_the_dose_count():
    out = calibrate.remaining_after(size=10.0, vial_unit="mg", water_ml=2.0, doses_taken=3, dose_amount=1.0, dose_unit="mg", iu_per_mg=None, ml_left=1.5)
    assert out.volume_remaining_ml == 1.5


# ---- who gets asked, and when ---------------------------------------------------------------------------------------------------------

def test_pending_vials_are_my_open_unconfirmed_unprompted_ones(client, db, me):
    item = make_stock(me, vials=3)
    mine = vial(me, item)
    vial(me, item, confirmed=True)
    vial(me, item, prompted=datetime(2026, 10, 1))
    gone = vial(me, item)
    with SessionLocal() as s:
        s.get(ActiveVial, gone).discarded_at = datetime(2026, 10, 2)
        s.commit()
    with SessionLocal() as s:
        assert [v.id for v in calibrate.pending(s, me)] == [mine]


def test_the_prompt_opens_on_the_dashboard_until_it_is_answered_or_skipped(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item)
    page = html.unescape(client.get("/dashboard").text)
    assert 'id="calibrate-dialog"' in page and "data-open-on-load" in page.split('id="calibrate-dialog"', 1)[1][:200]
    r = client.post("/active-vials/calibrate/skip", data={"next": "/dashboard"}, follow_redirects=False)
    assert r.status_code == 303 and get(vid).prompted_at is not None and get(vid).confirmed is False
    assert 'id="calibrate-dialog"' not in client.get("/dashboard").text


def test_there_is_no_prompt_when_nothing_needs_asking(client, db, me):
    item = make_stock(me, vials=3)
    vial(me, item, confirmed=True)
    assert 'id="calibrate-dialog"' not in client.get("/dashboard").text


# ---- answering ------------------------------------------------------------------------------------------------------------------------

def test_answering_sets_the_mix_and_what_is_left_and_marks_it_confirmed(client, db, me):
    item = make_stock(me, vials=3, size=10.0)
    vid = vial(me, item, water=3.0, left=3.0, conc=3.0)
    r = client.post("/active-vials/calibrate", data={**answers(vid, water_ml=2, doses_taken=1, dose_amount=1), "next": "/inventory"}, follow_redirects=False)
    assert r.status_code == 303
    v = get(vid)
    assert v.water_ml == 2.0 and v.concentration_mg_ml == pytest.approx(5.0) and v.volume_remaining_ml == pytest.approx(1.8)
    assert v.confirmed is True and v.prompted_at is not None


def test_a_bac_bottle_is_asked_only_how_many_ml_remain(client, db, me):
    item = bac(me)
    with SessionLocal() as s:
        v = ActiveVial(owner_id=me, inventory_item_id=item, water_ml=30.0, volume_remaining_ml=30.0, date_mixed=TODAY, discard_by=TODAY + timedelta(days=28))
        s.add(v)
        s.commit()
        vid = v.id
    page = html.unescape(client.get("/dashboard").text).split('id="calibrate-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert "How many mL are left" in page and "doses taken" not in page.split("Hospira", 1)[1]
    r = client.post("/active-vials/calibrate", data={"vial_ids": [str(vid)], f"ml_left[{vid}]": "18.5", "next": "/inventory"}, follow_redirects=False)
    assert r.status_code == 303 and get(vid).volume_remaining_ml == 18.5 and get(vid).confirmed is True


@pytest.mark.parametrize("bad", [{"water_ml": 0}, {"water_ml": "x"}, {"doses_taken": -1}, {"doses_taken": 2, "dose_amount": ""}, {"ml_left": 5}, {"ml_left": "x"}])
def test_bad_answers_change_nothing_and_say_why(client, db, me, bad):
    item = make_stock(me, vials=3, size=10.0)
    vid = vial(me, item)
    r = client.post("/active-vials/calibrate", data={**answers(vid, **bad), "next": "/inventory"}, follow_redirects=False)
    assert r.status_code == 422
    v = get(vid)
    assert v.confirmed is False and v.prompted_at is None and v.volume_remaining_ml == 2.0


def test_someone_elses_vial_cannot_be_answered(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item)
    with other_client("calibother") as stranger:
        r = stranger.post("/active-vials/calibrate", data={**answers(vid, water_ml=9), "next": "/inventory"}, follow_redirects=False)
        assert r.status_code in (303, 404, 422)
    assert get(vid).water_ml == 2.0 and get(vid).confirmed is False


def test_a_vial_made_by_reconstituting_is_confirmed_from_the_start(client, db, me):
    from supply_helpers import stock_everything
    stock_everything(me)
    item = make_stock(me, vials=3, size=10.0)
    r = client.post("/calculator/reconstitute", data={"inventory_item_id": str(item), "water_ml": "2", "dose_value": "1", "dose_unit": "mg",
                                                     "discard_by": str(TODAY + timedelta(days=28))}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        v = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == item))
        assert v.confirmed is True
        assert calibrate.pending(s, me) == []


# ---- the card -------------------------------------------------------------------------------------------------------------------------

def card(client, vid):
    return html.unescape(client.get("/inventory").text).split(f'data-vial-id="{vid}"', 1)[1].split("</article>", 1)[0]


def test_an_unconfirmed_vial_is_marked_estimated_and_offers_an_update(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item)
    c = card(client, vid)
    assert "estimated" in c and f"calibrate={vid}" in c


def test_a_confirmed_vial_is_not_marked_estimated(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item, confirmed=True)
    assert "estimated" not in card(client, vid)


def test_the_update_link_opens_the_prompt_for_that_vial(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item, prompted=datetime(2026, 10, 1))                  # already asked once, so the login prompt stays quiet
    assert 'id="calibrate-dialog"' not in client.get("/inventory").text
    page = html.unescape(client.get(f"/inventory?calibrate={vid}").text)
    assert 'id="calibrate-dialog"' in page and f'name="water_ml[{vid}]"' in page


def test_the_card_carries_the_periodic_table_tile(client, db, me):
    with SessionLocal() as s:
        item = InventoryItem(name="Retatrutide", category=Category.MEDICINE, owner_id=me, count=0, vial_size_mg=10, vial_size_unit="mg")
        s.add(item)
        s.commit()
        item_id = item.id
    vid = vial(me, item_id, confirmed=True)
    c = card(client, vid)
    assert 'class="element-tile"' in c and '<span class="el-symbol">Rt</span>' in c and '<span class="el-amount">10 mg</span>' in c


def test_bac_water_gets_no_tile(client, db, me):
    item = bac(me)
    with SessionLocal() as s:
        v = ActiveVial(owner_id=me, inventory_item_id=item, water_ml=30.0, volume_remaining_ml=30.0, date_mixed=TODAY, discard_by=TODAY + timedelta(days=28), confirmed=True)
        s.add(v)
        s.commit()
        vid = v.id
    assert "element-tile" not in card(client, vid)


def test_the_printed_label_of_an_open_vial_has_the_tile_and_a_sealed_vials_does_not(client, db, me):
    with SessionLocal() as s:
        item = InventoryItem(name="Retatrutide", category=Category.MEDICINE, owner_id=me, count=0, vial_size_mg=10, vial_size_unit="mg")
        s.add(item)
        s.commit()
        item_id = item.id
    vid = vial(me, item_id, confirmed=True)
    page = html.unescape(client.get(f"/active-vials/{vid}/label").text)
    assert 'class="element-tile"' in page and '<span class="el-symbol">Rt</span>' in page


def test_the_tile_is_lined_up_with_the_concentration_line_by_a_script(client, db, me):
    item = make_stock(me, vials=3)
    vid = vial(me, item, confirmed=True)
    page = html.unescape(client.get('/inventory').text)
    assert 'class="vial-conc"' in card(client, vid) and 'js/vial-tile.js' in page
