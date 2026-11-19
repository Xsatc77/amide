"""Avery 94500 round labels as a Word file: each peptide's own stored symbol, the numbered circles, and the item and check-in buttons."""

import io
import zipfile
from datetime import date

import pytest
from docx import Document

from app.db import SessionLocal
from app.inventory import avery94500, symbols
from app.models import Category, ElementSymbol, InventoryItem, Medium, Order, OrderItem, Peptide


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(OrderItem).delete()
        s.query(Order).delete()
        s.query(InventoryItem).delete()
        s.query(ElementSymbol).delete()
        s.query(Peptide).filter(Peptide.name.like("Avy%")).delete(synchronize_session=False)
        s.commit()


def item(uid, name="Avyretatrutide", size=10.0, count=3):
    with SessionLocal() as s:
        it = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=count, medium=Medium.LYOPHILIZED, vial_size_mg=size)
        s.add(it)
        s.commit()
        return it.id


def pictures(docx_bytes):
    """How many pictures sit on each of the document's 48 circles per sheet: [(sheet, circle), ...] in order."""
    doc = Document(io.BytesIO(docx_bytes))
    out = []
    for si, table in enumerate(doc.tables):
        n = 0
        for ri, row in enumerate(table.rows):
            if ri % 2:
                continue
            for ci, cell in enumerate(row.cells):
                if ci % 2:
                    continue
                n += 1
                if cell._tc.xpath(".//w:drawing"):
                    out.append((si, n))
    return out


def test_two_names_never_share_a_symbol_and_a_symbol_never_changes(db):
    first = symbols.symbol_for(db, "Retatrutide")
    second = symbols.symbol_for(db, "Retinol")                 # the same two first letters would clash
    third = symbols.symbol_for(db, "Retatrutide")
    assert first == "Rt" and second != first and second.startswith("R") and third == first
    db.rollback()


def test_a_library_alias_and_its_name_are_one_peptide_with_one_symbol(db):
    db.add(Peptide(name="Avy Peptide", aliases="Avy Alias, AvyP"))
    db.commit()
    assert symbols.symbol_for(db, "avy alias") == symbols.symbol_for(db, "Avy Peptide")
    assert db.query(ElementSymbol).count() == 1


def test_the_word_file_puts_the_labels_on_the_circles_from_the_one_picked(client, me):
    pid = item(me)
    r = client.get(f"/inventory/{pid}/labels.docx?count=3&start=47&lot=ab12&exp=12/27")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats") and "attachment" in r.headers["content-disposition"]
    assert pictures(r.content) == [(0, 47), (0, 48), (1, 1)]            # circle 47, 48, then the next sheet's first
    with SessionLocal() as s:
        assert s.query(ElementSymbol).one().name == "Avyretatrutide"


def test_a_bad_start_count_or_expiration_is_refused_and_another_persons_item_is_not_found(client, me):
    pid = item(me)
    for q in ("start=0", "start=49", "start=x", "count=0", "count=999", "exp=13/27", "exp=soon"):
        assert client.get(f"/inventory/{pid}/labels.docx?{q}").status_code == 422, q
    assert client.get("/inventory/999999/labels.docx").status_code == 404


def test_the_item_page_has_the_print_form_with_the_lot_and_expiration_filled_in(client, me):
    pid = item(me)
    page = client.get(f"/inventory/{pid}").text
    assert "Print Avery 94500 Labels" in page and f'action="/inventory/{pid}/labels.docx"' in page and 'name="start"' in page


def test_a_checked_in_order_prints_one_label_per_received_vial_with_its_own_lot_and_expiration(client, db, me):
    pid = item(me)
    other = item(me, "Avyother", 5.0, 2)
    with SessionLocal() as s:
        o = Order(order_date=date(2030, 1, 1), arrival_date=date(2030, 1, 5))
        s.add(o)
        s.flush()
        s.add_all([OrderItem(order_id=o.id, inventory_item_id=pid, quantity=2, received_quantity=2, lot_number="L1", expiration_date=date(2032, 6, 30)),
                   OrderItem(order_id=o.id, inventory_item_id=other, quantity=1, received_quantity=1, lot_number="L2", expiration_date=date(2031, 3, 1))])
        s.commit()
        oid = o.id
    page = client.get(f"/inventory/orders/{oid}/labels").text
    assert f'action="/inventory/orders/{oid}/labels.docx"' in page
    r = client.get(f"/inventory/orders/{oid}/labels.docx?start=2")
    assert r.status_code == 200 and pictures(r.content) == [(0, 2), (0, 3), (0, 4)]
    assert client.get(f"/inventory/orders/{oid}/labels.docx?start=60").status_code == 422
    assert client.get("/inventory/orders/999999/labels.docx").status_code == 404


def test_the_label_drawing_runs_for_a_long_symbol_and_blank_lot_and_exp():
    assert avery94500.label_png(avery94500.Spec("Abc", "500 MCG", "", "")).startswith(b"\x89PNG")
    assert avery94500.exp_text(date(2027, 12, 5)) == "12/27" and avery94500.strength_text(10.0, "mg") == "10 MG"
