"""Switching a protocol line between two forms of one medicine (sublingual drops to an injectable vial, say): the Switch button shows only when the
medicine is stocked in two or more mediums, history stays, and later doses come out of the new form."""

from datetime import date, timedelta

import pytest

from photo_helpers import other_client

from app.db import SessionLocal
from app.models import (ActiveVial, Category, DoseLog, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol,
                        ProtocolItem, Route, TimeOfDay, TitrationStep)

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(ActiveVial).delete()
        s.query(DoseLog).delete()
        s.query(Protocol).filter(Protocol.name.like("Switchmed%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Switchmed%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Switchmed%")).delete(synchronize_session=False)
        s.commit()


def stock(uid, medium, *, name="Switchmed B12", units=60, size=5000.0, unit=DoseUnit.MCG, active=False):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=0, medium=medium, vial_size_mg=size, vial_size_unit=unit, is_active=active)
        s.add(item)
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=20), arrival_date=TODAY - timedelta(days=10))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=units, received_quantity=units))
        s.commit()
        return item.id


def protocol(uid, item_id, *, start=None, end=None):
    with SessionLocal() as s:
        pep = s.query(Peptide).filter(Peptide.name == "Switchmed B12").first()
        if pep is None:
            pep = Peptide(name="Switchmed B12", source=PeptideSource.CUSTOM)
            s.add(pep)
            s.flush()
        p = Protocol(name="Switchmed Protocol", start_date=start or TODAY - timedelta(days=5), end_date=end, owner_id=uid)
        s.add(p)
        s.flush()
        line = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=5000.0, dose_unit=DoseUnit.MCG, frequency=Frequency.DAILY, time_of_day=TimeOfDay.AM,
                            route=Route.ORAL, inventory_item_id=item_id)
        s.add(line)
        s.commit()
        return p.id, line.id


def switch(client, pid, line, item_id, **extra):
    data = {"inventory_item_id": str(item_id), "route": "subq", "dose": "1000", "dose_unit": "mcg", **extra}
    return client.post(f"/protocols/{pid}/items/{line}/switch", data=data, follow_redirects=False)


def log(client, pid, line):
    return client.post("/today/log", data={"protocol_id": str(pid), "protocol_item_id": str(line), "scheduled_date": TODAY.isoformat()}, follow_redirects=False)


def left(*ids):
    with SessionLocal() as s:
        return tuple(s.get(InventoryItem, i).available_count for i in ids)


def active_section(client):
    return client.get("/inventory").text.split('id="active-vials"')[1].split("</section>")[0]


def test_no_switch_button_when_the_medicine_has_only_one_form(client, me):
    drops = stock(me, Medium.DROPS, active=True)
    protocol(me, drops)
    assert "switch-form" not in client.get("/protocols").text
    assert "switch-form" not in active_section(client)


def test_a_second_item_of_the_same_form_is_not_a_second_form(client, me):
    drops = stock(me, Medium.DROPS)
    stock(me, Medium.DROPS, size=1000.0)
    protocol(me, drops)
    assert "to Drops" not in client.get("/protocols").text


def test_a_differently_named_medicine_is_not_another_form(client, me):
    drops = stock(me, Medium.DROPS)
    stock(me, Medium.LYOPHILIZED, name="Switchmed Other", size=10.0, unit=DoseUnit.MG)
    protocol(me, drops)
    assert "to Lyophilized" not in client.get("/protocols").text


def test_the_button_shows_on_the_protocol_when_two_forms_are_stocked_and_names_the_other_one(client, me):
    drops = stock(me, Medium.DROPS)
    stock(me, Medium.LYOPHILIZED, size=10.0, unit=DoseUnit.MG)
    protocol(me, drops)
    page = client.get("/protocols").text
    assert "to Lyophilized" in page and "to Drops" not in page


def test_switching_moves_later_doses_to_the_new_form_and_keeps_the_history(client, me):
    drops = stock(me, Medium.DROPS)
    pills = stock(me, Medium.PILL, units=30)
    pid, line = protocol(me, drops)
    assert log(client, pid, line).status_code == 303
    assert left(drops, pills) == (59, 30)
    assert switch(client, pid, line, pills, route="oral", dose="5000").status_code == 303
    with SessionLocal() as s:
        row = s.get(ProtocolItem, line)
        assert row.inventory_item_id == pills and row.route is Route.ORAL
        assert s.query(DoseLog).count() == 1 and s.query(DoseLog).one().protocol_item_id == line     # the logged dose stays on the line
    assert left(drops, pills) == (59, 30)                                                              # the switch moves no stock
    with SessionLocal() as s:
        log_id = s.query(DoseLog).one().id
    client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False)              # undoing the earlier dose gives the unit back to the drops
    assert left(drops, pills) == (60, 30)
    assert log(client, pid, line).status_code == 303
    assert left(drops, pills) == (60, 29)


