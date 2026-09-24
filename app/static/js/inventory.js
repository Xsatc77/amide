// Inventory page: the "+" add button, edit buttons, and the item dialog.
(() => {
  const dialog = document.getElementById("item-dialog");
  const form = dialog.querySelector("form");
  const title = dialog.querySelector("[data-title]");
  const coaExisting = dialog.querySelector("[data-coa-existing]");
  const coaInput = dialog.querySelector('input[name="coa"]');
  const preview = dialog.querySelector("[data-coa-preview]");
  const fields = [
    "name", "count", "vial_size_mg", "vial_size_unit", "medium", "volume_ml", "units_per_package",
    "expiration_date", "storage", "cost", "vendor",
    "lot_number", "order_date", "shipped_date", "arrival_date",
    "coa_vial_size_mg", "coa_purity_pct", "notes",
  ];
  const rules = JSON.parse(document.getElementById("inv-rules").textContent);
  const mediumSelect = form.elements.medium;
  const fieldWrappers = {
    vial_size_mg: form.querySelector('[data-field="vial_size_mg"]'),
    volume_ml: form.querySelector('[data-field="volume_ml"]'),
    units_per_package: form.querySelector('[data-field="units_per_package"]'),
  };

  function syncMediumFields() {
    const medium = mediumSelect.value;
    const rule = rules[medium] || { required: [], labels: {} };
    // Volume and units-per-package only matter once a medium says so; Amount is always shown.
    fieldWrappers.volume_ml.hidden = !rule.required.includes("volume_ml");
    fieldWrappers.units_per_package.hidden = !rule.required.includes("units_per_package");
    for (const [field, wrapper] of Object.entries(fieldWrappers)) {
      const input = wrapper.querySelector("input");
      input.required = rule.required.includes(field);
      const labelEl = wrapper.querySelector(`[data-label-for="${field}"]`);
      if (labelEl && rule.labels[field]) labelEl.textContent = rule.labels[field];
    }
  }
  mediumSelect.addEventListener("change", syncMediumFields);

  function clearErrors() {
    dialog.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
    dialog.querySelectorAll(".error, .alert").forEach((el) => el.remove());
  }

  function resetPreview() {
    preview.hidden = true;
    if (preview.src) URL.revokeObjectURL(preview.src);
    preview.removeAttribute("src");
  }

  function openFor(item) {
    clearErrors();
    form.reset();
    resetPreview();
    form.action = item ? `/inventory/${item.id}` : "/inventory";
    title.textContent = item ? "Edit item" : "New inventory item";
    for (const f of fields) {
      form.elements[f].value = item ? item[f] ?? "" : f === "count" ? "1" : f === "vial_size_unit" ? "mg" : "";
    }
    coaExisting.hidden = !(item && item.has_coa);
    syncMediumFields();
    dialog.showModal();
    form.elements.name.focus();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "add") openFor(null);
    else if (action === "edit") openFor(JSON.parse(btn.dataset.item));
    else if (action === "close") dialog.close();
  });

  // Close when clicking the backdrop.
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  coaInput.addEventListener("change", () => {
    resetPreview();
    const file = coaInput.files[0];
    if (file && file.type.startsWith("image/") && !/hei[cf]/i.test(file.type)) {
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
    }
  });

  document.querySelectorAll("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    })
  );

  // Server re-rendered the page with validation errors: reopen the dialog as-is.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();
