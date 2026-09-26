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

  // Close only when BOTH the press and release land on the backdrop itself -- a `click` event's
  // target is the dialog element whenever the mouseup lands on the backdrop, even if the mousedown
  // that started a text-selection drag began inside a field. Checking mousedown too means a drag
  // that starts inside the form and ends outside it no longer closes the dialog.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
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
    "coa_vial_size_mg", "coa_purity_pct", "received_quantity",
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
    const receivedGroup = form.querySelector('[data-field="received_quantity"]');
    if (receivedGroup) receivedGroup.hidden = !order.arrived;
    const arrivalGroup = form.querySelector('[data-field="arrival_date"]');
    if (arrivalGroup) arrivalGroup.hidden = !order.arrived;
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
  // See item-dialog's own backdrop-close comment above for why mousedown is tracked too.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: values are already server-rendered
  // from `form`, so just reopen as-is.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();

// ---------------------------------------------------------------- item detail: check-in dialog
(() => {
  const dialog = document.getElementById("checkin-dialog");
  if (!dialog) return;  // Supply items, or a non-owner viewer, have no Order History section
  const form = dialog.querySelector("form");
  const linesContainer = dialog.querySelector("[data-checkin-lines]");
  const alertBox = dialog.querySelector("[data-checkin-alert]");
  const arrivalInput = form.elements.arrival_date;
  const arrivalError = dialog.querySelector('[data-error="arrival_date"]');
  const checkinData = JSON.parse(document.getElementById("checkin-data").textContent);

  function clearErrors() {
    alertBox.hidden = true;
    arrivalError.hidden = true;
    arrivalError.textContent = "";
    dialog.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
    dialog.querySelectorAll("[data-error]").forEach((el) => { el.hidden = true; el.textContent = ""; });
  }

  function buildLines(orderId, prefill) {
    linesContainer.innerHTML = "";
    const lines = checkinData[orderId] || [];
    for (const line of lines) {
      const posted = prefill && prefill.form ? prefill.form[`received_quantity_${line.id}`] : null;
      const postedNote = prefill && prefill.form ? prefill.form[`received_note_${line.id}`] : null;
      const err = prefill && prefill.errors ? prefill.errors[`received_quantity_${line.id}`] : null;

      const row = document.createElement("div");
      row.className = "grid";

      const nameDiv = document.createElement("div");
      nameDiv.className = "field span-2";
      const nameSpan = document.createElement("span");
      nameSpan.textContent = `${line.item_name} (ordered ${line.quantity})`;
      nameDiv.appendChild(nameSpan);

      const qtyLabel = document.createElement("label");
      qtyLabel.className = `field ${err ? "has-error" : ""}`;
      const qtySpan = document.createElement("span");
      qtySpan.textContent = "Received";
      const qtyInput = document.createElement("input");
      qtyInput.name = `received_quantity_${line.id}`;
      qtyInput.type = "number";
      qtyInput.min = "0";
      qtyInput.max = String(line.quantity);
      qtyInput.step = "1";
      qtyInput.value = posted != null && posted !== "" ? posted : String(line.quantity);
      qtyLabel.append(qtySpan, qtyInput);
      if (err) {
        const small = document.createElement("small");
        small.className = "error";
        small.textContent = err;
        qtyLabel.appendChild(small);
      }

      const noteLabel = document.createElement("label");
      noteLabel.className = "field";
      const noteSpan = document.createElement("span");
      noteSpan.textContent = "Note (if short or damaged)";
      const noteInput = document.createElement("input");
      noteInput.name = `received_note_${line.id}`;
      noteInput.maxLength = 300;
      noteInput.value = postedNote || "";
      noteLabel.append(noteSpan, noteInput);

      row.append(nameDiv, qtyLabel, noteLabel);
      linesContainer.appendChild(row);
    }
  }

  document.querySelectorAll('[data-action="check-in"]').forEach((btn) => btn.addEventListener("click", () => {
    clearErrors();
    form.reset();
    const orderId = btn.dataset.orderId;
    form.action = `${window.location.pathname}/orders/${orderId}/check-in`;
    buildLines(orderId, null);
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-checkin"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  // See item-dialog's own backdrop-close comment above for why mousedown is tracked too.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a check-in validation error: rebuild this order's rows from
  // checkin-data (same as a fresh open) then overlay what was actually posted and each field's
  // error, using checkin-error-data -- there is no server-rendered row to just reopen as-is here.
  if (dialog.hasAttribute("data-open-on-load")) {
    const errorData = JSON.parse(document.getElementById("checkin-error-data").textContent);
    const orderId = errorData.order_id;
    form.action = `${window.location.pathname}/orders/${orderId}/check-in`;
    arrivalInput.value = errorData.form.arrival_date || "";
    if (errorData.errors.arrival_date) {
      alertBox.hidden = false;
      arrivalError.hidden = false;
      arrivalError.textContent = errorData.errors.arrival_date;
    }
    buildLines(orderId, errorData);
    if (Object.keys(errorData.errors).length) alertBox.hidden = false;
    dialog.showModal();
  }
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
    // No BAC Water in stock -- the group renders no <select>, so a checked box would submit an
    // incomplete bundle. Block that at the button instead of silently posting.
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = bacCheckbox.checked && !bacItemSelect;
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
  // See item-dialog's own backdrop-close comment above for why mousedown is tracked too.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
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

// ---------------------------------------------------------------- inventory list: New Order dialog
(() => {
  const dialog = document.getElementById("new-order-dialog");
  if (!dialog) return;  // Only the Inventory list page has this dialog
  const form = dialog.querySelector("form");
  const linesContainer = dialog.querySelector("[data-lines-container]");
  const template = document.getElementById("new-order-line-template");
  let lineCount = 0;

  // ---- vendor branch: is this a new vendor, or an existing one from the dropdown? ----
  const vendorGroupExisting = dialog.querySelector('[data-vendor-group="existing"]');
  const vendorGroupNew = dialog.querySelector('[data-vendor-group="new"]');
  const vendorSelect = form.elements.vendor_id;
  const stalenessPrompt = dialog.querySelector("[data-staleness-prompt]");
  const stalenessDateEl = dialog.querySelector("[data-staleness-date]");
  const priceListReplaceGroup = dialog.querySelector("[data-price-list-replace]");
  const priceRecallData = JSON.parse(document.getElementById("price-recall-data").textContent);

  function syncVendorMode() {
    const mode = form.querySelector('[data-vendor-is-new-radio]:checked')?.value || "no";
    vendorGroupExisting.hidden = mode !== "no";
    vendorGroupNew.hidden = mode !== "yes";
    // A brand-new vendor has no price history to recall from -- drop any auto-filled costs still
    // carrying our own recall marker the moment the toggle switches to "new vendor" (Fix 3c). A
    // value the user actually typed (no marker) is left alone.
    if (mode === "yes") clearRecallForAllLines();
  }
  form.querySelectorAll('[data-vendor-is-new-radio]').forEach((r) => r.addEventListener("change", syncVendorMode));

  function syncPriceListReplaceGroup() {
    const current = form.querySelector('[data-price-list-current-radio]:checked')?.value || "yes";
    priceListReplaceGroup.hidden = current !== "no";
  }
  form.querySelectorAll('[data-price-list-current-radio]').forEach((r) =>
    r.addEventListener("change", syncPriceListReplaceGroup));

  // The "still current?" prompt only makes sense once an existing vendor with a price list on
  // file is picked -- driven by data-has-price-list/data-price-list-date on that <option>, set
  // server-side from vendor_price_list_map (Step 4).
  function syncStalenessPrompt() {
    const opt = vendorSelect.selectedOptions[0];
    const hasPriceList = !!(opt && opt.dataset.hasPriceList === "1");
    stalenessPrompt.hidden = !hasPriceList;
    stalenessDateEl.textContent = hasPriceList && opt.dataset.priceListDate
      ? ` (from ${opt.dataset.priceListDate})` : "";
    syncPriceListReplaceGroup();
  }

  // ---- per-line price recall: prefill a line's cost from the last time THIS vendor was ordered
  // from for an item of the same name (Step 5). priceRecallData is keyed by vendor id (as a
  // string, since it travels through JSON) -> lowercased item name -> dollars. Scoped server-side
  // to this viewer's own visible order history -- see _price_recall_map in inventory.py. ----
  function priceRecallCost(vendorId, itemName) {
    if (!vendorId || !itemName) return null;
    const forVendor = priceRecallData[String(vendorId)];
    if (!forVendor) return null;
    const cost = forVendor[itemName.trim().toLowerCase()];
    return cost === undefined ? null : cost;
  }

  // A cost field the recall mechanism itself filled carries dataset.recalled="1" -- cleared the
  // moment the user actually types into it (wireCostRecallInput below), so a value the user edited
  // is never touched again by recall, while a still-marked value is free to be replaced or cleared
  // as the vendor/item/mode selection changes underneath it (Fix 3).
  function clearRecallMarker(input) {
    delete input.dataset.recalled;
  }

  function markRecalled(input, value) {
    input.value = value;
    input.dataset.recalled = "1";
  }

  function wireCostRecallInput(costInput) {
    costInput.addEventListener("input", () => clearRecallMarker(costInput));
  }

  function applyPriceRecall(fieldset) {
    const costInput = fieldset.querySelector('[name$="-cost"]');
    if (!costInput) return;
    // Only ever touch a field that's currently empty, or still carries our own recall marker --
    // never a value the user actually typed into it.
    if (costInput.value && costInput.dataset.recalled !== "1") return;
    const mode = fieldset.querySelector('[data-line-mode]:checked')?.value || "existing";
    let itemName = null;
    if (mode === "existing") {
      const sel = fieldset.querySelector("[data-line-item-select]");
      itemName = sel?.selectedOptions[0]?.dataset.name || null;
    } else {
      itemName = fieldset.querySelector("[data-line-item-name]")?.value || null;
    }
    const cost = priceRecallCost(vendorSelect.value, itemName);
    if (cost != null) {
      markRecalled(costInput, cost.toFixed(2));
    } else if (costInput.dataset.recalled === "1") {
      // Was auto-filled for a previous vendor/item selection that no longer applies (e.g. the
      // vendor changed, or this line's item changed) and nothing matches the new one -- clear it
      // rather than leave the stale recalled value in place.
      costInput.value = "";
      clearRecallMarker(costInput);
    }
  }

  function applyPriceRecallToAllLines() {
    linesContainer.querySelectorAll("[data-line]").forEach(applyPriceRecall);
  }

  function clearRecallForAllLines() {
    linesContainer.querySelectorAll("[data-line]").forEach((fieldset) => {
      const costInput = fieldset.querySelector('[name$="-cost"]');
      if (costInput && costInput.dataset.recalled === "1") {
        costInput.value = "";
        clearRecallMarker(costInput);
      }
    });
  }

  vendorSelect.addEventListener("change", () => {
    syncStalenessPrompt();
    applyPriceRecallToAllLines();
  });

  // ---- new vendor branch: repeatable contact-method rows, mirroring vendors.js's identical
  // pattern for the Vendor edit dialog exactly (add/remove rows, toggle the "new type" field). ----
  const contactRowsContainer = dialog.querySelector("[data-new-vendor-contact-rows]");
  const contactRowTemplate = document.getElementById("new-vendor-contact-row-template");
  let vendorContactRowCount = contactRowsContainer.querySelectorAll("[data-new-vendor-contact-row]").length;

  function syncNewMethodType(row) {
    const select = row.querySelector('select[name$="-method_type_id"]');
    const newField = row.querySelector("[data-new-method-type]");
    newField.hidden = select.value !== "__new__";
  }

  function wireContactRow(row) {
    const select = row.querySelector('select[name$="-method_type_id"]');
    select.addEventListener("change", () => syncNewMethodType(row));
    syncNewMethodType(row);
    row.querySelector('[data-action="remove-vendor-contact-row"]').addEventListener("click", () => row.remove());
  }
  contactRowsContainer.querySelectorAll("[data-new-vendor-contact-row]").forEach(wireContactRow);

  function addContactRow() {
    const index = vendorContactRowCount++;
    const fragment = contactRowTemplate.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(index));
    });
    const row = fragment.querySelector("[data-new-vendor-contact-row]");
    contactRowsContainer.appendChild(fragment);
    wireContactRow(row);
  }
  dialog.querySelector('[data-action="add-vendor-contact-row"]').addEventListener("click", addContactRow);

  // ---- order lines (unchanged from before Task 5, plus price-recall wiring per line) ----

  function addLine() {
    const index = lineCount++;
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(index));
    });
    const fieldset = fragment.querySelector("[data-line]");
    syncLineMode(fieldset);
    fieldset.querySelectorAll('[data-line-mode]').forEach((radio) =>
      radio.addEventListener("change", () => { syncLineMode(fieldset); applyPriceRecall(fieldset); }));
    fieldset.querySelector('[data-action="remove-line"]').addEventListener("click", () => fieldset.remove());
    fieldset.querySelector("[data-line-item-select]").addEventListener("change", () => applyPriceRecall(fieldset));
    fieldset.querySelector("[data-line-item-name]").addEventListener("blur", () => applyPriceRecall(fieldset));
    const costInput = fieldset.querySelector('[name$="-cost"]');
    if (costInput) wireCostRecallInput(costInput);
    linesContainer.appendChild(fragment);
  }

  function syncLineMode(fieldset) {
    const mode = fieldset.querySelector('[data-line-mode]:checked')?.value || "existing";
    fieldset.querySelector('[data-line-group="existing"]').hidden = mode !== "existing";
    fieldset.querySelector('[data-line-group="new"]').hidden = mode !== "new";
  }

  document.querySelectorAll('[data-action="new-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    linesContainer.innerHTML = "";
    lineCount = 0;
    contactRowsContainer.innerHTML = "";
    vendorContactRowCount = 0;
    addLine();
    syncVendorMode();
    syncStalenessPrompt();
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-new-order"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  dialog.querySelector('[data-action="add-line"]').addEventListener("click", addLine);
  // See item-dialog's own backdrop-close comment above for why mousedown is tracked too.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: reopen with the posted lines restored
  // (from the new-order-error-data JSON blob) and per-line field errors shown. The header's own
  // fields (is_new_vendor/vendor_id/new-vendor profile/contact rows) are already server-rendered
  // correctly via `nof`/`new_order_contact_groups` -- just sync the JS-driven show/hide state.
  if (dialog.hasAttribute("data-open-on-load")) {
    syncVendorMode();
    syncStalenessPrompt();

    const errorData = JSON.parse(document.getElementById("new-order-error-data").textContent);
    linesContainer.innerHTML = "";
    lineCount = 0;
    const groups = errorData.line_groups || {};
    const indices = Object.keys(groups).map(Number).sort((a, b) => a - b);
    if (indices.length === 0) indices.push(0);
    for (const i of indices) {
      addLine();
      const fieldset = linesContainer.lastElementChild;
      const posted = groups[i] || {};
      const mode = posted.mode || "existing";
      const modeRadio = [...fieldset.querySelectorAll('[data-line-mode]')].find((r) => r.value === mode);
      if (modeRadio) { modeRadio.checked = true; modeRadio.dispatchEvent(new Event("change", { bubbles: true })); }
      for (const [field, value] of Object.entries(posted)) {
        if (field === "mode") continue;
        const el = fieldset.querySelector(`[name$="-${field}"]`);
        if (!el) continue;
        if (el.type === "radio") {
          const match = fieldset.querySelector(`[name$="-${field}"][value="${value}"]`);
          if (match) { match.checked = true; match.dispatchEvent(new Event("change", { bubbles: true })); }
        } else {
          el.value = value;
        }
      }
      for (const [field, message] of Object.entries(errorData.errors || {})) {
        const prefix = `lines-${i}-`;
        if (!field.startsWith(prefix)) continue;
        const fieldName = field.slice(prefix.length);
        const el = fieldset.querySelector(`[name$="-${fieldName}"]`);
        if (!el) continue;
        el.closest("label")?.classList.add("has-error");
        const small = document.createElement("small");
        small.className = "error";
        small.textContent = message;
        el.insertAdjacentElement("afterend", small);
      }
    }
    dialog.showModal();
  }
})();
