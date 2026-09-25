// Inventory page: the "+" add button, edit buttons, and the item dialog.
(() => {
  const dialog = document.getElementById("item-dialog");
  if (!dialog) return;  // Item detail page has no Add/Edit-item dialog (list.html only)
  const form = dialog.querySelector("form");
  const title = dialog.querySelector("[data-title]");
  const coaExisting = dialog.querySelector("[data-coa-existing]");
  const coaInput = dialog.querySelector('input[name="coa"]');
  const preview = dialog.querySelector("[data-coa-preview]");
  const fields = [
    "name", "category", "count", "vial_size_mg", "vial_size_unit", "medium", "volume_ml",
    "units_per_package", "storage", "cost", "vendor", "notes",
  ];
  const rules = JSON.parse(document.getElementById("inv-rules").textContent);
  const mediumSelect = form.elements.medium;
  const categoryRadios = form.querySelectorAll('input[name="category"]');
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

  function syncCategoryFields() {
    const category = form.querySelector('input[name="category"]:checked')?.value || "Medicine";
    const isEdit = dialog.dataset.mode === "edit";
    dialog.querySelector('[data-category-group="medicine"]').hidden = category === "Supply";
    dialog.querySelector('[data-category-group="supply"]').hidden = category !== "Supply";
    dialog.querySelectorAll('[data-category-group="order"]').forEach((el) => {
      el.hidden = category === "Supply" || isEdit;
    });
    dialog.querySelector('[data-category-group="category"]').hidden = isEdit;  // immutable once created
    dialog.querySelectorAll('[data-field-group="medium-only"]').forEach((el) => {
      el.hidden = category !== "Medicine";  // BAC Water has no medium-specific fields
    });
    if (category === "Medicine") syncMediumFields();
  }
  categoryRadios.forEach((r) => r.addEventListener("change", syncCategoryFields));

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
    dialog.dataset.mode = item ? "edit" : "add";
    for (const f of fields) {
      if (f === "category") continue;  // radios, set below
      form.elements[f].value = item ? item[f] ?? "" : f === "count" ? "1" : f === "vial_size_unit" ? "mg" : "";
    }
    const category = item ? item.category : "Medicine";
    form.querySelectorAll('input[name="category"]').forEach((r) => { r.checked = r.value === category; });
    if (coaExisting) coaExisting.hidden = true;  // COA lives per-order now (Task 5), never on this dialog
    syncCategoryFields();
    dialog.showModal();
    form.elements.name.focus();
  }

  form.addEventListener("submit", () => {
    // Only mirror when the Supply group is the one actually in use -- otherwise these (empty,
    // hidden) supply_* inputs would blank out the real name/cost/vendor/storage fields on every
    // Medicine/BAC Water submit.
    if (dialog.querySelector('[data-category-group="supply"]').hidden) return;
    form.querySelectorAll("[data-mirror]").forEach((el) => {
      form.elements[el.dataset.mirror].value = el.value;
    });
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "add") openFor(null);
    else if (action === "edit") openFor(JSON.parse(btn.dataset.item));
    else if (action === "close") dialog.close();
    else if (action === "reconstitute") {
      const itemId = btn.dataset.itemId;
      const existing = btn.dataset.activeVial ? JSON.parse(btn.dataset.activeVial) : null;
      const goToCalculator = () => { window.location.href = `/calculator?inventory_item_id=${itemId}`; };
      if (!existing) { goToCalculator(); return; }

      const checkDialog = document.getElementById("duplicate-vial-check");
      checkDialog.querySelector('[data-fill="item-name"]').textContent = btn.dataset.itemName;
      checkDialog.querySelector('[data-fill="existing-summary"]').textContent =
        `${existing.concentration.toFixed(2)} mg/mL, ${existing.doses} doses, discard by ${existing.discard_by}.`;
      checkDialog.querySelector('[data-action="continue-reconstitute"]').onclick = () => { checkDialog.close(); goToCalculator(); };
      checkDialog.querySelector('[data-action="cancel-reconstitute-check"]').onclick = () => checkDialog.close();
      checkDialog.showModal();
    }
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

  // Server re-rendered the page with validation errors: reopen the dialog as-is. Category
  // fields must be synced first, or every fieldset (Medicine/Supply/Order/COA) shows at once --
  // which also makes the submit-mirror logic run and clobber corrected fields with stale blanks.
  if (dialog.hasAttribute("data-open-on-load")) {
    syncCategoryFields();
    dialog.showModal();
  }
})();

// ---------------------------------------------------------------- active vials: expiry popups
(() => {
  // One popup per vial flagged by the server (data-expired-prompt), shown one at a time -- a
  // shared dialog reused via forEach would only leave the LAST card's handlers wired up.
  const queue = [...document.querySelectorAll("[data-expired-prompt]")];
  const dialog = document.getElementById("expiry-prompt");

  function showNext() {
    const card = queue.shift();
    if (!card) return;
    const vialId = card.dataset.expiredPrompt;
    dialog.querySelector('[data-fill="item-name"]').textContent = card.dataset.itemName || "This vial";
    dialog.querySelector('[data-action="expiry-discard"]').onclick = () => {
      dialog.close();
      fetch(`/active-vials/${vialId}/discard`, { method: "POST" }).then(() => {
        const itemId = card.dataset.itemIdForReconstitute;
        const again = document.getElementById("reconstitute-again-prompt");
        again.querySelector('[data-action="reconstitute-again-yes"]').onclick = () => {
          window.location.href = `/calculator?inventory_item_id=${itemId}`;
        };
        again.querySelector('[data-action="reconstitute-again-no"]').onclick = () => {
          again.close();
          if (queue.length) showNext();
          else window.location.href = "/inventory#active-vials";
        };
        again.showModal();
      });
    };
    dialog.querySelector('[data-action="expiry-not-yet"]').onclick = () => {
      dialog.close();
      fetch(`/active-vials/${vialId}/snooze-prompt`, { method: "POST" }).then(() => {
        if (queue.length) showNext();
        else window.location.reload();
      });
    };
    dialog.showModal();
  }
  showNext();

  // If we just redirected here after discarding via the Inventory-page popup flow, drop the query
  // param from the visible URL without a reload (it's already done its job).
  if (window.location.search.includes("just_discarded")) {
    window.history.replaceState({}, "", "/inventory#active-vials");
  }
})();

