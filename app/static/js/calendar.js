// Calendar: switch views from the dropdown, and show what's due when a line, block or card is clicked.
(() => {
  const select = document.getElementById("cal-view");
  select.addEventListener("change", () => select.form.submit());

  const data = JSON.parse(document.getElementById("cal-data").textContent).occurrences;
  const dialog = document.getElementById("cal-dialog");
  const $ = (id) => document.getElementById(id);

  function show(key) {
    const occ = data[key];
    if (!occ) return;
    $("cal-dialog-title").textContent = occ.name;
    $("cal-dialog-date").textContent = occ.date;
    $("cal-dialog-edit").href = occ.edit_url;
    dialog.className = `dialog cal-dialog c${occ.color}`;
    $("cal-dialog-items").replaceChildren(...occ.items.map((i) => {
      const li = document.createElement("li");
      const head = document.createElement("strong");
      head.textContent = i.peptide;
      const dose = document.createElement("span");
      dose.className = "cal-due-dose";
      dose.textContent = i.dose + (i.step ? ` · titration step ${i.step}` : "");
      const meta = document.createElement("span");
      meta.className = "small muted";
      meta.textContent = [i.time, i.route, i.inventory && `Inventory: ${i.inventory}`].filter(Boolean).join(" · ");
      li.append(head, dose, meta);
      return li;
    }));
    dialog.showModal();
  }

  // Close-on-backdrop only fires when BOTH the press and release land on the backdrop itself -- a
  // `click` event's target is the dialog element whenever the mouseup lands on the backdrop, even
  // if the mousedown that started a text-selection drag (e.g. copying a peptide name) began inside
  // the dialog's content.
  let mousedownOnBackdrop = false;
  document.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-key]");
    if (el) show(el.dataset.key);
    if (e.target.closest("[data-close]") || (mousedownOnBackdrop && e.target === dialog)) dialog.close();
  });
})();
