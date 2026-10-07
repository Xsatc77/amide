// Labs tab (inside Weight & Measurements): the New Panel / Edit Panel dialog (shared between both
// flows) and its bulk-entry sheet of repeatable result rows -- mirrors inventory.js's
// addLine/data-action="add-line" pattern, extended to a compact <table> row (Marker/Value/Unit/
// High/Low) and to pre-filling 25 blank lines by default, per the owner's own "bulk entry sheet"
// request.
(() => {
  const dialog = document.getElementById("lab-dialog");
  if (!dialog) return;  // only present on the Labs tab
  const form = dialog.querySelector("form");
  const rowsContainer = dialog.querySelector("[data-lab-rows-container]");
  const template = document.getElementById("lab-row-template");
  const titleEl = dialog.querySelector("[data-lab-dialog-title]");
  const OTHER_BLANK_ROWS = 3;

  function clearRowErrors(row) {
    row.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
    row.querySelectorAll("small.error").forEach((el) => el.remove());
  }

  function wireRow(row) {
    const select = row.querySelector("[data-lab-marker-select]");
    const otherInput = row.querySelector("[data-lab-marker-other]");
    function syncOther() {
      const isOther = select.value === "OTHER";
      otherInput.hidden = !isOther;
      // Clear any stale text left over from a previous "Other" selection when the marker changes
      // away from it -- otherwise the hidden field's old value would still be posted with the row
      // (rejected server-side with no visible indication why, since the field itself is now
      // hidden) every time the form is resubmitted. Never *disable* the input instead: a disabled
      // input isn't submitted at all, which would break the row-array alignment between
      // marker[]/value[]/etc. -- every row must always submit all its fields, blank or not.
      if (!isOther) otherInput.value = "";
    }
    select.addEventListener("change", syncOther);
    syncOther();
    row.querySelector('[data-action="remove-lab-row"]').addEventListener("click", () => row.remove());
  }

  function addRow() {
    const fragment = template.content.cloneNode(true);
    rowsContainer.appendChild(fragment);
    const row = rowsContainer.lastElementChild;
    wireRow(row);
    return row;
  }

  function addBlankRows(n) {
    for (let i = 0; i < n; i++) addRow();
  }

  // One line for every marker in the list (none twice), then a few blank lines for markers that are not listed: type the
  // name under "Other", or use "Add marker" for more.
  function addMarkerRows() {
    const present = new Set([...rowsContainer.querySelectorAll("[data-lab-marker-select]")].map((s) => s.value));
    const options = [...template.content.querySelectorAll("[data-lab-marker-select] option")].map((o) => o.value).filter((v) => v && v !== "OTHER");
    for (const marker of options) {
      if (present.has(marker)) continue;
      const row = addRow();
      row.querySelector("[data-lab-marker-select]").value = marker;
    }
    addBlankRows(OTHER_BLANK_ROWS);
  }

  // Fills one row's fields from a posted-row object (raw strings, as posted) and marks any of
  // that row's per-field errors -- mirrors inventory.js's checkin-dialog/new-order-dialog
  // reopen-on-error replay (buildLines / the data-open-on-load block), adapted to Labs's flat
  // marker[]/value[]/... array fields instead of indexed lines-{i}-field names.
  function fillRow(row, rowData, rowErrors) {
    const select = row.querySelector("[data-lab-marker-select]");
    if (rowData.marker) select.value = rowData.marker;
    select.dispatchEvent(new Event("change", { bubbles: true }));  // syncs the Other-field visibility
    row.querySelector('[name="marker_other[]"]').value = rowData.marker_other || "";
    row.querySelector('[name="value[]"]').value = rowData.value ?? "";
    row.querySelector('[name="unit[]"]').value = rowData.unit || "";
    row.querySelector('[name="range_low[]"]').value = rowData.range_low ?? "";
    row.querySelector('[name="range_high[]"]').value = rowData.range_high ?? "";

    if (!rowErrors) return;
    const fieldByErrorKey = {
      marker: 'select[name="marker[]"]',
      marker_other: '[name="marker_other[]"]',
      value: '[name="value[]"]',
      unit: '[name="unit[]"]',
      range_low: '[name="range_low[]"]',
      range_high: '[name="range_high[]"]',
      range: '[name="range_low[]"]',  // a low/high mismatch is flagged on the low field
    };
    const otherInput = row.querySelector("[data-lab-marker-other]");
    for (const [key, selector] of Object.entries(fieldByErrorKey)) {
      const message = rowErrors[key];
      if (!message) continue;
      // marker_other's own field can be hidden (its marker isn't "Other") -- syncOther above
      // just set its *current* visibility from the row's current marker. An error inside a
      // hidden element is never seen by the user, so fall back to the visible marker <select>.
      const targetSelector = (key === "marker_other" && otherInput.hidden) ? fieldByErrorKey.marker : selector;
      const el = row.querySelector(targetSelector);
      if (!el) continue;
      el.classList.add("has-error");
      const small = document.createElement("small");
      small.className = "error";
      small.textContent = message;
      el.insertAdjacentElement("afterend", small);
    }
  }

  function resetForNew() {
    form.reset();
    form.action = "/labs/panels";
    delete form.dataset.labPanelId;
    if (titleEl) titleEl.textContent = "New lab panel";
    rowsContainer.innerHTML = "";
    addMarkerRows();
  }

  function openForEdit(panelId) {
    const data = JSON.parse(document.getElementById("lab-panels-edit-data").textContent || "[]");
    const panel = data.find((p) => String(p.id) === String(panelId));
    if (!panel) return;
    form.reset();
    form.action = `/labs/panels/${panel.id}`;
    form.dataset.labPanelId = panel.id;
    if (titleEl) titleEl.textContent = "Edit lab panel";
    form.elements["drawn_at"].value = panel.drawn_at || "";
    form.elements["notes"].value = panel.notes || "";
    rowsContainer.innerHTML = "";
    panel.rows.forEach((rowData) => fillRow(addRow(), rowData));
    // Still the full sheet while editing: a line for every marker not already in the panel, so new results can be added alongside.
    addMarkerRows();
    dialog.showModal();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "open-lab-panel") {
      resetForNew();
      dialog.showModal();
    } else if (action === "open-edit-panel") {
      const select = document.getElementById("lab-edit-select");
      if (select && select.value) openForEdit(select.value);
    } else if (action === "close" && dialog.contains(btn)) {
      dialog.close();
    } else if (action === "add-lab-row") {
      addRow();
    }
  });

  // See journal.js's/inventory.js's identical backdrop-close comment: mousedown is tracked too so
  // a text-selection drag that starts inside the form and ends on the backdrop doesn't close it.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: rebuild every posted row (not just a
  // blank one) and each row's own field error(s) from lab-error-data, then reopen -- a bulk-entry
  // form must not force the user to retype already-correct rows just because one other row failed.
  // Works for both New and Edit: lab-error-data carries the panel id the form was posted to, if any.
  if (dialog.hasAttribute("data-open-on-load")) {
    const data = JSON.parse(document.getElementById("lab-error-data").textContent || "{}");
    if (data.id) {
      form.action = `/labs/panels/${data.id}`;
      form.dataset.labPanelId = data.id;
      if (titleEl) titleEl.textContent = "Edit lab panel";
    }
    form.elements["drawn_at"].value = data.drawn_at || "";
    form.elements["notes"].value = data.notes || "";

    rowsContainer.innerHTML = "";
    const rows = data.rows && data.rows.length ? data.rows : [];
    const rowErrors = data.errors || {};
    rows.forEach((rowData, i) => {
      const row = addRow();
      clearRowErrors(row);
      fillRow(row, rowData, {
        marker: rowErrors[`marker_${i}`],
        marker_other: rowErrors[`marker_other_${i}`],
        value: rowErrors[`value_${i}`],
        unit: rowErrors[`unit_${i}`],
        range_low: rowErrors[`range_low_${i}`],
        range_high: rowErrors[`range_high_${i}`],
        range: rowErrors[`range_${i}`],
      });
    });
    addMarkerRows();
    dialog.showModal();
  }
})();
