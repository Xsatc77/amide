// Labs tab (inside Weight & Measurements): the "New Panel" dialog open/close wiring and the
// repeatable result-row section, mirroring inventory.js's addLine/data-action="add-line" pattern.
(() => {
  const dialog = document.getElementById("lab-dialog");
  if (!dialog) return;  // only present on the Labs tab
  const form = dialog.querySelector("form");
  const rowsContainer = dialog.querySelector("[data-lab-rows-container]");
  const template = document.getElementById("lab-row-template");

  function clearRowErrors(row) {
    row.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
    row.querySelectorAll("small.error").forEach((el) => el.remove());
  }

  function wireRow(row) {
    const select = row.querySelector("[data-lab-marker-select]");
    const otherField = row.querySelector("[data-lab-marker-other]");
    function syncOther() {
      otherField.hidden = select.value !== "OTHER";
    }
    select.addEventListener("change", syncOther);
    syncOther();
    row.querySelector('[data-action="remove-lab-row"]').addEventListener("click", () => {
      // Always leave at least one row behind so the form never submits with zero rows.
      if (rowsContainer.querySelectorAll("[data-lab-row]").length > 1) row.remove();
    });
  }

  function addRow() {
    const fragment = template.content.cloneNode(true);
    rowsContainer.appendChild(fragment);
    const row = rowsContainer.lastElementChild;
    wireRow(row);
    return row;
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
    row.querySelector('[name="value[]"]').value = rowData.value || "";
    row.querySelector('[name="unit[]"]').value = rowData.unit || "";
    row.querySelector('[name="range_low[]"]').value = rowData.range_low || "";
    row.querySelector('[name="range_high[]"]').value = rowData.range_high || "";

    const fieldByErrorKey = {
      marker: 'select[name="marker[]"]',
      marker_other: '[name="marker_other[]"]',
      value: '[name="value[]"]',
      range_low: '[name="range_low[]"]',
      range_high: '[name="range_high[]"]',
      range: '[name="range_low[]"]',  // a low/high mismatch is flagged on the low field
    };
    for (const [key, selector] of Object.entries(fieldByErrorKey)) {
      const message = rowErrors[key];
      if (!message) continue;
      const el = row.querySelector(selector);
      if (!el) continue;
      el.closest("label")?.classList.add("has-error");
      const small = document.createElement("small");
      small.className = "error";
      small.textContent = message;
      el.insertAdjacentElement("afterend", small);
    }
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "open-lab-panel") {
      form.reset();
      rowsContainer.innerHTML = "";
      addRow();
      dialog.showModal();
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
  if (dialog.hasAttribute("data-open-on-load")) {
    const data = JSON.parse(document.getElementById("lab-error-data").textContent || "{}");
    form.elements["drawn_at"].value = data.drawn_at || "";
    form.elements["notes"].value = data.notes || "";

    rowsContainer.innerHTML = "";
    const rows = data.rows && data.rows.length ? data.rows : [{}];
    const rowErrors = data.errors || {};
    rows.forEach((rowData, i) => {
      const row = addRow();
      clearRowErrors(row);
      fillRow(row, rowData, {
        marker: rowErrors[`marker_${i}`],
        marker_other: rowErrors[`marker_other_${i}`],
        value: rowErrors[`value_${i}`],
        range_low: rowErrors[`range_low_${i}`],
        range_high: rowErrors[`range_high_${i}`],
        range: rowErrors[`range_${i}`],
      });
    });
    dialog.showModal();
  }
})();
