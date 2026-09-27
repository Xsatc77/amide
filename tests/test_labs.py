import html

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LabMarker, LabPanel, Share, ShareCategory, User

from tests.test_measurements_page import _logged_in_client, _text


def _tester_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def _clear_lab_panels(*owner_ids: int) -> None:
    with SessionLocal() as s:
        for oid in owner_ids:
            s.query(LabPanel).filter_by(owner_id=oid).delete()
        s.commit()


def test_new_panel_with_one_result_creates_a_panel(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": ["mIU/L"],
            "range_low[]": ["0.5"], "range_high[]": ["4.5"], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me_user.id)).all()
            [result] = panel.results
            assert result.marker == LabMarker.TSH and result.value == 2.5
            assert result.range_low == 0.5 and result.range_high == 4.5
    finally:
        _clear_lab_panels(me)


def test_new_panel_with_multiple_results(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH", "HDL"], "value[]": ["2.5", "55"], "unit[]": ["mIU/L", "mg/dL"],
            "range_low[]": ["", ""], "range_high[]": ["", ""], "marker_other[]": ["", ""],
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me_user.id)).all()
            assert {r.marker for r in panel.results} == {LabMarker.TSH, LabMarker.HDL}
    finally:
        _clear_lab_panels(me)


def test_other_marker_requires_marker_other_text(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["OTHER"], "value[]": ["1.2"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
    finally:
        _clear_lab_panels(me)


def test_non_other_marker_rejects_marker_other_text(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": ["Some Custom Name"],
        })
        assert r.status_code == 422
    finally:
        _clear_lab_panels(me)


def test_panel_with_zero_results_is_rejected(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={"drawn_at": "2026-09-28"})
        assert r.status_code == 422
    finally:
        _clear_lab_panels(me)


def test_out_of_range_flag_only_set_when_both_bounds_present(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH", "HDL", "LDL"],
            "value[]": ["9.0", "55", "130"],
            "unit[]": ["", "", ""],
            "range_low[]": ["0.5", "", "0"],
            "range_high[]": ["4.5", "", ""],  # HDL: no range at all; LDL: only a low bound
            "marker_other[]": ["", "", ""],
        }, follow_redirects=False)
        assert r.status_code == 303
        t = html.unescape(client.get("/measurements?tab=labs").text)
        # Scope to the rendered panel list -- the "New Panel" dialog's marker <select> (and its
        # <template> for repeatable rows) also contains every marker's name, including "TSH"/"HDL"/
        # "LDL", well before the actual results table. Rows render in submission order (TSH, HDL,
        # LDL) -- scope each assertion to the slice of the page between one marker's label and the
        # next, so a flag rendered next to one row can't be mistaken for a flag on a different row.
        t = t.split('id="labs-panels-heading"', 1)[1]
        tsh_idx = t.index("TSH")
        hdl_idx = t.index("HDL")
        ldl_idx = t.index("LDL")
        assert "Out of range" in t[tsh_idx:hdl_idx]
        assert "Out of range" not in t[hdl_idx:ldl_idx]
        assert "Out of range" not in t[ldl_idx:ldl_idx + 300]
    finally:
        _clear_lab_panels(me)


def test_labs_tab_respects_personal_data_sharing(client, db):
    me = _tester_id()
    other = _logged_in_client("LabsSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "labssharepartner"))
    try:
        r = other.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["3.1"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303

        # No Share yet -- the signed-in test user must not see the other user's lab panel.
        t_before = _text(client.get("/measurements?tab=labs"))
        assert "3.1" not in t_before

        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        # Now visible, tagged with the owner's name.
        t_after = _text(client.get("/measurements?tab=labs"))
        assert "3.1" in t_after
        assert "LabsSharePartner" in t_after
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_lab_panels(me, other_id)


def test_labs_tab_loads_with_empty_state(client, db):
    r = client.get("/measurements?tab=labs")
    assert r.status_code == 200
    assert "No lab panels yet" in r.text or "New Panel" in r.text
