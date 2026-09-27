# Labs & Medical Results Design (Roadmap Phase 6, part 3 of 3)

**Goal:** the third and final Phase 6 sub-project. Bulk entry of blood-marker results per lab
draw/panel, each result carrying its own user-entered reference range (ranges vary by lab), an
optional PDF/image attachment of the actual report, per-marker trend charts with a range selector,
and a query-time view of which protocol(s) were active around each draw date. This completes the
Labs tab on the Weight & Measurements page (currently the last remaining placeholder).

**Deferred, explicitly out of scope for this spec:** a built-in "standard" reference range per
marker (every range is user-entered, since it varies by lab); anything beyond one optional file per
panel (no multi-file galleries); automatic PDF parsing/OCR of lab values (typed entry only).

## Data model

```python
class LabMarker(str, enum.Enum):
    TOTAL_TESTOSTERONE = "Total Testosterone"
    FREE_TESTOSTERONE = "Free Testosterone"
    ESTRADIOL = "Estradiol"
    LH = "LH"
    FSH = "FSH"
    SHBG = "SHBG"
    PROLACTIN = "Prolactin"
    IGF_1 = "IGF-1"
    CORTISOL = "Cortisol"
    FASTING_GLUCOSE = "Fasting Glucose"
    HBA1C = "HbA1c"
    FASTING_INSULIN = "Fasting Insulin"
    TOTAL_CHOLESTEROL = "Total Cholesterol"
    LDL = "LDL"
    HDL = "HDL"
    TRIGLYCERIDES = "Triglycerides"
    TSH = "TSH"
    FREE_T3 = "Free T3"
    FREE_T4 = "Free T4"
    ALT = "ALT"
    AST = "AST"
    CREATININE = "Creatinine"
    EGFR = "eGFR"
    BUN = "BUN"
    HEMOGLOBIN = "Hemoglobin"
    HEMATOCRIT = "Hematocrit"
    WBC = "WBC"
    PLATELETS = "Platelets"
    HS_CRP = "hs-CRP"
    VITAMIN_D = "Vitamin D"
    FERRITIN = "Ferritin"
    OTHER = "Other"
```

`LabPanel` — one row per blood draw/lab visit:
```python
class LabPanel(Base):
    """One draw/visit's worth of results, optionally with the lab's own report attached."""
    __tablename__ = "lab_panels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    drawn_at: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    report_filename: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    results: Mapped[list["LabResult"]] = relationship(back_populates="panel", cascade="all, delete-orphan")
```

`LabResult` — one marker's value within a panel:
```python
class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (
        CheckConstraint("range_low IS NULL OR range_high IS NULL OR range_low <= range_high",
                        name="ck_lab_result_range_order"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("lab_panels.id", ondelete="CASCADE"), index=True)
    marker: Mapped[LabMarker] = mapped_column(_enum_column(LabMarker))
    marker_other: Mapped[str | None] = mapped_column(String(80))  # set only when marker == OTHER
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    range_low: Mapped[float | None] = mapped_column(Float)
    range_high: Mapped[float | None] = mapped_column(Float)

    panel: Mapped["LabPanel"] = relationship(back_populates="results")
```

"Out of range" is never stored — computed at display time by comparing `value` against
`range_low`/`range_high` whenever both are present on that result.

File storage reuses `app/uploads.py`'s existing COA convention exactly (same `ALLOWED_TYPES`
image+PDF set, same content-sniffing validation) via a parallel `save_lab_report`/
`lab_report_path`/`lab_report_media_type`/`delete_lab_report` set of functions — no Word-document
support (unlike vendor price lists), since a lab report is always a PDF or a scanned/photographed
image, never a Word doc.

Sharing: `LabPanel` (and its `results`) fold into the existing `ShareCategory.PERSONAL_DATA`
category — the same one Weight & Measurements and Journal already use.

## Bulk entry form

One form per panel: a draw date (defaults to today, editable — unlike Journal, a lab result is
often entered days after the actual draw, so back-dating is expected and allowed here), an optional
file attachment, free-text notes, and a repeatable row of (marker dropdown + value + unit + low/high
range) — the user adds as many rows as their panel has markers, in one sitting. Selecting `Other` on
a row reveals a free-text `marker_other` field for that row. At least one result row is required;
value is required per row, unit and range are optional per row.

## Charts + protocol overlay

One hand-drawn SVG trend chart per marker that has 2+ data points across all of the user's panels
(own entries only, matching Weight & Measurements' own established chart-scoping rule — a sharing
partner's results never mix into your own trend line), with the same 7-day/14-day/1-month/3-month/
6-month/1-year/lifetime range selector already established. Each plotted point's reference range
(when present) renders as a shaded band behind the line so an out-of-range point is visually
obvious without needing a separate legend.

Below the panel list (and inside each panel's own detail view), a "what was active" section shows
which protocol(s)/peptide(s) had a scheduled dose on that panel's `drawn_at` date — a query-time
join against `DoseLog` by date, mirroring Journal's own dose-linking (no stored relationship,
respects the same dose-visibility scoping already used elsewhere in this app).

## Page layout

The Labs tab (currently the last placeholder card on the Weight & Measurements page) becomes real:
a "New Panel" button opening the bulk-entry dialog (matching Journal's established `<dialog>`
convention), a reverse-chronological list of past panels (date, notes, a link to the attached report
if present, each result's marker/value/unit/range with an out-of-range flag), and the per-marker
trend-chart section below the list. Panels shared via `PERSONAL_DATA` appear in the same list,
tagged with the owner's name, matching the display convention already established by Weight &
Measurements and Journal.

## Review focus

1. A result row's "out of range" flag must only ever be computed from that SPECIFIC result's own
   `range_low`/`range_high` — never from a different marker's range, and never shown at all when
   either bound is missing (a result with no range entered is neither flagged in-range nor
   out-of-range).
2. `marker_other` must be required when (and only required when) `marker == LabMarker.OTHER` — a
   non-`OTHER` row with `marker_other` set, or an `OTHER` row with it blank, must be rejected with a
   clear error, never silently accepted.
3. Trend charts must never mix a sharing partner's results into the signed-in user's own chart
   series (same class of bug the Weight & Measurements final review caught and fixed for its own
   charts) — each marker's trend line is built from the viewer's own panels only.
4. The uploaded-report validation must reject a disguised file the same way COA uploads already do
   (content-sniffed against its claimed extension, not just trusting the filename/extension) —
   reuse `app/uploads.py`'s existing sniffing logic rather than re-implementing a weaker check.
5. The "what was active" protocol lookup must scope to each panel's own `drawn_at` date when
   browsing multiple past panels in the list view — not always "today's" active protocols (the same
   failure mode Journal's final review caught for its own dose-linking).
