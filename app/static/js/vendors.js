// Vendor detail: edit dialog + repeatable contact-method rows.
(() => {
  const dialog = document.getElementById("vendor-edit-dialog");
  if (!dialog) return;  // Vendors list page has no edit dialog
  const rowsContainer = dialog.querySelector("[data-contact-rows]");
  const template = document.getElementById("contact-row-template");
  let rowCount = rowsContainer.querySelectorAll("[data-contact-row]").length;

  function syncNewMethodType(row) {
    const select = row.querySelector('select[name$="-method_type_id"]');
    const newField = row.querySelector("[data-new-method-type]");
    newField.hidden = select.value !== "__new__";
  }

  function wireRow(row) {
    const select = row.querySelector('select[name$="-method_type_id"]');
    select.addEventListener("change", () => syncNewMethodType(row));
    syncNewMethodType(row);
    row.querySelector('[data-action="remove-contact-row"]').addEventListener("click", () => row.remove());
  }

  rowsContainer.querySelectorAll("[data-contact-row]").forEach(wireRow);

  function addRow() {
    const index = rowCount++;
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(index));
    });
    const row = fragment.querySelector("[data-contact-row]");
    rowsContainer.appendChild(fragment);
    wireRow(row);
  }

  dialog.querySelector('[data-action="add-contact-row"]').addEventListener("click", addRow);

  document.querySelectorAll('[data-action="edit-vendor"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.showModal()));
  dialog.querySelectorAll('[data-action="close-vendor-edit"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  // Close only when BOTH the press and release land on the backdrop itself -- a `click` event's
  // target is the dialog element whenever the mouseup lands on the backdrop, even if the mousedown
  // that started a text-selection drag began inside a field. Checking mousedown too means a drag
  // that starts inside the form and ends outside it no longer closes the dialog.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: values are already server-rendered from
  // `form`, so just reopen as-is.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();
