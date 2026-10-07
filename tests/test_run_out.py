"""'Runs out on...': when the stock of a peptide used by an active protocol will be gone."""

import html
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.inventory.runout import runs_out
from app.models import (
    ActiveVial, Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TitrationStep,
)

TODAY = date.today()


def make_stock(uid, vials=3, size=10.0, unit=DoseUnit.MG, name="Runout Test Item"):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=size, vial_size_unit=unit)
        s.add(item)
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=20), arrival_date=TODAY - timedelta(days=10))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=vials, received_quantity=vials))
        s.commit()
        return item.id


def make_protocol(uid, item_id, *, dose=1.0, unit=DoseUnit.MG, frequency=Frequency.DAILY, weekdays=None, every_n=None, paused=False, link=True, name="Runout Test Protocol"):
    with SessionLocal() as s:
        pep = Peptide(name=f"{name} Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name=name, start_date=TODAY - timedelta(days=5), end_date=TODAY + timedelta(days=200), owner_id=uid, paused=paused)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=dose, dose_unit=unit, frequency=frequency, weekdays=weekdays, every_n_days=every_n,
                           route=Route.SUBQ, inventory_item_id=item_id if link else None))
        s.commit()
        return p.id


@pytest.fixture(autouse=True)
def _clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(ActiveVial).delete()
        s.query(Protocol).filter(Protocol.name.like("Runout%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Runout%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Runout%")).delete(synchronize_session=False)
        s.commit()


def result(uid, item_id):
    with SessionLocal() as s:
        return runs_out(s, uid, TODAY).get(item_id)


def test_daily_dose_divides_the_stock(me):
    item = make_stock(me)                                   # 3 vials x 10 mg
    make_protocol(me, item)                                 # 1 mg a day
    found = result(me, item)
    assert found.days == 30 and found.date == TODAY + timedelta(days=30)


@pytest.mark.parametrize("kwargs,days", [({"frequency": Frequency.EOD}, 60), ({"frequency": Frequency.WEEKLY}, 210), ({"dose": 500.0, "unit": DoseUnit.MCG}, 60),
                                         ({"frequency": Frequency.WEEKDAYS, "weekdays": "MWF"}, 70), ({"frequency": Frequency.EVERY_N_DAYS, "every_n": 3}, 90)])
def test_the_schedule_and_unit_are_taken_into_account(me, kwargs, days):
    item = make_stock(me)
    make_protocol(me, item, **kwargs)
    assert result(me, item).days == days


def test_an_open_vial_adds_what_is_left_in_it(me):
    item = make_stock(me)
    with SessionLocal() as s:
        s.add(ActiveVial(owner_id=me, inventory_item_id=item, concentration_mg_ml=5.0, water_ml=2.0, dose_value=1.0, dose_unit=DoseUnit.MG, doses_total=10,
                         date_mixed=TODAY, discard_by=TODAY + timedelta(days=28), volume_remaining_ml=1.0))
        s.commit()
    make_protocol(me, item)
    assert result(me, item).days == 35                      # 30 mg sealed + 5 mg in the open vial


def test_two_protocols_using_the_same_stock_add_their_use(me):
    item = make_stock(me)
    make_protocol(me, item, name="Runout Test A")
    make_protocol(me, item, name="Runout Test B")
    assert result(me, item).days == 15


def test_titration_uses_the_dose_of_the_current_step(me):
    item = make_stock(me)
    with SessionLocal() as s:
        pep = Peptide(name="Runout Step Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Runout Titrated", start_date=TODAY - timedelta(days=20), owner_id=me, titration_enabled=True)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=1.0, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item,
                           steps=[TitrationStep(start_week=1, end_week=2, dose=1.0), TitrationStep(start_week=3, end_week=None, dose=3.0)]))
        s.commit()
    assert result(me, item).days == 10                      # today is in week 3: 3 mg a day against 30 mg


@pytest.mark.parametrize("kwargs", [{"paused": True}, {"link": False}, {"frequency": Frequency.AS_NEEDED}])
def test_nothing_is_estimated_without_an_active_scheduled_linked_protocol(me, kwargs):
    item = make_stock(me)
    make_protocol(me, item, **kwargs)
    assert result(me, item) is None


def test_the_inventory_page_shows_when_each_item_runs_out(client, db, me):
    item = make_stock(me)
    make_protocol(me, item)
    page = html.unescape(client.get("/inventory").text)
    assert "<th>Runs out</th>" in page and (TODAY + timedelta(days=30)).strftime("%m/%d/%Y") in page and "30 days" in page


def test_the_dashboard_warns_when_stock_runs_out_soon_unless_an_order_is_on_the_way(client, db, me):
    item = make_stock(me, vials=1, size=5.0)                # 5 mg, 1 mg a day: 5 days
    make_protocol(me, item)
    alerts = html.unescape(client.get("/dashboard").text).split('id="alerts-heading"')[1].split("</section>")[0]
    assert "Runout Test Item" in alerts and "runs out in 5 days" in alerts
    with SessionLocal() as s:
        order = Order(order_date=TODAY)
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item, quantity=2))
        s.commit()
    assert "runs out in" not in html.unescape(client.get("/dashboard").text).split('id="alerts-heading"')[1].split("</section>")[0]


def test_a_long_supply_raises_no_dashboard_alert(client, db, me):
    item = make_stock(me, vials=10, size=10.0)
    make_protocol(me, item)
    assert "runs out in" not in html.unescape(client.get("/dashboard").text).split('id="alerts-heading"')[1].split("</section>")[0]
