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
