# tests/test_price_list_importer.py
from datetime import date

from sqlalchemy import func, select

from app.models import (
    Peptide, PeptideSource, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)


def make_list(db, vendor=None, filename="Acme - Price List - 2026-09-01.pdf"):
    plist = PriceList(vendor_name="Acme", vendor_id=vendor.id if vendor else None, list_date=date(2026, 9, 1),
                      source_filename=filename, warehouse=Warehouse.CHINA, warehouse_source=WarehouseSource.ASSUMED)
    plist.items.append(PriceListItem(code="ZX10", product_name="Zorvex", vial_amount=10, vial_unit="mg",
                                     pack_size=10, pack_price=50.0, pack_type="kit", extra_prices={"10kits+": 45.0},
                                     flags=["code-size-mismatch"]))
    db.add(plist)
    db.commit()
    return plist


def test_price_list_round_trips_with_json_columns(db):
    make_list(db)
    db.expire_all()
    item = db.scalar(select(PriceListItem))
    assert (item.vial_amount, item.pack_size, item.pack_price, item.pack_type) == (10, 10, 50.0, "kit")
    assert item.extra_prices == {"10kits+": 45.0} and item.flags == ["code-size-mismatch"]
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source, plist.currency) == (Warehouse.CHINA, WarehouseSource.ASSUMED, "USD")
    assert plist.imported_at is not None


def test_deleting_a_list_deletes_its_items(db):
    plist = make_list(db)
    db.delete(plist)
    db.commit()
    assert db.scalar(select(func.count()).select_from(PriceListItem)) == 0


def test_deleting_the_vendor_leaves_the_list_with_its_name(db):
    vendor = Vendor(name="Acme")
    db.add(vendor)
    db.commit()
    make_list(db, vendor)
    db.delete(vendor)
    db.commit()
    db.expire_all()
    plist = db.scalar(select(PriceList))
    assert plist.vendor_id is None and plist.vendor_name == "Acme"


def test_deleting_a_matched_card_keeps_the_item(db):
    card = Peptide(name="Zorvex Card", source=PeptideSource.CUSTOM)
    db.add(card)
    db.commit()
    plist = make_list(db)
    plist.items[0].peptide_id = card.id
    db.commit()
    db.delete(card)
    db.commit()
    db.expire_all()
    assert db.scalar(select(PriceListItem)).peptide_id is None


