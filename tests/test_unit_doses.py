"""Items that are not mixed from powder (drops, pills, autoinjectors, liquids) use up their stock a dose at a time: each logged dose takes its share of one
unit, undo gives it back, and a skip takes nothing."""

from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.inventory.runout import runs_out
from app.models import (Category, DoseLog, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay)

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(DoseLog).delete()
        s.query(Protocol).filter(Protocol.name.like("Unitdose%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Unitdose%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Unitdose%")).delete(synchronize_session=False)
        s.commit()


def make(uid, *, medium=Medium.DROPS, units=60, size=5000.0, size_unit=DoseUnit.MCG, dose=5000.0, dose_unit=DoseUnit.MCG, link=True, frequency=Frequency.DAILY):
    with SessionLocal() as s:
        item = InventoryItem(name="Unitdose B12", category=Category.MEDICINE, owner_id=uid, count=0, medium=medium, vial_size_mg=size, vial_size_unit=size_unit)
        s.add(item)
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=20), arrival_date=TODAY - timedelta(days=10))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=units, received_quantity=units, lot_number="L1"))
        pep = s.query(Peptide).filter(Peptide.name == "Unitdose Peptide").first()
        if pep is None:
            pep = Peptide(name="Unitdose Peptide", source=PeptideSource.CUSTOM)
            s.add(pep)
            s.flush()
        p = Protocol(name="Unitdose Protocol", start_date=TODAY - timedelta(days=5), owner_id=uid)
        s.add(p)
        s.flush()
        pi = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=dose, dose_unit=dose_unit, frequency=frequency, route=Route.ORAL,
                          inventory_item_id=item.id if link else None)
        s.add(pi)
        s.commit()
        return item.id, p.id, pi.id


def log(client, protocol_id, pitem_id, day=None):
    return client.post("/today/log", data={"protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id), "scheduled_date": (day or TODAY).isoformat()}, follow_redirects=False)


def item(item_id):
    s = SessionLocal()
    obj = s.get(InventoryItem, item_id)
    obj.available_count, obj.units_left, obj.dosed_units                         # load while the session is open
    return obj


def test_a_logged_dose_takes_one_unit_when_the_dose_is_the_units_amount(client, me):
    item_id, pid, piid = make(me)
    assert item(item_id).available_count == 60
    assert log(client, pid, piid).status_code == 303
    after = item(item_id)
    assert after.available_count == 59 and after.dosed_units == pytest.approx(1.0)
    with SessionLocal() as s:
        assert s.query(DoseLog).one().inventory_units == pytest.approx(1.0)


def test_a_part_dose_takes_a_part_of_a_unit(client, me):
    item_id, pid, piid = make(me, dose=2500.0)
    log(client, pid, piid)
    after = item(item_id)
    assert after.dosed_units == pytest.approx(0.5) and after.units_left == pytest.approx(59.5) and after.available_count == 59


def test_the_dose_is_converted_to_the_units_own_unit(client, me):
    item_id, pid, piid = make(me, dose=5.0, dose_unit=DoseUnit.MG)               # 5 mg against a 5000 mcg unit
    log(client, pid, piid)
    assert item(item_id).dosed_units == pytest.approx(1.0)


def test_undo_gives_the_unit_back(client, me):
    item_id, pid, piid = make(me)
    log(client, pid, piid)
    with SessionLocal() as s:
        log_id = s.query(DoseLog).one().id
    assert client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False).status_code == 303
    after = item(item_id)
    assert after.available_count == 60 and after.dosed_units == pytest.approx(0.0)


def test_a_skip_takes_nothing(client, me):
    item_id, pid, piid = make(me)
    client.post("/today/skip", data={"protocol_id": str(pid), "protocol_item_id": str(piid), "scheduled_date": TODAY.isoformat()}, follow_redirects=False)
    assert item(item_id).available_count == 60 and item(item_id).dosed_units == 0


def test_a_powder_vial_item_is_left_to_the_vial_gauge(client, me):
    item_id, pid, piid = make(me, medium=Medium.LYOPHILIZED, size=10.0, size_unit=DoseUnit.MG, dose=1.0, dose_unit=DoseUnit.MG)
    log(client, pid, piid)
    assert item(item_id).dosed_units == 0 and item(item_id).available_count == 60


def test_an_item_the_protocol_does_not_link_to_is_untouched(client, me):
    item_id, pid, piid = make(me, link=False)
    log(client, pid, piid)
    assert item(item_id).available_count == 60


def test_the_last_units_are_never_taken_below_zero(client, me):
    item_id, pid, piid = make(me, units=1, dose=5000.0)
    log(client, pid, piid, TODAY - timedelta(days=1))
    log(client, pid, piid)
    after = item(item_id)
    assert after.available_count == 0 and after.units_left == pytest.approx(0.0) and after.dosed_units == pytest.approx(1.0)
    with SessionLocal() as s:
        assert sorted(round(l.inventory_units, 3) for l in s.query(DoseLog)) == [0.0, 1.0]


def test_runs_out_follows_what_is_left(client, me):
    item_id, pid, piid = make(me)
    with SessionLocal() as s:
        assert runs_out(s, me, TODAY)[item_id].days == 60
    log(client, pid, piid)
    with SessionLocal() as s:
        assert runs_out(s, me, TODAY)[item_id].days == 59


def test_the_lots_account_for_the_units_dosed(client, me):
    from app.inventory.groups import lots
    item_id, pid, piid = make(me, units=5)
    log(client, pid, piid)
    with SessionLocal() as s:
        assert [l.remaining for l in lots(s.get(InventoryItem, item_id))] == [4]


def test_the_inventory_page_shows_the_lower_count(client, me):
    item_id, pid, piid = make(me)
    log(client, pid, piid)
    page = client.get("/inventory").text
    row = page.split("Unitdose B12", 1)[1].split("</tr>", 1)[0]
    assert ">59<" in row.replace(" ", "").replace("\n", "")


def test_migration_0066_adds_the_columns(tmp_path):
    import sqlite3
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent
    db = tmp_path / "m.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0066")
    with sqlite3.connect(db) as c:
        assert "dosed_units" in {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "inventory_units" in {r[1] for r in c.execute("pragma table_info(dose_logs)")}
    command.downgrade(cfg, "0065")
    with sqlite3.connect(db) as c:
        assert "dosed_units" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}


