import html
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LabMarker, LabPanel, LabResult, Share, ShareCategory, User

from tests.test_journal import _seed_dose_log
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


def test_nan_value_is_rejected_with_422_not_a_500(client, db):
    """`float("nan")` passes a bare try/except float() parse (it's valid float syntax), but would
    then reach SQLite's NOT NULL `value` column as an effective NULL, raising an unhandled
    IntegrityError -- a 500, not a normal validation error."""
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["nan"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


def test_inf_value_is_rejected_not_silently_stored(client, db):
    """`float("inf")` (and any magnitude beyond float64, which Python's parser silently coerces to
    inf, e.g. `1e309`) must not be silently accepted -- it would store fine but produce nan/inf
    chart coordinates downstream."""
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["inf"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


def test_huge_range_bound_that_parses_to_inf_is_rejected(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": ["1e309"], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


def test_stale_marker_other_error_uses_the_indexed_key_convention(client, db):
    """Reproduces the server-side symptom of the stale-hidden-field bug: marker is switched to a
    non-Other value but marker_other[] still carries text (as would happen if the frontend failed
    to clear it after a marker change). The existing 422 rejection must still fire, and the error
    must land under the same indexed key convention (`marker_other_{i}`) the frontend's
    fieldByErrorKey map already knows how to locate -- not some other key it can't find."""
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": ["Stale Custom Name"],
        })
        assert r.status_code == 422
        t = html.unescape(r.text)
        error_data = t.split('id="lab-error-data">', 1)[1].split("</script>", 1)[0]
        assert '"marker_other_0"' in error_data
    finally:
        _clear_lab_panels(me)


def test_marker_other_over_length_is_rejected(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["OTHER"], "value[]": ["1.2"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": ["x" * 81],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


def test_unit_over_length_is_rejected(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": ["x" * 21],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


def test_notes_over_length_is_rejected(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28", "notes": "x" * 2001,
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
    finally:
        _clear_lab_panels(me)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_invalid_row_with_a_valid_file_upload_leaves_no_orphaned_file(client, db):
    """The file must only be written to disk after all row/field validation has passed -- writing
    it any earlier would leave an orphaned, unreferenced file on disk whenever a 422 fires for some
    other reason."""
    from app import config

    me = _tester_id()
    before = set(config.LAB_REPORT_DIR.iterdir()) if config.LAB_REPORT_DIR.exists() else set()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["nan"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, files={"report": ("report.png", PNG_BYTES, "image/png")})
        assert r.status_code == 422
        after = set(config.LAB_REPORT_DIR.iterdir()) if config.LAB_REPORT_DIR.exists() else set()
        assert after == before
        with SessionLocal() as s:
            assert s.query(LabPanel).filter_by(owner_id=me).count() == 0
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


def test_panel_shows_active_protocols_on_its_own_draw_date(client, db):
    me = _tester_id()
    draw_date = date(2026, 9, 20)
    _seed_dose_log(me, "LabProtocolPeptide", draw_date)
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-20",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303
        t = _text(client.get("/measurements?tab=labs"))
        assert "LabProtocolPeptide" in t
    finally:
        _clear_lab_panels(me)


def test_past_panel_shows_that_days_protocols_not_todays(client, db):
    me = _tester_id()
    past_date = date(2026, 9, 20)
    _seed_dose_log(me, "PastLabPeptide", past_date)
    _seed_dose_log(me, "TodayOnlyLabPeptide", date.today())
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-20",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303
        t = _text(client.get("/measurements?tab=labs"))
        # Scope to the panel list -- the "New Panel" dialog above it is unrelated to this assertion.
        panels_section = t.split('id="labs-panels-heading"', 1)[1]
        assert "PastLabPeptide" in panels_section
        assert "TodayOnlyLabPeptide" not in panels_section
    finally:
        _clear_lab_panels(me)


def test_shared_panel_active_protocols_use_the_owners_doses_not_the_viewers(client, db):
    """A shared panel's `active_protocols` must be built from the PANEL OWNER's own DoseLog rows on
    that panel's `drawn_at` -- never the viewer's, even when the viewer happens to have logged a
    dose of their own on that exact same date. Direct evidence via `labs_tab_context`, not just
    code-reading: the viewer (tester) and the sharing partner each get their own DoseLog seeded on
    the panel's draw date, and only the partner's peptide may appear on the partner's panel."""
    me = _tester_id()
    other = _logged_in_client("LabsProtocolSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "labsprotocolsharepartner"))
    draw_date = date(2026, 9, 20)
    _seed_dose_log(me, "ViewerOwnPeptide", draw_date)
    _seed_dose_log(other_id, "PartnerPeptide", draw_date)
    try:
        r = other.post("/labs/panels", data={
            "drawn_at": "2026-09-20",
            "marker[]": ["TSH"], "value[]": ["3.1"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303

        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        from app.routers.labs import labs_tab_context
        with SessionLocal() as s:
            ctx = labs_tab_context(s, me, "lifetime", None)
            shared_panel = next(p for p in ctx["panels"] if p["owner_name"] == "LabsProtocolSharePartner")
            peptide_names = {d["peptide_name"] for d in shared_panel["active_protocols"]}
            assert peptide_names == {"PartnerPeptide"}
            assert "ViewerOwnPeptide" not in peptide_names
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_lab_panels(me, other_id)


def test_blank_rows_in_a_bulk_sheet_are_silently_skipped(client, db):
    """The sheet now ships with many blank lines by default -- an unfilled line must never be an
    error, only a row that has a marker but no value would previously have been."""
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH", "FASTING_GLUCOSE", "LDL"],
            "value[]": ["2.5", "", ""],
            "unit[]": ["mIU/L", "", ""],
            "range_low[]": ["", "", ""], "range_high[]": ["", "", ""],
            "marker_other[]": ["", "", ""],
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me_user.id)).all()
            [result] = panel.results  # only the one filled-in row was saved
            assert result.marker == LabMarker.TSH
    finally:
        _clear_lab_panels(me)


def test_all_blank_rows_is_a_422_not_a_silent_empty_panel(client, db):
    me = _tester_id()
    try:
        r = client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH", "FASTING_GLUCOSE"], "value[]": ["", ""], "unit[]": ["", ""],
            "range_low[]": ["", ""], "range_high[]": ["", ""], "marker_other[]": ["", ""],
        })
        assert r.status_code == 422
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            assert s.scalars(select(LabPanel).where(LabPanel.owner_id == me_user.id)).all() == []
    finally:
        _clear_lab_panels(me)


def test_edit_route_replaces_an_existing_panels_results(client, db):
    me = _tester_id()
    try:
        client.post("/labs/panels", data={
            "drawn_at": "2026-09-20",
            "marker[]": ["TSH"], "value[]": ["2.0"], "unit[]": ["mIU/L"],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me_user.id)).all()
            panel_id = panel.id

        r = client.post(f"/labs/panels/{panel_id}", data={
            "drawn_at": "2026-09-21",
            "marker[]": ["FASTING_GLUCOSE"], "value[]": ["95"], "unit[]": ["mg/dL"],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        }, follow_redirects=False)
        assert r.status_code == 303

        with SessionLocal() as s:
            edited = s.get(LabPanel, panel_id)
            assert edited.drawn_at == date(2026, 9, 21)
            [result] = edited.results
            assert result.marker == LabMarker.FASTING_GLUCOSE and result.value == 95.0
    finally:
        _clear_lab_panels(me)


def test_edit_route_rejects_another_users_panel(client, db):
    me = _tester_id()
    other = _logged_in_client("labseditother")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "labseditother"))
    try:
        other.post("/labs/panels", data={
            "drawn_at": "2026-09-20",
            "marker[]": ["TSH"], "value[]": ["2.0"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        with SessionLocal() as s:
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == other_id)).all()
            panel_id = panel.id

        r = client.post(f"/labs/panels/{panel_id}", data={
            "drawn_at": "2026-09-21",
            "marker[]": ["FASTING_GLUCOSE"], "value[]": ["95"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        assert r.status_code == 404
    finally:
        _clear_lab_panels(me, other_id)


def test_edit_button_and_picker_only_appear_once_a_panel_exists(client, db):
    me = _tester_id()
    try:
        t = _text(client.get("/measurements?tab=labs"))
        assert 'data-action="open-edit-panel"' not in t
        assert 'id="lab-edit-select"' not in t

        client.post("/labs/panels", data={
            "drawn_at": "2026-09-28",
            "marker[]": ["TSH"], "value[]": ["2.5"], "unit[]": [""],
            "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""],
        })
        t2 = _text(client.get("/measurements?tab=labs"))
        assert 'data-action="open-edit-panel"' in t2
        assert 'id="lab-edit-select"' in t2
        assert '"marker": "TSH"' in t2  # the raw edit-data JSON carries the enum name, not the label
    finally:
        _clear_lab_panels(me)
