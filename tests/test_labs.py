import html
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LabMarker, LabPanel, LabResult, Share, ShareCategory, User

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


def test_validation_error_preserves_the_other_posted_rows(client, db):
    """A bulk-entry form must not discard every already-correct row just because one other row
    failed -- the whole point of a bulk form is entering many rows in one sitting. Two valid rows
    plus one invalid row (a non-"Other" marker illegally carrying marker_other text) should still
    surface the two valid rows' posted values in the re-rendered page, not a blank form."""
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH", "HDL", "LDL"],
            "value[]": ["2.5", "55", "130"],
            "unit[]": ["mIU/L", "mg/dL", "mg/dL"],
            "range_low[]": ["0.5", "", ""],
            "range_high[]": ["4.5", "", ""],
            "marker_other[]": ["", "", "Not allowed here"],  # LDL row is invalid
        })
        assert r.status_code == 422
        t = html.unescape(r.text)
        assert 'id="lab-error-data"' in t
        error_data = t.split('id="lab-error-data">', 1)[1].split("</script>", 1)[0]
        # The two valid rows' posted values must round-trip into the error-data blob the client
        # rebuilds the dialog from, not just the invalid row / a blank form.
        assert '"TSH"' in error_data and '"2.5"' in error_data
        assert '"HDL"' in error_data and '"55"' in error_data
        assert '"Not allowed here"' in error_data  # the invalid row's own posted text, too
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


def test_marker_chart_only_appears_with_2_or_more_points(client, db):
    me = _tester_id()
    try:
        client.post("/labs/panels", data={
            "drawn_at": "2026-09-20", "marker[]": ["TSH"], "value[]": ["2.0"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        t = client.get("/measurements?tab=labs&range=lifetime").text
        assert "TSH" in t  # the single result still shows in the panel list
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            from app.routers.labs import labs_tab_context
            ctx = labs_tab_context(s, me_user.id, "lifetime", None)
            assert ctx["lab_charts"]["series"] == []  # not yet -- only one point so far

        client.post("/labs/panels", data={
            "drawn_at": "2026-09-27", "marker[]": ["TSH"], "value[]": ["2.4"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            from app.routers.labs import labs_tab_context
            ctx = labs_tab_context(s, me_user.id, "lifetime", None)
            labels = [c["marker_label"] for c in ctx["lab_charts"]["series"]]
            assert "TSH" in labels
    finally:
        _clear_lab_panels(me)


def test_marker_chart_does_not_mix_a_sharing_partners_results(client, db):
    me = _tester_id()
    other = _logged_in_client("LabsChartSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "labschartsharepartner"))
    try:
        client.post("/labs/panels", data={
            "drawn_at": "2026-09-20", "marker[]": ["TSH"], "value[]": ["2.0"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        client.post("/labs/panels", data={
            "drawn_at": "2026-09-27", "marker[]": ["TSH"], "value[]": ["2.4"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        other.post("/labs/panels", data={
            "drawn_at": "2026-09-25", "marker[]": ["TSH"], "value[]": ["999.9"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        r = client.get("/measurements?tab=labs&range=lifetime")
        assert r.status_code == 200
        assert "999.9" in r.text  # visible in the shared panel list

        from app.routers.labs import labs_tab_context
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            ctx = labs_tab_context(s, me_user.id, "lifetime", None)
            tsh_chart = next(c for c in ctx["lab_charts"]["series"] if c["marker_label"] == "TSH")
            # Only the viewer's own 2 points are plotted -- the partner's 999.9 would have both
            # added a 3rd point and wildly skewed the value scale had it been mixed in.
            assert len(tsh_chart["chart"]["points"]) == 2
            assert tsh_chart["chart"]["max_v"] < 100
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_lab_panels(me, other_id)


def test_chart_range_selector_filters_points(client, db):
    old = date(2026, 1, 1)
    me = _tester_id()
    try:
        with SessionLocal() as s:
            user = s.scalar(select(User).where(User.username_key == "tester"))
            panel = LabPanel(owner_id=user.id, drawn_at=old, created_at=old)
            panel.results.append(LabResult(marker=LabMarker.TSH, value=1.0))
            s.add(panel)
            s.commit()
        client.post("/labs/panels", data={
            "drawn_at": "2026-09-28", "marker[]": ["TSH"], "value[]": ["2.4"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })

        from app.routers.labs import labs_tab_context
        with SessionLocal() as s:
            user = s.scalar(select(User).where(User.username_key == "tester"))
            ctx_lifetime = labs_tab_context(s, user.id, "lifetime", None)
            tsh_lifetime = next(c for c in ctx_lifetime["lab_charts"]["series"]
                                if c["marker_label"] == "TSH")
            assert tsh_lifetime["chart"]["min_v"] == 1.0

            ctx_7d = labs_tab_context(s, user.id, "7d", date(2026, 9, 21))
            # The old (2026-01-01) point falls outside the 7-day window, so TSH now has fewer
            # than 2 points in range and its chart disappears entirely.
            labels_7d = [c["marker_label"] for c in ctx_7d["lab_charts"]["series"]]
            assert "TSH" not in labels_7d
    finally:
        _clear_lab_panels(me)