def test_switching_sets_the_route_and_dose_and_the_active_items(client, me):
    drops = stock(me, Medium.DROPS, active=True)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops)
    assert switch(client, pid, line, pills, route="oral", dose="2.5", dose_unit="mg").status_code == 303
    with SessionLocal() as s:
        row = s.get(ProtocolItem, line)
        assert (row.dose, row.dose_unit, row.route) == (2.5, DoseUnit.MG, Route.ORAL)
        assert s.get(InventoryItem, pills).is_active is True and s.get(InventoryItem, drops).is_active is False


def test_the_old_form_stays_active_while_another_line_still_uses_it(client, me):
    drops = stock(me, Medium.DROPS, active=True)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops)
    protocol(me, drops)
    switch(client, pid, line, pills, route="oral")
    with SessionLocal() as s:
        assert s.get(InventoryItem, drops).is_active is True


def test_a_titrated_line_keeps_its_dose(client, me):
    drops = stock(me, Medium.DROPS)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops)
    with SessionLocal() as s:
        s.add(TitrationStep(protocol_item_id=line, start_week=1, end_week=2, dose=3000.0))
        s.commit()
    assert switch(client, pid, line, pills, route="oral", dose="9999").status_code == 303
    with SessionLocal() as s:
        assert s.get(ProtocolItem, line).dose == 5000.0 and s.get(ProtocolItem, line).inventory_item_id == pills


def test_the_switch_is_refused_for_the_same_form_another_medicine_or_a_bad_dose(client, me):
    drops = stock(me, Medium.DROPS)
    other_drops = stock(me, Medium.DROPS, size=1000.0)
    pills = stock(me, Medium.PILL)
    stranger = stock(me, Medium.PILL, name="Switchmed Other")
    pid, line = protocol(me, drops)
    assert switch(client, pid, line, other_drops).status_code == 422
    assert switch(client, pid, line, drops).status_code == 422
    assert switch(client, pid, line, stranger).status_code == 422
    assert switch(client, pid, line, pills, dose="0").status_code == 422
    assert switch(client, pid, line, pills, dose="lots").status_code == 422
    assert switch(client, pid, line, pills, route="bogus").status_code == 422
    with SessionLocal() as s:
        assert s.get(ProtocolItem, line).inventory_item_id == drops


def test_an_ended_protocol_cannot_be_switched(client, me):
    drops = stock(me, Medium.DROPS)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops, start=TODAY - timedelta(days=20), end=TODAY - timedelta(days=2))
    assert switch(client, pid, line, pills).status_code == 404


def test_a_line_that_is_not_in_that_protocol_is_not_found(client, me):
    drops = stock(me, Medium.DROPS)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops)
    assert switch(client, pid, line + 999, pills).status_code == 404


def test_the_button_is_on_the_active_card_of_a_drops_item_and_of_an_open_vial(client, me):
    drops = stock(me, Medium.DROPS, active=True)
    powder = stock(me, Medium.LYOPHILIZED, size=10.0, unit=DoseUnit.MG)
    pid, line = protocol(me, drops)
    assert "Switchmed Protocol to Lyophilized" in active_section(client)
    switch(client, pid, line, powder, route="subq", dose="1", dose_unit="mg")
    with SessionLocal() as s:
        s.add(ActiveVial(owner_id=me, inventory_item_id=powder, concentration_mg_ml=5.0, water_ml=2.0, dose_value=1.0, dose_unit=DoseUnit.MG, doses_total=5,
                         date_mixed=TODAY, discard_by=TODAY + timedelta(days=28), volume_remaining_ml=2.0))
        s.commit()
    assert "Switchmed Protocol to Drops" in active_section(client)                                      # on the open vial's card, back the other way
    back = client.post(f"/protocols/{pid}/items/{line}/switch", data={"inventory_item_id": str(drops), "route": "oral", "dose": "5000", "dose_unit": "mcg",
                                                                      "next": "/inventory#active-vials"}, follow_redirects=False)
    assert back.headers["location"] == "/inventory#active-vials"


def test_another_persons_protocol_cannot_be_switched(client, me):
    drops = stock(me, Medium.DROPS)
    pills = stock(me, Medium.PILL)
    pid, line = protocol(me, drops)
    with other_client("switchother") as member:
        assert switch(member, pid, line, pills).status_code == 404
    with SessionLocal() as s:
        assert s.get(ProtocolItem, line).inventory_item_id == drops
