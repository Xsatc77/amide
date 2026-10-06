"""Builders for backup tests. Invented vendors (Acme, Zephyr) and peptides (Zorvex, Quillamine) only: real vendors and
price lists never appear in the repository."""

from datetime import date, datetime

from sqlalchemy import text

from app import config
from app.backup import sections as reg
from app.models import (
    ActiveVial, BodyMeasurement, ContactMethodType, DispensingMethod, DoseLog, DoseStatus, DoseUnit, FitnessTestExerciseName,
    FitnessTestResult, InventoryItem, JournalEntry, JournalEntrySideEffect, JournalQuickNote, JournalSideEffect, LabMarker,
    LabPanel, LabResult, Order, OrderItem, Peptide, PeptideSource, PriceList, PriceListItem, Protocol, ProtocolItem, Route, Sale,
    TimeOfDay, Vendor, VendorContact, VendorWallet, Warehouse, WarehouseSource, WaterLog, WorkoutExercise, WorkoutExerciseLog,
    WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource,
)

PDF = b"%PDF-1.4 test file"


def write(directory, name: str, data: bytes = PDF) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return name


def seed_world(db, uid: int, tag: str = "A") -> dict:
    """One person's data across every person section, plus a vendor, a price list and a custom peptide.
    Returns the main objects by name."""
    w: dict = {}
    method = db.query(ContactMethodType).filter_by(name="Email").first() or ContactMethodType(name="Email")
    vendor = Vendor(name=f"Acme Labs {tag}", website="https://acme.example", notes=f"note {tag}", created_by_id=uid,
                    price_list_filename=write(config.PRICE_LIST_DIR, f"vendor-{tag}.pdf"))
    vendor.contacts.append(VendorContact(method_type=method, value=f"sales@acme-{tag.lower()}.example"))
    vendor.wallets.append(VendorWallet(coin="BTC", address=f"bc1q{tag.lower()}wallet0000000000", network="Bitcoin",
                                       qr_filename=write(config.WALLET_QR_DIR, f"qr-{tag}.png", b"\x89PNG\r\n\x1a\n")))
    peptide = Peptide(name=f"Zorvex {tag}", source=PeptideSource.CUSTOM)
    db.add_all([vendor, peptide])
    db.flush()
    w["vendor"], w["peptide"] = vendor, peptide

    item = InventoryItem(owner_id=uid, name=f"Zorvex {tag} 10mg", vial_size_mg=10.0, vendor_id=vendor.id, count=5)
    db.add(item)
    db.flush()
    order = Order(order_date=date(2026, 3, 1), vendor_id=vendor.id, vendor=vendor.name)
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=5, received_quantity=5,
                     coa_filename=write(config.COA_DIR, f"coa-{tag}.pdf")))
    db.add(Sale(inventory_item_id=item.id, quantity=1, sale_date=date(2026, 3, 5), price_cents=2500))
    vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0, dose_value=250.0,
                      dose_unit=DoseUnit.MCG, doses_total=40, dispensing_method=DispensingMethod.SYRINGE,
                      volume_remaining_ml=1.5, date_mixed=date(2026, 3, 2), discard_by=date(2026, 4, 2))
    db.add(vial)
    db.flush()
    w["item"], w["order"], w["vial"] = item, order, vial

    protocol = Protocol(owner_id=uid, name=f"Stack {tag}", start_date=date(2026, 3, 3))
    protocol.items.append(ProtocolItem(peptide_id=peptide.id, inventory_item_id=item.id))
    db.add(protocol)
    db.flush()
    db.add(DoseLog(owner_id=uid, protocol_id=protocol.id, protocol_item_id=protocol.items[0].id, active_vial_id=vial.id,
                   peptide_id=peptide.id, peptide_name=peptide.name, dose_value=250.0, dose_unit=DoseUnit.MCG,
                   route=Route.SUBQ, scheduled_date=date(2026, 3, 4), scheduled_time_of_day=TimeOfDay.AM,
                   status=DoseStatus.ON_TIME))
    w["protocol"] = protocol

    plan = WorkoutPlan(owner_id=uid, name=f"Plan {tag}", source=WorkoutSource.MANUAL, started_on=date(2026, 3, 1),
                       source_pdf_filename=write(config.WORKOUT_PDF_DIR, f"plan-{tag}.pdf"))
    plan.days = [WorkoutPlanDay(position=0, label="Day A")]
    plan.days[0].exercises = [WorkoutExercise(position=0, name="Bench Press", sets_text="3", reps_text="8")]
    db.add(plan)
    db.flush()
    log = WorkoutLog(owner_id=uid, plan_day_id=plan.days[0].id, day_label="Day A", plan_name=plan.name,
                     log_date=date(2026, 3, 6))
    log.exercise_logs.append(WorkoutExerciseLog(exercise_id=plan.days[0].exercises[0].id, name="Bench Press", completed=True,
                                                sets=3, reps_value=8, weight_value=135.0, net_kcal=20.0))
    db.add(log)
    db.add(FitnessTestResult(owner_id=uid, exercise=FitnessTestExerciseName.MAX_PUSHUPS, value=30.0, tested_at=date(2026, 3, 7)))
    w["plan"] = plan

    db.add(BodyMeasurement(owner_id=uid, measured_at=date(2026, 3, 8), weight_lbs=180.0))
    db.add(WaterLog(owner_id=uid, logged_at=datetime(2026, 3, 8, 9, 0), ounces=16.0))
    entry = JournalEntry(owner_id=uid, entry_date=date(2026, 3, 9), mood=4, notes=f"journal {tag}")
    entry.side_effects.append(JournalEntrySideEffect(side_effect=JournalSideEffect.HEADACHE))
    entry.quick_notes.append(JournalQuickNote(noted_at=datetime(2026, 3, 9, 10, 0), text="quick"))
    db.add(entry)
    panel = LabPanel(owner_id=uid, drawn_at=date(2026, 3, 10), report_filename=write(config.LAB_REPORT_DIR, f"lab-{tag}.pdf"))
    panel.results.append(LabResult(marker=LabMarker.TOTAL_TESTOSTERONE, value=550.0))
    db.add(panel)
    pl = PriceList(vendor_name=vendor.name, vendor_id=vendor.id, warehouse=Warehouse.US, warehouse_source=WarehouseSource.FILENAME,
                   list_date=date(2026, 3, 11), source_filename=f"{vendor.name} - us - 2026-03-11.pdf")
    pl.items.append(PriceListItem(code="ZX", product_name=peptide.name, peptide_id=peptide.id, vial_amount=10, vial_unit="mg",
                                  pack_size=10, pack_price=100.0))
    db.add(pl)
    db.commit()
    return w