def left(*ids):
    with SessionLocal() as s:
        return tuple(s.get(InventoryItem, i).available_count for i in ids)


def test_two_items_with_the_same_name_use_up_their_own_stock(client, me):
    drops, drops_protocol, drops_line = make(me)
    pills, pills_protocol, pills_line = make(me, medium=Medium.PILL, units=30)
    powder, _, _ = make(me, medium=Medium.LYOPHILIZED, size=10.0, size_unit=DoseUnit.MG, dose=1.0, dose_unit=DoseUnit.MG)
    assert log(client, drops_protocol, drops_line).status_code == 303
    assert left(drops, pills, powder) == (59, 30, 60)
    assert log(client, pills_protocol, pills_line).status_code == 303
    assert left(drops, pills) == (59, 29)
    with SessionLocal() as s:
        log_id = s.query(DoseLog).filter(DoseLog.protocol_id == drops_protocol).one().id
    client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False)
    assert left(drops, pills) == (60, 29)
    page = client.get("/inventory")
    assert page.status_code == 200 and page.text.count("Unitdose B12") >= 3
    assert client.get("/today").status_code == 200


def switch_line(client, protocol_id, peptide_id, new_item_id, *, route="oral"):
    """Edit the protocol the way the builder does, pointing its one line at another inventory item."""
    return client.post(f"/protocols/{protocol_id}", data={
        "name": "Unitdose Protocol", "start_date": (TODAY - timedelta(days=5)).isoformat(), "weeks": "8", "goal": ["fat-loss"],
        "items-0-peptide_id": str(peptide_id), "items-0-dose": "5000", "items-0-dose_unit": "mcg", "items-0-frequency": "daily",
        "items-0-time_of_day": "am", "items-0-route": route, "items-0-inventory_item_id": str(new_item_id),
    }, follow_redirects=False)


def two_forms(me):
    """Sublingual drops (linked) and a second same-named item to switch to; the line is set to the morning so an edit keeps its history."""
    drops, protocol_id, line = make(me)
    pills, _, _ = make(me, medium=Medium.PILL, units=30)
    with SessionLocal() as s:
        s.get(ProtocolItem, line).time_of_day = TimeOfDay.AM
        peptide_id = s.get(ProtocolItem, line).peptide_id
        s.commit()
    return drops, pills, protocol_id, line, peptide_id


def test_switching_a_line_to_the_other_form_takes_later_doses_from_it_and_keeps_the_history(client, me):
    drops, pills, protocol_id, line, peptide_id = two_forms(me)
    yesterday = TODAY - timedelta(days=1)
    assert log(client, protocol_id, line, yesterday).status_code == 303
    assert left(drops, pills) == (59, 30)
    assert switch_line(client, protocol_id, peptide_id, pills).status_code == 303
    with SessionLocal() as s:
        [new_line] = s.get(Protocol, protocol_id).items
        assert new_line.inventory_item_id == pills
        old = s.query(DoseLog).one()
        assert old.protocol_item_id == new_line.id and old.scheduled_date == yesterday      # the history follows the line
        new_line_id = new_line.id
    assert left(drops, pills) == (59, 30)                                                   # switching alone moves no stock
    assert log(client, protocol_id, new_line_id).status_code == 303
    assert left(drops, pills) == (59, 29)                                                   # today's dose comes out of the new form only



def test_undoing_a_dose_logged_before_a_same_day_switch_gives_the_unit_back_to_the_form_it_came_from(client, me):
    drops, pills, protocol_id, line, peptide_id = two_forms(me)
    assert log(client, protocol_id, line).status_code == 303
    assert left(drops, pills) == (59, 30)
    switch_line(client, protocol_id, peptide_id, pills)
    with SessionLocal() as s:
        log_id = s.query(DoseLog).one().id
    assert client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False).status_code == 303
    assert left(drops, pills) == (60, 30)