def test_source_filename_is_unique(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    make_list(db)
    with pytest.raises(IntegrityError):
        make_list(db)
    db.rollback()


import pytest

from app.library.price_lists.importer import decide_warehouse, store_price_list, vendor_key
from app.library.price_lists.reader import PriceListData
from app.library.price_lists.rows import ParsedRow, Spec

FILE = "Acme Labs - Price List - 2026-09-01.pdf"


def row(code, name, amount=10, unit="mg", pack=10, price=50.0, **kw):
    return ParsedRow(code=code, name=name, spec=Spec(amount, unit, pack), pack_price=price, **kw)


def data(*rows, shipping=None, hint=None):
    return PriceListData(rows=list(rows), shipping_note=shipping, warehouse_hint=hint)


def add_card(db, name, specs=None):
    db.add(Peptide(name=name, source=PeptideSource.CUSTOM, library_specifications=specs))
    db.commit()


def store(db, filename, d, **kw):
    kw.setdefault("prefix_table", {})
    return store_price_list(db, filename, d, **kw)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_vendor_key_ignores_case_punctuation_and_the_word_peptide():
    assert vendor_key("Zephyr Peptides") == vendor_key("Zephyr") == "zephyr"
    assert vendor_key("Orchid Peptide") == vendor_key("orchid") == "orchid"
    assert vendor_key("Mid Valley Bio") == "midvalleybio"


def test_decide_warehouse_precedence():
    assert decide_warehouse(None, None, None) == (Warehouse.CHINA, WarehouseSource.ASSUMED)
    assert decide_warehouse(None, "us", None) == (Warehouse.US, WarehouseSource.TEXT)
    assert decide_warehouse("us", "china", None) == (Warehouse.US, WarehouseSource.FILENAME)
    assert decide_warehouse("us", "china", "china") == (Warehouse.CHINA, WarehouseSource.MANUAL)


def test_import_creates_the_vendor_and_the_list(db):
    report = store(db, FILE, data(row("ZX10", "Zorvex 10")))
    vendor = db.scalar(select(Vendor).where(Vendor.name == "Acme Labs"))
    assert report.vendor_created and vendor.created_by_id is None
    assert vendor.price_list_updated_at == date(2026, 9, 1)
    plist = db.scalar(select(PriceList))
    assert (plist.vendor_name, plist.vendor_id, plist.list_date, plist.source_filename) == (
        "Acme Labs", vendor.id, date(2026, 9, 1), FILE)


def test_existing_vendor_is_reused_ignoring_case_and_the_word_peptides(db):
    db.add_all([Vendor(name="Zephyr Peptides"), Vendor(name="Northwind")])
    db.commit()
    store(db, "Zephyr - Price List NEW - 2026-05-19.pdf", data(row("ZX10", "Zorvex")))
    store(db, "northwind - USA Price List - 2026-10-03.pdf", data(row("ZX10", "Zorvex")))
    assert count(db, Vendor) == 2
    assert {p.vendor_name for p in db.scalars(select(PriceList))} == {"Zephyr", "northwind"}
    assert all(p.vendor_id is not None for p in db.scalars(select(PriceList)))


def test_one_vendor_can_have_a_china_and_a_usa_list(db):
    store(db, "Borealis - China Price List - 2026-08-24.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Borealis - USA Price List - 2026-08-31.pdf", data(row("ZX10", "Zorvex")))
    assert db.scalar(select(func.count()).select_from(Vendor).where(Vendor.name == "Borealis")) == 1
    lists = db.scalars(select(PriceList).order_by(PriceList.list_date)).all()
    assert [(l.warehouse, l.warehouse_source) for l in lists] == [
        (Warehouse.CHINA, WarehouseSource.FILENAME), (Warehouse.US, WarehouseSource.FILENAME)]
    assert lists[0].vendor_id == lists[1].vendor_id


def test_unnamed_warehouse_is_assumed_china(db):
    report = store(db, FILE, data(row("ZX10", "Zorvex")))
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source) == (Warehouse.CHINA, WarehouseSource.ASSUMED)
    assert (report.warehouse, report.warehouse_source) == ("china", "assumed")


def test_warehouse_override_and_text_hint(db):
    store(db, FILE, data(row("ZX10", "Zorvex"), hint="us"))
    assert db.scalar(select(PriceList.warehouse_source)) == WarehouseSource.TEXT
    store(db, FILE, data(row("ZX10", "Zorvex"), hint="us"), warehouse_override="china")
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source) == (Warehouse.CHINA, WarehouseSource.MANUAL)


def test_reimport_replaces_the_list_instead_of_duplicating(db):
    store(db, FILE, data(row("ZX10", "Zorvex", 10), row("ZX20", "Zorvex", 20)))
    store(db, FILE, data(row("ZX10", "Zorvex", 10)))
    assert count(db, PriceList) == 1 and count(db, PriceListItem) == 1


def test_newer_list_from_the_same_vendor_is_kept_as_history(db):
    store(db, "Acme Labs - Price List - 2026-09-01.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Acme Labs - Price List - 2026-10-01.pdf", data(row("ZX10", "Zorvex")))
    assert count(db, PriceList) == 2 and count(db, Vendor) == 1


def test_vendor_date_only_moves_forward(db):
    store(db, "Acme Labs - Price List - 2026-10-01.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Acme Labs - Price List - 2026-09-01.pdf", data(row("ZX10", "Zorvex")))
    assert db.scalar(select(Vendor.price_list_updated_at).where(Vendor.name == "Acme Labs")) == date(2026, 10, 1)


def test_dry_run_reports_but_writes_nothing(db):
    add_card(db, "Zorvex")
    report = store(db, FILE, data(row("ZX10", "Zorvex", 10)), dry_run=True)
    assert report.rows == 1 and report.matched == 1 and report.vendor_created
    db.expire_all()
    assert count(db, Vendor) == 0 and count(db, PriceList) == 0
    assert db.scalar(select(Peptide.library_specifications).where(Peptide.name == "Zorvex")) is None


