// Price correction: each dropdown starts with only its current choice (hundreds of products times every peptide would be a huge page) and is filled with every
// peptide the first time it is opened. A search box and a "no match only" switch narrow the rows; rows you have changed are marked and counted.
(() => {
  const dataNode = document.getElementById("pc-peptides");
  const form = document.getElementById("pc-form");
  if (!dataNode || !form) return;
  const peptides = JSON.parse(dataNode.textContent);
  const selects = [...form.querySelectorAll("[data-pc-select]")];
  const rows = [...form.querySelectorAll("[data-pc-row]")];
  const filter = document.getElementById("pc-filter");
  const unmatchedOnly = document.getElementById("pc-unmatched");
  const changedNote = document.getElementById("pc-changed");

  function option(value, text, selected) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    o.selected = !!selected;
    return o;
  }

  function fill(select) {
    if (select.dataset.filled) return;
    select.dataset.filled = "1";
    const current = select.value;
    select.textContent = "";
    select.appendChild(option("none", "No match", current === "none"));
    peptides.forEach((p) => select.appendChild(option("p" + p.id, p.name, current === "p" + p.id)));
    select.appendChild(option("new", "+ Add as a new peptide", current === "new"));
  }

  function refresh() {
    const text = (filter.value || "").trim().toLowerCase();
    rows.forEach((row) => {
      const hide = (text && !row.dataset.name.includes(text)) || (unmatchedOnly.checked && row.dataset.matched);
      row.hidden = !!hide;
    });
    const changed = selects.filter((s) => s.value !== s.dataset.original);
    selects.forEach((s) => s.closest("tr").classList.toggle("pc-changed", s.value !== s.dataset.original));
    changedNote.textContent = changed.length ? `${changed.length} change${changed.length === 1 ? "" : "s"} not saved yet` : "";
  }

  selects.forEach((select) => {
    ["focus", "pointerdown", "keydown"].forEach((type) => select.addEventListener(type, () => fill(select), { once: true }));
    select.addEventListener("change", refresh);
  });
  filter.addEventListener("input", refresh);
  unmatchedOnly.addEventListener("change", refresh);
  refresh();
})();
