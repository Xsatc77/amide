import html

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, InventoryItem, User


def text(r) -> str:
    return html.unescape(r.text)


@pytest.fixture
def lyo_item(client):
    """A Lyophilized inventory item this test user owns, with 2 in stock."""
    client.post("/inventory", data={"name": "AV Test Peptide", "count": "2", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        return s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Test Peptide"))


@pytest.fixture
def mcg_item(client):
    """A Lyophilized inventory item labeled in mcg, not mg -- Reconstitute must not treat its
    vial_size_mg number as milligrams (that would be a 1000x concentration error)."""
    client.post("/inventory", data={"name": "AV Mcg Peptide", "count": "2", "vial_size_mg": "500",
                                    "vial_size_unit": "mcg", "medium": "Lyophilized"})
    with SessionLocal() as s:
        return s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Mcg Peptide"))


def test_calculator_inventory_select_uses_item_id_as_value(client, db, lyo_item):
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}" data-vial-mg="10">AV Test Peptide (10 mg)</option>' in t


def test_calculator_ignores_vial_mg_override_when_item_selected(client, db, lyo_item):
    """A bookmarked/forged ?vial_mg= must never win over the selected item's real amount --
    otherwise the confirmation modal (built from the live field) can save a different
    concentration than what the item's own vial size implies."""
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}&vial_mg=999"))
    assert 'id="calc-vial"' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]
    assert 'value="999"' not in t


def test_calculator_prefills_from_inventory_item_id_query_param(client, db, lyo_item):
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}"))
    assert f'<option value="{lyo_item}" data-vial-mg="10" selected>' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]  # the vial-amount field is prefilled


def test_reconstitute_commit_creates_vial_and_decrements_count(client, db, lyo_item, me):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"

    with SessionLocal() as s:
        item = s.get(InventoryItem, lyo_item)
        assert item.count == 1  # started at 2
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == lyo_item))
        assert vial is not None and vial.owner_id == me
        assert vial.concentration_mg_ml == pytest.approx(5.0)  # 10mg / 2mL
        assert vial.doses_total == 40  # 10mg // (250mcg = 0.25mg) == 40
        assert vial.discarded_at is None


