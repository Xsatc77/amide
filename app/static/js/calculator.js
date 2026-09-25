// Reconstitution calculator: reads the form, calls the server's math on every change (debounced), and
// renders the result + syringe visual. No math happens here — the numbers always come from the API.
(() => {
  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("calc-data").textContent);
  const capacityFor = new Map(data.syringe_capacities);  // [mL, units] pairs -> a real Map avoids the
  // string-coercion trap of plain-object numeric keys (Number(1.0) stringifies to "1", not "1.0").
  const els = {
    vial: $("calc-vial"), water: $("calc-water"), dose: $("calc-dose"), doseUnit: $("calc-dose-unit"),
    inventory: $("calc-inventory"), protocolDose: $("calc-protocol-dose"),
    warning: $("calc-warning"), unitsValue: $("calc-units-value"),
    concentration: $("calc-concentration"), draw: $("calc-draw"), doses: $("calc-doses"),
    fill: $("calc-fill"), ticks: $("calc-ticks"), scale: $("calc-scale"),
    targetUnits: $("calc-target-units"), targetResult: $("calc-target-result"),
    reconstituteWrap: $("calc-reconstitute"), reconstituteBtn: $("calc-reconstitute-btn"),
    confirmDialog: $("reconstitute-confirm"),
  };
  let syringeMl = Number(document.querySelector('[data-syringe][aria-pressed="true"]')?.dataset.syringe) || 1.0;

  function fmt(n, digits = 4) {
    return Number(n.toPrecision(digits)).toString();
  }

  function buildScale() {
    const capacity = capacityFor.get(syringeMl);
    const step = capacity <= 30 ? 5 : capacity <= 50 ? 10 : 20;
    els.scale.replaceChildren();
    els.ticks.replaceChildren();
    for (let u = 0; u <= capacity; u += step) {
      const pct = (u / capacity) * 100;
      const label = document.createElement("span");
      label.style.left = `${pct}%`;
      label.textContent = u;
      els.scale.append(label);
      const tick = document.createElement("span");
      tick.className = "calc-tick";
      tick.style.left = `${pct}%`;
      els.ticks.append(tick);
    }
  }

  async function recompute() {
    const params = new URLSearchParams({
      vial_mg: els.vial.value, water_ml: els.water.value, dose_value: els.dose.value,
      dose_unit: els.doseUnit.value, syringe_ml: String(syringeMl),
    });
    const r = await (await fetch(`/api/calculator/compute?${params}`)).json();
    render(r);
  }

  function render(r) {
    const capacity = capacityFor.get(syringeMl);
    if (r.problems.length) {
      els.warning.hidden = false;
      els.warning.textContent = "Enter a " + r.problems.join(", ") + " amount greater than 0 to see a result.";
      els.unitsValue.innerHTML = '—<span class="calc-units-label">units</span>';
      els.concentration.textContent = els.draw.textContent = els.doses.textContent = "—";
      els.fill.style.width = "0%";
      return;
    }
    els.warning.hidden = !r.over_capacity;
    if (r.over_capacity) {
      els.warning.textContent = `This draw (${fmt(r.units, 3)} units) is more than the ${syringeMl} mL syringe holds (${capacity} units). Pick a larger syringe or a more concentrated mix.`;
    }
    els.unitsValue.innerHTML = `${fmt(r.units, 3)}<span class="calc-units-label">units</span>`;
    els.concentration.innerHTML = `${fmt(r.concentration_mg_ml)} mg/mL <span class="muted">(${fmt(r.concentration_mcg_ml)} mcg/mL)</span>`;
    els.draw.textContent = `${fmt(r.draw_ml)} mL`;
    els.doses.textContent = r.doses_per_vial;
    els.fill.style.width = `${Math.min(100, (r.units / capacity) * 100)}%`;
    els.fill.classList.toggle("over", r.over_capacity);
  }

  async function recomputeTarget() {
    if (!els.targetUnits.value) {
      els.targetResult.textContent = "—";
      return;
    }
    const params = new URLSearchParams({
      vial_mg: els.vial.value,
      dose_mg: els.doseUnit.value === "mcg" ? String(Number(els.dose.value || 0) / 1000) : els.dose.value,
      target_units: els.targetUnits.value,
    });
    const { water_ml } = await (await fetch(`/api/calculator/target-water?${params}`)).json();
    els.targetResult.textContent = water_ml == null ? "Enter a vial and dose first" : `${fmt(water_ml)} mL`;
  }

  document.querySelectorAll("[data-fill]").forEach((btn) =>
    btn.addEventListener("click", () => {
      $(btn.dataset.fill).value = btn.dataset.value;
      recompute();
    })
  );

  document.querySelectorAll("[data-syringe]").forEach((btn) =>
    btn.addEventListener("click", () => {
      syringeMl = Number(btn.dataset.syringe);
      document.querySelectorAll("[data-syringe]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      buildScale();
      recompute();
    })
  );

  function updateReconstituteVisibility() {
    if (!els.reconstituteWrap) return;
    els.reconstituteWrap.hidden = !els.inventory?.value;
    // Locked to the selected item's real vial size so it can never diverge from what the server
    // will actually save; editable again once the selection is cleared (standalone/practice mode).
    els.vial.readOnly = !!els.inventory?.value;
  }

  els.inventory?.addEventListener("change", () => {
    const opt = els.inventory.selectedOptions[0];
    if (opt?.dataset.vialMg) { els.vial.value = opt.dataset.vialMg; recompute(); }
    updateReconstituteVisibility();
  });
  updateReconstituteVisibility();

  els.reconstituteBtn?.addEventListener("click", () => {
    const opt = els.inventory.selectedOptions[0];
    const dialog = els.confirmDialog;
    dialog.querySelector('[data-fill="item-name"]').textContent = opt.textContent;
    dialog.querySelector('[data-fill="water-ml"]').textContent = `${els.water.value} mL`;
    dialog.querySelector('[data-fill="concentration"]').textContent = els.concentration.textContent;
    dialog.querySelector('[data-fill="dose"]').textContent = `${els.dose.value} ${els.doseUnit.value}`;
    dialog.querySelector('[data-fill="doses"]').textContent = els.doses.textContent;
    dialog.querySelector("[data-inventory-item-id]").value = opt.value;
    dialog.querySelector("[data-water-ml-value]").value = els.water.value;
    dialog.querySelector("[data-dose-value]").value = els.dose.value;
    dialog.querySelector("[data-dose-unit-value]").value = els.doseUnit.value;
    const discardInput = dialog.querySelector("[data-discard-by]");
    if (!discardInput.value) {
      const d = new Date();
      d.setDate(d.getDate() + (Number(data.default_discard_days) || 28));
      discardInput.value = d.toISOString().slice(0, 10);
    }
    dialog.showModal();
  });
  els.confirmDialog?.querySelector('[data-action="cancel-reconstitute"]')?.addEventListener("click", () => {
    els.confirmDialog.close();
  });

  els.protocolDose?.addEventListener("change", () => {
    if (!els.protocolDose.value) return;
    const [dose, unit] = els.protocolDose.value.split("|");
    els.dose.value = dose;
    els.doseUnit.value = unit;
    recompute();
    recomputeTarget();
  });

  let timer;
  const debounced = (fn) => () => { clearTimeout(timer); timer = setTimeout(fn, 150); };
  [els.vial, els.water, els.dose].forEach((el) => el.addEventListener("input", debounced(() => { recompute(); recomputeTarget(); })));
  els.doseUnit.addEventListener("change", () => { recompute(); recomputeTarget(); });
  els.targetUnits.addEventListener("input", debounced(recomputeTarget));

  buildScale();
  recompute();
})();
