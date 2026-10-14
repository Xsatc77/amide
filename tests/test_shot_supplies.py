"""Supplies used by a shot: a syringe shot takes an alcohol pad and a dosing syringe, a peptide pen shot takes a pad and a pen needle, and converting an
open vial to a pen takes 2 pads, a reconstitution syringe, a pen cartridge and a pen needle. Logging a dose never fails for want of a supply."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.inventory import consumption
from app.models import (ActiveVial, Category, DispensingMethod, DoseLog, DoseUnit, Frequency, InventoryItem, Order, Peptide, PeptideSource, Protocol,
                        ProtocolItem, Route, SupplyType)
from supply_helpers import counts, make_supply, stock_everything
from test_run_out import make_stock

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(DoseLog).delete()
        s.query(ActiveVial).delete()
        s.query(Protocol).filter(Protocol.name.like("Runout%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).delete()
        s.query(Peptide).filter(Peptide.name.like("Runout%")).delete(synchronize_session=False)
        s.commit()


def setup(uid, *, method=DispensingMethod.SYRINGE, **stock):
    ids = stock_everything(uid, **stock)
    item = make_stock(uid, vials=3, size=10.0)
    with SessionLocal() as s:
        v = ActiveVial(owner_id=uid, inventory_item_id=item, concentration_mg_ml=5.0, water_ml=2.0, volume_remaining_ml=2.0, date_mixed=TODAY,
                       discard_by=TODAY + timedelta(days=28), doses_total=10, dispensing_method=method, confirmed=True)
        s.add(v)
        pep = Peptide(name="Runout Shot Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Runout Shot Protocol", start_date=TODAY - timedelta(days=5), owner_id=uid)
        s.add(p)
        s.flush()
        pi = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=1.0, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item)
        s.add(pi)
        s.commit()
        return ids, p.id, pi.id, v.id


def log(client, pid, piid, day=None):
    return client.post("/today/log", data={"protocol_id": str(pid), "protocol_item_id": str(piid), "scheduled_date": (day or TODAY).isoformat()}, follow_redirects=False)


def test_a_syringe_shot_takes_a_pad_and_a_dosing_syringe(client, me):
    ids, pid, piid, _ = setup(me)
    before = counts(ids)
    assert log(client, pid, piid).status_code == 303
    after = counts(ids)
    assert (after["pads"], after["dosing"]) == (before["pads"] - 1, before["dosing"] - 1)
    assert (after["recon"], after["pen_vial"], after["pen_needle"]) == (before["recon"], before["pen_vial"], before["pen_needle"])


def test_a_pen_shot_takes_a_pad_and_a_pen_needle(client, me):
    ids, pid, piid, _ = setup(me, method=DispensingMethod.PEN)
    before = counts(ids)
    log(client, pid, piid)
    after = counts(ids)
    assert (after["pads"], after["pen_needle"]) == (before["pads"] - 1, before["pen_needle"] - 1)
    assert (after["dosing"], after["recon"], after["pen_vial"]) == (before["dosing"], before["recon"], before["pen_vial"])


def test_undoing_the_dose_gives_the_supplies_back(client, me):
    ids, pid, piid, _ = setup(me)
    before = counts(ids)
    log(client, pid, piid)
    with SessionLocal() as s:
        log_id = s.query(DoseLog).one().id
    assert client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False).status_code == 303
    assert counts(ids) == before


def test_a_skipped_dose_takes_nothing(client, me):
    ids, pid, piid, _ = setup(me)
    before = counts(ids)
    client.post("/today/skip", data={"protocol_id": str(pid), "protocol_item_id": str(piid), "scheduled_date": TODAY.isoformat()}, follow_redirects=False)
    assert counts(ids) == before


def test_a_dose_with_no_open_vial_takes_no_supplies(client, me):
    ids, pid, piid, vid = setup(me)
    with SessionLocal() as s:
        s.get(ActiveVial, vid).discarded_at = date.today()
        s.commit()
    before = counts(ids)
    log(client, pid, piid)
    assert counts(ids) == before


def test_missing_supplies_never_stop_a_dose_from_being_logged_or_go_below_zero(client, me):
    ids, pid, piid, _ = setup(me, pads=0, dosing_syringes=1)
    assert log(client, pid, piid).status_code == 303
    after = counts(ids)
    assert after["pads"] == 0 and after["dosing"] == 0
    with SessionLocal() as s:
        assert s.query(DoseLog).count() == 1
    log(client, pid, piid, TODAY - timedelta(days=1))                          # no syringes left at all now
    assert counts(ids)["dosing"] == 0


def test_the_supplies_taken_are_remembered_on_the_dose(client, me):
    ids, pid, piid, _ = setup(me)
    log(client, pid, piid)
    with SessionLocal() as s:
        used = s.query(DoseLog).one().supplies_used
    assert used == {str(ids["pads"]): 1, str(ids["dosing"]): 1}


def test_a_shot_draws_on_the_next_item_when_the_first_runs_out(client, me):
    ids, pid, piid, _ = setup(me, pads=1)
    second = make_supply(me, "Test alcohol pads B", SupplyType.ALCOHOL_PAD, 5)
    log(client, pid, piid)
    log(client, pid, piid, TODAY - timedelta(days=1))
    with SessionLocal() as s:
        assert s.get(InventoryItem, ids["pads"]).count == 0 and s.get(InventoryItem, second).count == 4


def test_converting_a_vial_to_a_pen_takes_two_pads_a_reconstitution_syringe_a_cartridge_and_a_pen_needle(client, me):
    ids, pid, piid, vid = setup(me)
    before = counts(ids)
    r = client.post(f"/active-vials/{vid}/convert-to-pen", follow_redirects=False)
    assert r.status_code == 303
    after = counts(ids)
    assert (after["pads"], after["recon"], after["pen_vial"], after["pen_needle"]) == (before["pads"] - 2, before["recon"] - 1, before["pen_vial"] - 1, before["pen_needle"] - 1)
    assert after["dosing"] == before["dosing"]                                   # a converted vial does not use a dosing syringe


def test_the_conversion_list_is_the_one_given():
    assert consumption.CONVERT_SUPPLIES == {SupplyType.ALCOHOL_PAD: 2, SupplyType.RECON_SYRINGE: 1, SupplyType.PEN_VIAL: 1, SupplyType.PEN_NEEDLE: 1}
    assert consumption.SHOT_SUPPLIES == {SupplyType.ALCOHOL_PAD: 1, SupplyType.DOSING_SYRINGE: 1}
    assert consumption.PEN_SHOT_SUPPLIES == {SupplyType.ALCOHOL_PAD: 1, SupplyType.PEN_NEEDLE: 1}


def test_migration_0067_adds_the_column(tmp_path):
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
    command.upgrade(cfg, "0067")
    with sqlite3.connect(db) as c:
        assert "supplies_used" in {r[1] for r in c.execute("pragma table_info(dose_logs)")}
    command.downgrade(cfg, "0066")
    with sqlite3.connect(db) as c:
        assert "supplies_used" not in {r[1] for r in c.execute("pragma table_info(dose_logs)")}