def test_reconstitute_commit_refuses_when_count_is_zero(client, db, me):
    client.post("/inventory", data={"name": "Empty Stock", "count": "0", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Empty Stock"))

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/calculator?inventory_item_id={item_id}")
    assert "reconstitute_error=" in r.headers["location"]
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0

    t = text(client.get(r.headers["location"]))
    assert "none left in stock" in t


def test_calculator_page_shows_count_0_and_disables_from_dropdown_data(client, db):
    client.post("/inventory", data={"name": "Empty Stock 2", "count": "0", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    t = text(client.get("/calculator"))
    assert '"count": 0' in t


def test_reconstitute_commit_requires_ownership(client, db, lyo_item):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVOther", "password": "AVOther1!", "confirm": "AVOther1!"})
    r = other.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404


def test_calculator_page_excludes_non_mg_inventory_items(client, db, lyo_item, mcg_item):
    """vial_size_mg is only ever milligrams to the reconstitution math; an mcg- or IU-labeled
    item would silently compute a wildly wrong concentration if offered here."""
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}"' in t
    assert f'<option value="{mcg_item}"' not in t


def test_reconstitute_commit_rejects_non_mg_item(client, db, mcg_item):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(mcg_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 404
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=mcg_item).count() == 0
        assert s.get(InventoryItem, mcg_item).count == 2  # untouched


def test_reconstitute_button_hidden_for_non_mg_item(client, db, mcg_item):
    t = text(client.get("/inventory"))
    assert f'data-item-id="{mcg_item}"' not in t


def _button_tag(t: str, item_id) -> str:
    """The full opening <button ...> tag whose data-item-id matches, regardless of attribute
    order or surrounding whitespace -- avoids brittle fixed-length slicing."""
    marker = f'data-item-id="{item_id}"'
    start = t.rindex("<button", 0, t.index(marker))
    end = t.index(">", t.index(marker)) + 1
    return t[start:end]


def test_reconstitute_button_present_and_disabled_when_out_of_stock(client, db, lyo_item):
    client.post("/inventory", data={"name": "Zero Stock", "count": "0", "vial_size_mg": "5",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        zero_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Zero Stock"))
    t = text(client.get("/inventory"))
    assert f'data-action="reconstitute" data-item-id="{lyo_item}"' in t
    assert "disabled" in _button_tag(t, zero_id)
    assert "disabled" not in _button_tag(t, lyo_item)  # has stock -- not disabled


def test_duplicate_vial_check_scoped_to_exact_item(client, db, lyo_item):
    client.post("/inventory", data={"name": "AV Test Peptide Two", "count": "1", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        other_item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Test Peptide Two"))

    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })

    t = text(client.get("/inventory"))
    # The exact item with an open vial carries its data for the JS-side warning dialog.
    assert "data-active-vial=" in _button_tag(t, lyo_item)
    # The similarly-named other item has no open vial and carries no such data.
    assert "data-active-vial=" not in _button_tag(t, other_item_id)


from datetime import datetime, timedelta


def _reconstitute(client, item_id, discard_by="2026-12-31"):
    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": discard_by,
    })
    with SessionLocal() as s:
        return s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == item_id))


def test_active_vial_card_shows_icon_and_label_fields(client, db, lyo_item):
    _reconstitute(client, lyo_item)
    t = text(client.get("/inventory"))
    assert 'id="active-vials"' in t
    assert "vial-blank-label.png" in t
    assert "AV Test Peptide" in t.split('id="active-vials"')[1]
    assert "5 mg/mL" in t  # 10mg / 2mL
    assert "10 mg" in t.split('id="active-vials"')[1].split("5 mg/mL")[0][-30:]  # total content near it


def test_discard_sets_discarded_at_and_leaves_active_list(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item)
    r = client.post(f"/active-vials/{vial_id}/discard", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        assert v is not None and v.discarded_at is not None  # row survives
    t = text(client.get("/inventory"))
    assert "AV Test Peptide" not in t.split('id="active-vials"')[1].split("</section>")[0]


def test_expiry_popup_fires_once_then_throttles_24_hours(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")  # already expired
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' in t  # server flags it for the JS popup to show

    client.post(f"/active-vials/{vial_id}/snooze-prompt")
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).last_discard_prompt_at is not None

    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' not in t  # throttled, no popup flag
    section = t.split('id="active-vials"')[1].split("</section>")[0]
    assert "vial-card-yellow" in section  # renders yellow instead


def test_expiry_popup_does_not_repeat_within_24_hours(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = datetime.utcnow() - timedelta(hours=1)
        s.commit()
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' not in t

    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = datetime.utcnow() - timedelta(hours=25)
        s.commit()
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' in t


def test_expiry_prompt_names_each_expired_vial(client, db, lyo_item):
    """With more than one expired vial, the popup must say which one it's about -- the JS queues
    them one at a time using this per-card name and the dialog's data-fill="item-name" span."""
    client.post("/inventory", data={"name": "AV Second Peptide", "count": "2", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        second_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Second Peptide"))
    vial_id_1 = _reconstitute(client, lyo_item, discard_by="2020-01-01")
    vial_id_2 = _reconstitute(client, second_id, discard_by="2020-01-01")

    t = text(client.get("/inventory"))
    assert f'data-vial-id="{vial_id_1}"' in t and 'data-item-name="AV Test Peptide"' in \
        t[t.index(f'data-vial-id="{vial_id_1}"'):t.index(f'data-vial-id="{vial_id_1}"') + 400]
    assert f'data-vial-id="{vial_id_2}"' in t and 'data-item-name="AV Second Peptide"' in \
        t[t.index(f'data-vial-id="{vial_id_2}"'):t.index(f'data-vial-id="{vial_id_2}"') + 400]
    assert 'data-fill="item-name"' in t.split('id="expiry-prompt"')[1][:200]


def test_shared_active_vial_is_read_only(client, db, lyo_item, me):
    from app.models import Share, ShareCategory

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVShared", "password": "AVShared1!", "confirm": "AVShared1!"})
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "avshared"))
        s.add(Share(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY))
        s.commit()

    try:
        vial_id = _reconstitute(client, lyo_item)
        t = text(other.get("/inventory"))
        section = t.split('id="active-vials"')[1].split("</section>")[0]
        assert "AV Test Peptide" in section
        assert f'data-vial-id="{vial_id}"' in section
        assert f'action="/active-vials/{vial_id}/discard"' not in section  # read-only: no discard button for a grantee

        assert other.post(f"/active-vials/{vial_id}/discard").status_code == 404
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY).delete()
            s.commit()

    assert other.post(f"/active-vials/{vial_id}/discard").status_code == 404
