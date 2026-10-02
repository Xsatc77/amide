// Protocols page: goal card selection, the Build link, saved-protocol filters, and confirm dialogs.
(() => {
  const buildLink = document.getElementById("build-link");
  const cards = [...document.querySelectorAll(".goal-card")];

  function updateBuildLink() {
    const goals = cards.filter((c) => c.getAttribute("aria-pressed") === "true").map((c) => c.dataset.goal);
    buildLink.href = "/protocols/new" + (goals.length ? "?" + goals.map((g) => "goal=" + encodeURIComponent(g)).join("&") : "");
    buildLink.setAttribute("aria-disabled", goals.length ? "false" : "true");
  }

  cards.forEach((card) =>
    card.addEventListener("click", () => {
      card.setAttribute("aria-pressed", card.getAttribute("aria-pressed") === "true" ? "false" : "true");
      updateBuildLink();
    })
  );
  buildLink.addEventListener("click", (e) => {
    if (buildLink.getAttribute("aria-disabled") === "true") e.preventDefault();
  });
  updateBuildLink();

  // Saved protocols filter chips.
  const chips = [...document.querySelectorAll("[data-filter]")];
  const rows = [...document.querySelectorAll("#saved-protocols tr[data-status]")];
  const emptyNote = document.querySelector(".empty-filter");
  chips.forEach((chip) =>
    chip.addEventListener("click", () => {
      chips.forEach((c) => c.setAttribute("aria-pressed", c === chip ? "true" : "false"));
      const f = chip.dataset.filter;
      let shown = 0;
      rows.forEach((r) => {
        r.hidden = f !== "all" && r.dataset.status !== f;
        if (!r.hidden) shown++;
      });
      if (emptyNote) emptyNote.hidden = shown > 0 || !rows.length;
    })
  );

  document.querySelectorAll("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    })
  );

  // Mine / Shared with me tabs.
  const tabChips = [...document.querySelectorAll("[data-tab]")];
  const tabPanels = { mine: document.getElementById("tab-mine"), shared: document.getElementById("tab-shared") };
  tabChips.forEach((chip) =>
    chip.addEventListener("click", () => {
      tabChips.forEach((c) => c.setAttribute("aria-pressed", c === chip ? "true" : "false"));
      Object.entries(tabPanels).forEach(([key, panel]) => { panel.hidden = key !== chip.dataset.tab; });
    })
  );
})();