def test_never_creates_library_cards(db):
    before = count(db, Peptide)
    report = store(db, FILE, data(row("ZX10", "Unknown Thing")))
    assert count(db, Peptide) == before
    assert db.scalar(select(PriceListItem.peptide_id)) is None
    assert report.unmatched == ["Unknown Thing"] and report.matched == 0


def test_matched_rows_add_vial_sizes_and_keep_the_cards_existing_ones(db):
    add_card(db, "Zorvex", specs="50mg")
    report = store(db, FILE, data(row("ZX5", "Zorvex", 5), row("ZX10", "Zorvex", 10), row("ZX3", "Zorvex", 3, "ml")))
    assert db.scalar(select(Peptide.library_specifications).where(Peptide.name == "Zorvex")) == "5mg, 10mg, 50mg"
    assert report.specs_added == 1
    card = db.scalar(select(Peptide).where(Peptide.name == "Zorvex"))
    assert {i.peptide_id for i in db.scalars(select(PriceListItem))} == {card.id}


def test_liquids_are_stored_but_add_no_specs(db):
    store(db, FILE, data(row("BA10", "Bacteriostatic Water", 10, "ml")))
    item = db.scalar(select(PriceListItem))
    assert (item.vial_unit, item.peptide_id) == ("ml", None)


def test_with_and_without_variants_stay_separate(db):
    add_card(db, "Zorvex with B12")
    add_card(db, "Zorvex without B12")
    store(db, FILE, data(row("Z5", "Zorvex without B12", 5)))
    specs = dict(db.execute(select(Peptide.name, Peptide.library_specifications)).all())
    assert specs["Zorvex without B12"] == "5mg" and specs["Zorvex with B12"] is None


@pytest.mark.parametrize("pack, expected", [(10, "kit"), (5, "box"), (1, "box"), (None, None), (12, None)])
def test_pack_type_is_kit_for_ten_vials_and_box_for_fewer(db, pack, expected):
    store(db, FILE, data(row("ZX10", "Zorvex", pack=pack)))
    item = db.scalar(select(PriceListItem))
    assert (item.pack_size, item.pack_type) == (pack, expected)


def test_pack_size_price_extras_and_flags_are_stored(db):
    store(db, FILE, data(row("TC250", "Zorvex Oil", 250, "mg/ml", 1, 30.0,
                             extra_prices={"10kits+": 25.0}, flags=["code-size-mismatch"])))
    item = db.scalar(select(PriceListItem))
    assert (item.vial_amount, item.vial_unit, item.pack_size, item.pack_price, item.pack_type) == (
        250, "mg/ml", 1, 30.0, "box")
    assert item.extra_prices == {"10kits+": 25.0} and item.flags == ["code-size-mismatch"]


def test_row_with_no_name_is_stored_and_flagged(db):
    report = store(db, FILE, data(row("ZZ9", None)))
    item = db.scalar(select(PriceListItem))
    assert item.product_name is None and item.flags == ["no-name"]
    assert report.flagged == ["ZZ9 -: no-name"]


def test_file_without_a_vendor_or_date_is_skipped(db):
    report = store(db, "Price List.pdf", data(row("ZX10", "Zorvex")))
    assert report.skipped and count(db, PriceList) == 0 and count(db, Vendor) == 0


def test_list_with_no_rows_is_skipped(db):
    report = store(db, FILE, data())
    assert "scanned" in report.skipped and count(db, PriceList) == 0


def test_names_are_repaired_from_the_learned_code_table(db):
    from app.library.price_lists.names import PrefixName
    store(db, FILE, data(row("ZX10", None)), prefix_table={"ZX": PrefixName("zorvex", "Zorvex")})
    item = db.scalar(select(PriceListItem))
    assert item.product_name == "Zorvex" and item.flags == ["name-from-code"]


from app.db import SessionLocal
from app.library.price_lists.importer import format_reports, run_import