def person_counts(db, uid: int, keys=None) -> dict[str, int]:
    """{table: row count} of the person's rows for the person sections (and every row of the shared tables)."""
    from app.backup.export import table_rows
    out = {}
    for key in keys or reg.LOAD_ORDER:
        section = reg.SECTIONS[key]
        for tbl in section.tables:
            who = uid if section.level == reg.PERSON else None
            out[tbl.name] = len(table_rows(db, tbl, who))
    return out


def wipe_person(db, uid: int) -> None:
    """Delete one person's rows in every person section and every shared row the seed made (not accounts)."""
    for table in ("dose_logs", "protocols", "workout_logs", "workout_plans", "fitness_test_results", "body_measurements",
                  "water_logs", "journal_entries", "lab_panels", "active_vials"):
        db.execute(text(f"DELETE FROM {table} WHERE owner_id = :u"), {"u": uid})
    db.execute(text("DELETE FROM orders WHERE id IN (SELECT order_id FROM order_items)"))
    db.execute(text("DELETE FROM inventory_items WHERE owner_id = :u"), {"u": uid})
    db.execute(text("DELETE FROM price_lists"))
    db.execute(text("DELETE FROM vendors"))
    db.execute(text("DELETE FROM peptides WHERE source = 'custom'"))
    db.commit()


def clean_files() -> None:
    for directory in (config.COA_DIR, config.PRICE_LIST_DIR, config.LAB_REPORT_DIR, config.WORKOUT_PDF_DIR, config.WALLET_QR_DIR):
        if directory.is_dir():
            for f in directory.glob("*"):
                f.unlink()
