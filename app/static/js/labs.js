// Labs tab (inside Weight & Measurements): the "New Panel" dialog open/close wiring and the
// repeatable result-row section, mirroring inventory.js's addLine/data-action="add-line" pattern.
(() => {
  const dialog = document.getElementById("lab-dialog");
  if (!dialog) return;  // only present on the Labs tab
  const rowsContainer = dialog.querySelector("[data-lab-rows-container]");
  const template = document.getElementById("lab-row-template");

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
    wireRow(rowsContainer.lastElementChild);
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "open-lab-panel") {
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

  // Server re-rendered the page after a validation error: the dialog's own `open` attribute
  // (set server-side, same as journal-dialog's convention) already shows it -- just make sure it
  // has at least one row to submit, since the posted rows aren't reconstructed here (Task 2 keeps
  // this simple, matching the errors-dict summary shown above the fieldset).
  if (dialog.hasAttribute("open") && !rowsContainer.querySelector("[data-lab-row]")) {
    addRow();
  }
})();
