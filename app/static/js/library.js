// Library list: live search and goal / "added by me" filters.
(() => {
  const search = document.getElementById("lib-search");
  const tiles = [...document.querySelectorAll(".lib-tile")];
  const chips = [...document.querySelectorAll("[data-filter]")];
  const count = document.getElementById("lib-count");
  const empty = document.getElementById("lib-empty");
  let filter = "all";

  function apply() {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const t of tiles) {
      const matchesFilter = filter === "all" || (filter === "added" ? t.dataset.added === "1"
        : t.dataset.goals.split(" ").includes(filter));
      const matchesSearch = words.every((w) => t.dataset.search.includes(w));
      t.hidden = !(matchesFilter && matchesSearch);
      if (!t.hidden) shown++;
    }
    count.textContent = shown;
    empty.hidden = shown > 0;
  }

  search.addEventListener("input", apply);
  chips.forEach((chip) => chip.addEventListener("click", () => {
    filter = chip.dataset.filter;
    chips.forEach((c) => c.setAttribute("aria-pressed", c === chip ? "true" : "false"));
    apply();
  }));
})();