def test_run_import_reads_folders_learns_codes_and_skips_non_pdfs(tmp_path):
    names = {
        "a": "A - Price List - 2026-01-01.pdf", "b": "B - Price List - 2026-01-02.pdf",
        "c": "C - Price List - 2026-01-03.pdf", "scan": "D - Price List - 2026-01-04-01.jpg",
    }
    for name in names.values():
        (tmp_path / name).write_bytes(b"")
    # fictional product names, so no seeded library card is matched and given sizes
    reading = {
        names["a"]: data(row("ZX5", "Zorvex", 5), row("ZX10", None, 10)),
        names["b"]: data(row("ZX5", "zorvex", 5)),
        names["c"]: data(row("ZX10", None, 10), row("QU5", "Quillamine", 5)),  # ZX10 has no name anywhere
    }
    reports = run_import([tmp_path], SessionLocal, reader=lambda path: reading[path.name])

    by_name = {r.filename: r for r in reports}
    assert "not a PDF" in by_name[names["scan"]].skipped
    with SessionLocal() as s:
        c_items = s.scalars(select(PriceListItem).join(PriceList).where(PriceList.source_filename == names["c"])
                            .order_by(PriceListItem.id)).all()
        assert [(i.code, i.product_name, i.flags) for i in c_items] == [
            ("ZX10", "Zorvex", ["name-from-code"]), ("QU5", "Quillamine", None)]  # QU seen at one vendor: not learned
        a_zx10 = s.scalar(select(PriceListItem).join(PriceList)
                          .where(PriceList.source_filename == names["a"], PriceListItem.code == "ZX10"))
        assert a_zx10.product_name == "Zorvex" and a_zx10.flags is None  # carried down its run, not repaired


def test_run_import_one_bad_file_does_not_stop_the_others(tmp_path):
    good, bad = "A - Price List - 2026-01-01.pdf", "B - Price List - 2026-01-02.pdf"
    for name in (good, bad):
        (tmp_path / name).write_bytes(b"")

    def reader(path):
        if path.name == bad:
            raise RuntimeError("unreadable")
        return data(row("ZX10", "Zorvex"))

    reports = {r.filename: r for r in run_import([tmp_path], SessionLocal, reader=reader)}
    assert "unreadable" in reports[bad].skipped and reports[good].skipped is None
    with SessionLocal() as s:
        assert [p.source_filename for p in s.scalars(select(PriceList))] == [good]


def test_run_import_dry_run_writes_nothing(tmp_path):
    name = "A - Price List - 2026-01-01.pdf"
    (tmp_path / name).write_bytes(b"")
    reports = run_import([tmp_path / name], SessionLocal, dry_run=True, reader=lambda p: data(row("ZX10", "Zorvex")))
    assert reports[0].rows == 1
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(PriceList)) == 0


def test_report_text_lists_flags_unmatched_and_assumed_warehouses(db):
    reports = [store(db, FILE, data(row("ZX10", "Unknown Thing", flags=["code-size-mismatch"])))]
    text = format_reports(reports)
    assert FILE in text and "Acme Labs (new)" in text and "china (assumed)" in text
    assert "ZX10 Unknown Thing: code-size-mismatch" in text and "Unknown Thing" in text
    assert "Warehouse assumed (China) for" in text and "dry run" not in text.lower()
    assert "dry run" in format_reports(reports, dry_run=True).lower()


def test_names_repaired_on_an_earlier_import_do_not_feed_the_next_learning_pass(db):
    from app.library.price_lists.importer import observations_from_db
    from app.library.price_lists.names import PrefixName
    store(db, FILE, data(row("ZX10", None), row("QU5", "Quillamine")),
          prefix_table={"ZX": PrefixName("zorvex", "Zorvex")})
    observed = {(prefix, name) for _, prefix, name in observations_from_db(db)}
    assert ("QU", "Quillamine") in observed and ("ZX", "Zorvex") not in observed


def test_report_mentions_spec_lines_that_were_not_read(db):
    d = data(row("ZX10", "Zorvex"))
    d.unread_spec_lines = 3
    text = format_reports([store(db, FILE, d)])
    assert "3 spec lines were not read" in text