// ---------------------------------------------------------------- item detail: order dialog
(() => {
  const dialog = document.getElementById("order-dialog");
  if (!dialog) return;  // Supply items have no Order History section
  const form = dialog.querySelector("form");
  const orderFields = [
    "quantity", "order_date", "shipped_date", "arrival_date", "tracking_site", "tracking_number",
    "vendor", "lot_number", "cost", "tax", "shipping", "expiration_date",
    "coa_vial_size_mg", "coa_purity_pct",
  ];

  document.querySelectorAll('[data-action="add-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    form.action = window.location.pathname + "/orders";
    dialog.querySelector("[data-title]").textContent = "Add order";
    dialog.showModal();
  }));
  document.querySelectorAll('[data-action="edit-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    const order = JSON.parse(btn.dataset.order);
    for (const f of orderFields) {
      if (form.elements[f]) form.elements[f].value = order[f] ?? "";
    }
    form.action = `${window.location.pathname}/orders/${btn.dataset.orderId}`;
    dialog.querySelector("[data-title]").textContent = "Edit order";
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-order"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));

  // Server re-rendered the page after an order validation error: the form's action/title/values
  // are already server-rendered correctly (see detail.html) -- just reopen the dialog as-is.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();

// ---------------------------------------------------------------- item detail: edit-item dialog
(() => {
  const dialog = document.getElementById("item-edit-dialog");
  if (!dialog) return;  // Not the item owner, or not on the detail page
  const dataEl = document.getElementById("edit-item-data");
  const data = dataEl ? JSON.parse(dataEl.textContent) : null;
  const form = dialog.querySelector("form");
  const fields = ["name", "medium", "vial_size_mg", "vial_size_unit", "volume_ml",
    "units_per_package", "count", "cost", "vendor", "storage", "notes"];

  document.querySelectorAll('[data-action="edit-item"]').forEach((btn) => btn.addEventListener("click", () => {
    if (data) {
      for (const f of fields) {
        if (form.elements[f]) form.elements[f].value = data[f] ?? "";
      }
    }
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-item-edit"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: values are already server-rendered
  // from `form`, so just reopen as-is.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();

// ---------------------------------------------------------------- item detail: sale dialog
(() => {
  const dialog = document.getElementById("sale-dialog");
  if (!dialog) return;  // Supply items, or a non-owner viewer, have no Sold button/dialog
  const form = dialog.querySelector("form");
  const bacCheckbox = form.elements.include_bac_water;
  const bacGroup = dialog.querySelector("[data-bac-group]");
  const bacItemSelect = form.elements.bac_item_id;
  const bacQuantitySelect = form.elements.bac_quantity;
  const priceInput = form.elements.price;
  const bacPriceInput = form.elements.bac_price;
  const totalEl = document.getElementById("sale-total");

  function money(n) {
    return `$${n.toFixed(2)}`;
  }

  function updateTotal() {
    const main = parseFloat(priceInput.value) || 0;
    const bac = (bacCheckbox && bacCheckbox.checked && bacPriceInput) ? (parseFloat(bacPriceInput.value) || 0) : 0;
    totalEl.textContent = money(main + bac);
  }

  function syncBacGroup() {
    if (!bacCheckbox || !bacGroup) return;
    bacGroup.hidden = !bacCheckbox.checked;
    updateTotal();
  }

  function rebuildBacQuantityOptions() {
    if (!bacItemSelect || !bacQuantitySelect) return;
    const selected = bacItemSelect.selectedOptions[0];
    const available = selected ? parseInt(selected.dataset.available || "0", 10) : 0;
    const wanted = bacQuantitySelect.dataset.selected || "";
    bacQuantitySelect.innerHTML = "";
    for (let n = 1; n <= available; n++) {
      const opt = document.createElement("option");
      opt.value = String(n);
      opt.textContent = String(n);
      bacQuantitySelect.appendChild(opt);
    }
    if (wanted) bacQuantitySelect.value = wanted;
  }

  document.querySelectorAll('[data-action="sold"]').forEach((btn) => btn.addEventListener("click", () => {
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-sale"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  if (bacCheckbox) bacCheckbox.addEventListener("change", syncBacGroup);
  if (bacItemSelect) bacItemSelect.addEventListener("change", () => { rebuildBacQuantityOptions(); updateTotal(); });
  if (bacPriceInput) bacPriceInput.addEventListener("input", updateTotal);
  priceInput.addEventListener("input", updateTotal);

  // Initial state matters both for a fresh dialog and for a server re-render after a validation
  // error (sf.* values already reflected server-side; this fills in what only JS can compute).
  rebuildBacQuantityOptions();
  syncBacGroup();
  updateTotal();

  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();
