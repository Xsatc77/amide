"""Settings > Medicines, and the red-and-black caution tape on peptides that are commonly flagged with them."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.library.interactions import cautions_for
from app.models import Peptide, UserMedicine


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(UserMedicine).filter(UserMedicine.owner_id == me).delete()
            s.commit()
    wipe()
    yield
    wipe()


def meds(me):
    with SessionLocal() as s:
        return sorted(m.name for m in s.scalars(select(UserMedicine).where(UserMedicine.owner_id == me)))


def test_rules_flag_the_expected_pairs():
    assert cautions_for("Semaglutide", None, ["Metformin 500mg"])
    assert cautions_for("CJC-1295 (No DAC)", "CJC", ["Insulin glargine"])
    assert cautions_for("BPC-157", None, ["Warfarin"])
    assert cautions_for("Thymosin Alpha 1", "TA-1", ["Prednisone"])


def test_unrelated_pairs_and_empty_lists_are_not_flagged():
    assert cautions_for("BPC-157", None, ["Metformin"]) == []
    assert cautions_for("Semaglutide", None, []) == []
    assert cautions_for("Semaglutide", None, ["Vitamin D"]) == []


def test_add_and_remove_a_medicine(client, db, me):
    r = client.post("/settings/medicines", data={"name": "  Metformin ", "notes": "500 mg"}, follow_redirects=False)
    assert r.status_code == 303 and meds(me) == ["Metformin"]
    page = client.get("/settings").text
    assert 'id="medicines"' in page and "Metformin" in page
    with SessionLocal() as s:
        mid = s.scalar(select(UserMedicine.id).where(UserMedicine.owner_id == me))
    assert client.post(f"/settings/medicines/{mid}/delete", follow_redirects=False).status_code == 303
    assert meds(me) == []


def test_duplicates_and_blanks_are_refused(client, db, me):
    client.post("/settings/medicines", data={"name": "Metformin"})
    assert client.post("/settings/medicines", data={"name": "metformin"}).status_code == 422
    assert client.post("/settings/medicines", data={"name": "  "}).status_code == 422
    assert meds(me) == ["Metformin"]


def test_a_flagged_peptide_gets_tape_in_the_library_and_a_note_on_its_page(client, db, me):
    with SessionLocal() as s:
        pid = s.scalar(select(Peptide.id).where(Peptide.name.like("Semaglutide%")))
    if pid is None:
        pytest.skip("no semaglutide card in the test library")
    assert "caution-tape" not in client.get(f"/library/{pid}").text.split("<style")[0].split('class="lib-layout"')[-1]
    client.post("/settings/medicines", data={"name": "Metformin"})
    assert "Caution with your medicines" in client.get(f"/library/{pid}").text
    assert "caution-tape" in client.get("/library").text


def test_another_persons_medicine_cannot_be_removed(client, db, me):
    assert client.post("/settings/medicines/999999/delete").status_code == 404


def test_a_medicine_can_carry_a_dose_text_shown_in_the_list(client, db, me):
    from app.models import UserMedicine
    r = client.post("/settings/medicines", data={"name": "Dose Text Med", "dose_text": " 10 mg daily "}, follow_redirects=False)
    assert r.status_code == 303
    row = db.query(UserMedicine).filter_by(owner_id=me, name="Dose Text Med").one()
    assert row.dose_text == "10 mg daily" and "10 mg daily" in client.get("/settings").text
    assert client.post("/settings/medicines", data={"name": "Too Long Dose", "dose_text": "x" * 81}).status_code == 422
    db.delete(row)
    db.commit()
