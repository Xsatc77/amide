// Reconstitution calculator: reads the form, calls the server's math on every change (debounced), and renders the result, the
// syringe visual and the "Why?" explanation. No math happens here — every number comes from the API.
(() => {
  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("calc-data").textContent);
  const capacityFor = new Map(data.syringe_capacities);  // [mL, units] pairs -> a real Map avoids the
  // string-coercion trap of plain-object numeric keys (Number(1.0) stringifies to "1", not "1.0").
  const els = {
    vial: $("calc-vial"), vialUnit: $("calc-vial-unit"), water: $("calc-water"), dose: $("calc-dose"), doseUnit: $("calc-dose-unit"),
    iuWrap: $("calc-iu-wrap"), iu: $("calc-iu-per-mg"), iuNote: $("calc-iu-note"),
    inventory: $("calc-inventory"), library: $("calc-library"), protocolDose: $("calc-protocol-dose"), bac: $("calc-bac"), bacInfo: $("calc-bac-info"),
    levelsWrap: $("calc-levels-wrap"), levels: $("calc-levels"), levelNote: $("calc-level-note"), practice: $("calc-practice"),
    warning: $("calc-warning"), unitsValue: $("calc-units-value"),
    concentration: $("calc-concentration"), draw: $("calc-draw"), doses: $("calc-doses"),
    fill: $("calc-fill"), ticks: $("calc-ticks"), scale: $("calc-scale"),
    why: $("calc-why"), whyEmpty: $("calc-why-empty"), steps: $("calc-steps"), issues: $("calc-issues"), fixes: $("calc-fixes"),
    range: $("calc-range"), suggestions: $("calc-suggestions"),
    targetUnits: $("calc-target-units"), targetResult: $("calc-target-result"),
    reconstituteWrap: $("calc-reconstitute"), reconstituteBtn: $("calc-reconstitute-btn"),
    confirmDialog: $("reconstitute-confirm"),
  };
  let syringeMl = Number(document.querySelector('[data-syringe][aria-pressed="true"]')?.dataset.syringe) || 1.0;
  const inventoryById = new Map(data.inventory.map((i) => [String(i.id), i]));
  const libraryById = new Map(data.library.map((p) => [String(p.id), p]));
  const libraryValue = (id) => `lib-${id}`;                 // option values carry a prefix so they can never be mistaken for inventory item ids
  const libraryPeptide = (value) => (value ? libraryById.get(value.replace(/^lib-/, "")) : null);
  const bacById = new Map((data.bac || []).map((b) => [String(b.id), b]));

  function fmt(n, digits = 4) {
    return Number(n.toPrecision(digits)).toString();
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------ units

  function needsFactor() {
    const v = els.vialUnit.value, d = els.doseUnit.value;
    return v !== d && (v === "IU" || d === "IU");
  }

  function syncUnitsUi() {
    const iuVial = els.vialUnit.value === "IU";
    document.querySelectorAll("[data-presets]").forEach((b) => { b.hidden = (b.dataset.presets === "IU") !== iuVial; });
    els.iuWrap.hidden = !needsFactor();
    if (!els.iuWrap.hidden) {
      els.iuNote.textContent = `The vial is in ${els.vialUnit.value} and the dose in ${els.doseUnit.value}: this converts between them (HGH is about 3 IU per mg).`;
    }
  }

  // ------------------------------------------------------------ scale + result

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

  function params() {
    return new URLSearchParams({
      vial_mg: els.vial.value, vial_unit: els.vialUnit.value, water_ml: els.water.value, dose_value: els.dose.value,
      dose_unit: els.doseUnit.value, syringe_ml: String(syringeMl), iu_per_mg: els.iu.value,
    });
  }

  async function recompute() {
    syncUnitsUi();
    const r = await (await fetch(`/api/calculator/compute?${params()}`)).json();
    render(r);
    renderWhy(r.teach);
    updateBacInfo();
  }

  function render(r) {
    const capacity = capacityFor.get(syringeMl);
    if (r.problems.length) {
      els.warning.hidden = false;
      els.warning.textContent = r.problems.includes("iu_per_mg")
        ? "Enter the IU per mg so the dose can be converted to the vial's unit."
        : "Enter a " + r.problems.join(", ") + " amount greater than 0 to see a result.";
      els.unitsValue.innerHTML = '—<span class="calc-units-label">units</span>';
      els.concentration.textContent = els.draw.textContent = els.doses.textContent = "—";
      els.fill.style.width = "0%";
      return;
    }
    els.warning.hidden = !r.over_capacity;
    if (r.over_capacity) {
      els.warning.textContent = `This draw (${fmt(r.units, 3)} units) is more than the ${syringeMl} mL syringe holds (${capacity} units). See “Why?” below for what to change.`;
    }
    els.unitsValue.innerHTML = `${fmt(r.units, 3)}<span class="calc-units-label">units</span>`;
    els.concentration.replaceChildren(document.createTextNode(`${fmt(r.concentration)} ${r.concentration_unit}/mL`));
    if (r.concentration_mcg_ml != null) {
      els.concentration.append(" ", el("span", "muted", `(${fmt(r.concentration_mcg_ml)} mcg/mL)`));
    }
    els.draw.textContent = `${fmt(r.draw_ml)} mL`;
    els.doses.textContent = r.doses_per_vial;
    els.fill.style.width = `${Math.min(100, (r.units / capacity) * 100)}%`;
    els.fill.classList.toggle("over", r.over_capacity);
  }

  // ------------------------------------------------------------ the "Why?" panel

  function renderWhy(t) {
    els.steps.replaceChildren();
    els.issues.replaceChildren();
    els.suggestions.replaceChildren();
    els.fixes.hidden = true;
    els.whyEmpty.hidden = t.verdict !== "incomplete";
    for (const step of t.steps) {
      const li = el("li", "calc-step");
      li.append(el("strong", "", step.title + ": "), document.createTextNode(step.text));
      els.steps.append(li);
    }
    for (const issue of t.issues) {
      const bad = issue.kind !== "little_water";
      els.issues.append(el("p", bad ? "calc-issue" : "calc-note", issue.message));
    }
    if (t.split) {
      els.issues.append(el("p", "calc-note",
        `If this really is the dose, split it into ${t.split.injections} injections of about ${fmt(t.split.units_each, 3)} units each.`));
    }
    if (t.verdict === "ok" && t.steps.length) {
      els.issues.append(el("p", "calc-ok", "This draw is easy to measure on the syringe."));
    }
    if (t.viable_water) {
      els.fixes.hidden = false;
      els.range.textContent = `(${fmt(t.viable_water.low, 3)} to ${fmt(t.viable_water.high, 3)} mL puts the draw between 5 units and the syringe's top mark)`;
      for (const s of t.suggestions) {
        if (s.water_ml < 0.1 || s.water_ml > 20) continue;
        const b = el("button", "chip", `${fmt(s.water_ml, 3)} mL → ${s.units} units`);
        b.type = "button";
        b.addEventListener("click", () => { els.water.value = fmt(s.water_ml, 4); recompute(); });
        els.suggestions.append(b);
      }
    }
  }

  async function recomputeTarget() {
    if (!els.targetUnits.value) {
      els.targetResult.textContent = "—";
      return;
    }
    const p = new URLSearchParams({
      vial_mg: els.vial.value, vial_unit: els.vialUnit.value, dose_value: els.dose.value, dose_unit: els.doseUnit.value,
      iu_per_mg: els.iu.value, target_units: els.targetUnits.value,
    });
    const { water_ml } = await (await fetch(`/api/calculator/target-water?${p}`)).json();
    els.targetResult.textContent = water_ml == null ? "Enter a vial and dose first" : `${fmt(water_ml)} mL`;
  }

  // ------------------------------------------------------------ peptide pickers and library doses

  let activeLibrary = null;

  function setDose(amount, unit) {
    els.dose.value = amount;
    els.doseUnit.value = unit;
  }

  function showLevels(peptide, preferred = "Intermediate") {
    activeLibrary = peptide;
    els.levels.replaceChildren();
    const tiers = peptide ? peptide.tiers : {};
    const names = Object.keys(tiers);
    els.levelsWrap.hidden = !peptide || !names.length;
    els.levelNote.textContent = "";
    if (!peptide) return;
    for (const level of ["Beginner", "Intermediate", "Advanced"]) {
      const tier = tiers[level];
      if (!tier) continue;
      const b = el("button", "chip", `${level}: ${tier.text}`);
      b.type = "button";
      b.dataset.level = level;
      b.addEventListener("click", () => chooseLevel(level));
      els.levels.append(b);
    }
    if (tiers[preferred]) chooseLevel(preferred);
    else els.levelNote.textContent = "This peptide has no library dose to pre-fill.";
  }

  function chooseLevel(level) {
    const tier = activeLibrary?.tiers[level];
    els.levels.querySelectorAll("[data-level]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.level === level)));
    if (!tier) return;
    const freq = tier.frequency ? ` ${tier.frequency.toLowerCase()}` : "";
    if (tier.amount == null) {
      els.levelNote.textContent = `${level} dose is “${tier.text}” — a weight-based or other dose that can't be pre-filled; enter the amount yourself.`;
      return;
    }
    setDose(fmt(tier.amount), tier.unit);
    els.levelNote.textContent = `${level}${freq}: ${tier.text}. Change the dose to try other amounts.`;
    syncUnitsUi();
    recompute();
    recomputeTarget();
  }

  function applyFactorDefault(value) {
    if (value && !els.iu.value) els.iu.value = value;
  }

  function updateReconstituteVisibility() {
    if (!els.reconstituteWrap) return;
    const item = els.inventory?.value ? inventoryById.get(els.inventory.value) : null;
    els.reconstituteWrap.hidden = !item;
    // Locked to the selected item's real vial size and unit so they can never diverge from what the server will save;
    // editable again once the selection is cleared (practice mode).
    els.vial.readOnly = !!item;
    els.vialUnit.disabled = !!item;
    els.practice.hidden = !!item || !(els.library?.value);
    const outOfStock = !!item && item.count <= 0;
    if (els.reconstituteBtn) {
      els.reconstituteBtn.disabled = outOfStock;
      els.reconstituteBtn.title = outOfStock ? "None left in stock to reconstitute." : "";
    }
  }

  els.inventory?.addEventListener("change", () => {
    const item = els.inventory.value ? inventoryById.get(els.inventory.value) : null;
    if (item) {
      els.vial.value = fmt(item.vial_mg);
      els.vialUnit.value = item.unit;
      applyFactorDefault(item.iu_per_mg);
      if (item.iu_per_mg) els.iu.value = item.iu_per_mg;
      const peptide = item.peptide_id ? libraryById.get(String(item.peptide_id)) : null;
      if (els.library) els.library.value = peptide ? libraryValue(peptide.id) : "";
      showLevels(peptide);
    }
    updateReconstituteVisibility();
    syncUnitsUi();
    recompute();
    recomputeTarget();
  });

  els.library?.addEventListener("change", () => {
    const peptide = libraryPeptide(els.library.value);
    const hadInventory = !!els.inventory?.value;
    if (els.inventory) els.inventory.value = "";                 // choosing from the master list is practice, not a real vial
    els.vial.readOnly = false;
    els.vialUnit.disabled = false;
    if (hadInventory) {                                          // leaving a real vial: start the practice vial fresh
      const iuDose = peptide && Object.values(peptide.tiers).some((t) => t.unit === "IU");
      els.vialUnit.value = iuDose ? "IU" : "mg";
      els.vial.value = "10";
      els.iu.value = "";
    }
    if (peptide) applyFactorDefault(peptide.iu_per_mg);
    showLevels(peptide);
    updateReconstituteVisibility();
    syncUnitsUi();
    recompute();
    recomputeTarget();
  });

  // ------------------------------------------------------------ BAC water

  function updateBacInfo() {
    if (!els.bac) return;
    const bac = els.bac.value ? bacById.get(els.bac.value) : null;
    const water = Number(els.water.value) || 0;
    let note = "";
    if (bac) {
      if (bac.open) {
        note = `Open vial: ${bac.open.ml_left} mL left, use by ${bac.open.discard_by}.`;
        if (water > bac.open.ml_left) note += ` That is less than the ${fmt(water)} mL you need, so a new bottle would be opened.`;
      } else {
        note = bac.in_stock > 0 ? `A new bottle (${bac.bottle_ml || 30} mL) would be opened; ${bac.in_stock} in stock.` : "None in stock and no open vial.";
      }
    } else {
      note = "Amide uses an open vial first, then your highest-priority bottle in stock.";
    }
    els.bacInfo.textContent = note;
  }

  // ------------------------------------------------------------ the usual controls

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

  els.reconstituteBtn?.addEventListener("click", () => {
    const opt = els.inventory.selectedOptions[0];
    const dialog = els.confirmDialog;
    dialog.querySelector('[data-fill="item-name"]').textContent = opt.textContent;
    dialog.querySelector('[data-fill="water-ml"]').textContent = `${els.water.value} mL`;
    dialog.querySelector('[data-fill="bac"]').textContent = els.bac?.value ? els.bac.selectedOptions[0].textContent : "Automatic (priority order)";
    dialog.querySelector('[data-fill="concentration"]').textContent = els.concentration.textContent;
    dialog.querySelector('[data-fill="dose"]').textContent = `${els.dose.value} ${els.doseUnit.value}`;
    dialog.querySelector('[data-fill="doses"]').textContent = els.doses.textContent;
    dialog.querySelector("[data-inventory-item-id]").value = opt.value;
    dialog.querySelector("[data-water-ml-value]").value = els.water.value;
    dialog.querySelector("[data-dose-value]").value = els.dose.value;
    dialog.querySelector("[data-dose-unit-value]").value = els.doseUnit.value;
    dialog.querySelector("[data-iu-per-mg-value]").value = needsFactor() || els.vialUnit.value === "IU" ? els.iu.value : "";
    dialog.querySelector("[data-bac-item-id]").value = els.bac?.value || "";
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
    setDose(dose, unit);
    recompute();
    recomputeTarget();
  });

  let timer;
  const debounced = (fn) => () => { clearTimeout(timer); timer = setTimeout(fn, 150); };
  [els.vial, els.water, els.dose, els.iu].forEach((el_) => el_.addEventListener("input", debounced(() => { recompute(); recomputeTarget(); })));
  [els.doseUnit, els.vialUnit].forEach((el_) => el_.addEventListener("change", () => { recompute(); recomputeTarget(); }));
  els.bac?.addEventListener("change", updateBacInfo);
  els.targetUnits.addEventListener("input", debounced(recomputeTarget));

  buildScale();
  syncUnitsUi();
  // Arrived with an inventory item already chosen (?inventory_item_id=): fill in its library dose too.
  if (els.inventory?.value) els.inventory.dispatchEvent(new Event("change"));
  else { updateReconstituteVisibility(); recompute(); }
})();
