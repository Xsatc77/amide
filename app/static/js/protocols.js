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
    thead.innerHTML = "<tr><th>Peptide</th><th>Total</th><th>Vials</th><th>BAC water</th></tr>";
    const tbody = document.createElement("tbody");
    for (const t of totals) {
      const tr = document.createElement("tr");
      const totalCell = t.as_needed || t.total_amount === null ? (t.note || "") : `${fmt(t.total_amount)} ${t.unit}`;
      const vialsCell = t.vials_estimate === null ? (t.note || "—") : String(t.vials_estimate);
      const bacCell = t.bac_water_ml === null ? "—" : `${fmt(t.bac_water_ml)} mL`;
      for (const text of [t.peptide, totalCell, vialsCell, bacCell]) {
        const td = document.createElement("td");
        td.textContent = text;
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(thead, tbody);
    const caption = document.createElement("p");
    caption.className = "muted small";
    caption.textContent = "Estimated using 1.5 mL bacteriostatic water per vial.";
    body.replaceChildren(table, caption);
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