// Total course quantities popup: one shared dialog, populated from the page's embedded JSON.
(() => {
  const dialog = document.getElementById("course-totals-dialog");
  if (!dialog) return;
  const body = document.getElementById("course-totals-body");
  const allTotals = JSON.parse(document.getElementById("course-totals-data").textContent);

  function fmt(n) {
    return n === null || n === undefined ? "" : String(Math.round(n * 100) / 100);
  }

  function render(protocolId) {
    const totals = allTotals[String(protocolId)];
    if (totals === null || totals === undefined) {
      body.replaceChildren(Object.assign(document.createElement("p"), {
        className: "muted",
        textContent: "Set an end date on this protocol to see its total course quantities.",
      }));
      return;
    }
    const table = document.createElement("table");
    table.className = "inv-table";
    const thead = document.createElement("thead");
    thead.innerHTML = "<tr><th>Peptide</th><th>Total</th><th>Vial Size</th><th>Vials</th><th>Bac Water</th></tr>";
    const tbody = document.createElement("tbody");
    let totalBacWater = 0;
    for (const t of totals) {
      const tr = document.createElement("tr");

      // Peptide cell
      const peptideTd = document.createElement("td");
      peptideTd.textContent = t.peptide;
      tr.append(peptideTd);

      // Total cell
      const totalTd = document.createElement("td");
      totalTd.textContent = t.as_needed || t.total_amount === null ? (t.note || "") : `${fmt(t.total_amount)} ${t.unit}`;
      tr.append(totalTd);

      // Vial Size cell (dropdown or input)
      const vialSizeTd = document.createElement("td");
      if (t.library_specifications) {
        const select = document.createElement("select");
        select.disabled = t.bac_water_ml !== null;
        select.className = "vial-size-select";
        select.dataset.totalAmount = t.total_amount || "0";
        select.dataset.unit = t.unit;

        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Select...";
        select.append(option);

        const specs = t.library_specifications.split(", ");
        specs.forEach(spec => {
          const opt = document.createElement("option");
          opt.value = spec;
          opt.textContent = spec;
          select.append(opt);
        });

        select.addEventListener("change", () => updateVials(tr, t));
        vialSizeTd.append(select);
      } else {
        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = "Enter size";
        input.disabled = t.bac_water_ml !== null;
        input.className = "vial-size-input";
        input.dataset.totalAmount = t.total_amount || "0";
        input.dataset.unit = t.unit;
        input.addEventListener("change", () => updateVials(tr, t));
        vialSizeTd.append(input);
      }
      tr.append(vialSizeTd);

      // Vials cell (dynamic)
      const vialsTd = document.createElement("td");
      vialsTd.className = "vials-cell";
      vialsTd.textContent = t.vials_estimate === null ? (t.note || "—") : String(t.vials_estimate);
      tr.append(vialsTd);

      // BAC water cell (dynamic)
      const bacTd = document.createElement("td");
      bacTd.className = "bac-cell";
      bacTd.textContent = t.bac_water_ml === null ? "—" : `${fmt(t.bac_water_ml)} mL`;
      if (t.bac_water_ml !== null) {
        totalBacWater += t.bac_water_ml;
      }
      tr.append(bacTd);

      tbody.append(tr);
    }
    table.append(thead, tbody);
    const caption = document.createElement("p");
    caption.className = "muted small";
    caption.textContent = `Estimated using 1.5 mL bacteriostatic water per vial. Total BAC Water Needed: ${fmt(totalBacWater)} mL`;
    body.replaceChildren(table, caption);
  }

  function updateVials(row, originalData) {
    const input = row.querySelector(".vial-size-select, .vial-size-input");
    const vialSizeStr = input.value;

    if (!vialSizeStr) {
      row.querySelector(".vials-cell").textContent = "—";
      row.querySelector(".bac-cell").textContent = "—";
      return;
    }

    const totalAmount = parseFloat(input.dataset.totalAmount);
    const unit = input.dataset.unit;

    const doseFactors = { "mg": 1, "mcg": 0.001, "IU": 0.0000167 };
    const doseFactor = doseFactors[unit];

    if (doseFactor === undefined) {
      row.querySelector(".vials-cell").textContent = "—";
      row.querySelector(".bac-cell").textContent = "—";
      return;
    }

    const regex = /(\d+(?:\.\d+)?)\s*(mg|mcg|iu|IU)/i;
    const match = vialSizeStr.match(regex);

    if (!match) {
      row.querySelector(".vials-cell").textContent = "—";
      row.querySelector(".bac-cell").textContent = "—";
      return;
    }

    const vialAmount = parseFloat(match[1]);
    const vialUnit = match[2].toLowerCase() === 'iu' ? 'IU' : match[2].toLowerCase();
    const vialFactor = doseFactors[vialUnit];

    if (vialFactor === undefined) {
      row.querySelector(".vials-cell").textContent = "—";
      row.querySelector(".bac-cell").textContent = "—";
      return;
    }

    const totalMg = totalAmount * doseFactor;
    const vialMg = vialAmount * vialFactor;
    const vialsEstimate = Math.ceil(totalMg / vialMg - 1e-9);
    const bacWaterMl = vialsEstimate * 1.5;

    row.querySelector(".vials-cell").textContent = String(vialsEstimate);
    row.querySelector(".bac-cell").textContent = `${fmt(bacWaterMl)} mL`;
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".totals-btn");
    if (btn) {
      render(btn.dataset.protocolId);
      dialog.showModal();
      return;
    }
    if (e.target.closest("[data-close]") && dialog.open) dialog.close();
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });
})();
