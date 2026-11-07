// "Weight & reps each set": ticking the box swaps the single weight for one box per set (as many as the Sets field says).
// Field names come from the checkbox's own name: "per_set[12]" gives "set_weight_1[12]", "extra-3-per_set" gives "extra-3-set_weight_1".
function syncSetWeights(row) {
  const box = row.querySelector("[data-per-set]");
  const holder = row.querySelector("[data-set-weights]");
  if (!box || !holder) return;
  const single = row.querySelector('input[name^="weight_value"], input[name$="-weight_value"]');
  const sets = row.querySelector('input[name^="sets_value"], input[name$="-sets_value"]');
  const reps = row.querySelector('input[name^="reps_value"], input[name$="-reps_value"]');
  holder.hidden = !box.checked;
  if (single) single.style.display = box.checked ? "none" : "";
  if (reps) reps.style.display = box.checked ? "none" : "";
  if (!box.checked) return;
  const want = Math.min(50, Math.max(1, parseInt(sets && sets.value, 10) || 1));
  const have = [...holder.querySelectorAll("label")];
  for (let k = have.length + 1; k <= want; k++) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "number"; input.step = "0.1"; input.min = "0"; input.style.width = "5em";
    input.name = box.name.replace("per_set", "set_weight_" + k);
    input.setAttribute("aria-label", "Weight, set " + k);
    const rep = document.createElement("input");
    rep.type = "number"; rep.step = "1"; rep.min = "1"; rep.max = "200"; rep.style.width = "4em";
    rep.name = box.name.replace("per_set", "set_reps_" + k);
    rep.setAttribute("aria-label", "Reps, set " + k);
    const before = have.length ? holder.querySelectorAll("label") : [];
    const last = before.length ? before[before.length - 1].querySelectorAll("input") : [];
    input.value = (last[0] ? last[0].value : single && single.value) || "";
    rep.value = (last[1] ? last[1].value : reps && reps.value) || "";
    label.append("Set " + k + " ", input, " × ", rep);
    holder.appendChild(label);
  }
  holder.querySelectorAll("label").forEach((label, i) => { if (i >= want) label.remove(); });
}
function wireSetWeights(row) {
  const box = row.querySelector("[data-per-set]");
  if (!box) return;
  const sets = row.querySelector('input[name^="sets_value"], input[name$="-sets_value"]');
  box.addEventListener("change", () => syncSetWeights(row));
  if (sets) sets.addEventListener("input", () => syncSetWeights(row));
  syncSetWeights(row);
}
document.querySelectorAll("[data-per-set]").forEach((box) => wireSetWeights(box.closest("tr")));

// Workout log form: rows for exercises added on the day, from a <template> with the "__I__" index placeholder
// (same convention as the plan editor and the vendor contact rows). Nothing is stored in the browser.
(() => {
  const rows = document.querySelector("[data-extra-rows]");
  const template = document.getElementById("extra-row-template");
  const addButton = document.querySelector('[data-action="add-extra"]');
  if (!rows || !template || !addButton) return;
  let count = rows.querySelectorAll("[data-extra-row]").length;

  function wire(row) {
    row.querySelector('[data-action="remove-extra"]').addEventListener("click", () => row.remove());
  }

  rows.querySelectorAll("[data-extra-row]").forEach(wire);

  // Last time: under an exercise added on the day, what was last completed for it (sets, reps, weight or minutes).
  let lastTimer = null;
  function showLast(input) {
    const cell = input.closest("td");
    let note = cell.querySelector("[data-last-time]");
    if (!note) {
      note = document.createElement("div");
      note.className = "muted small";
      note.dataset.lastTime = "";
      cell.appendChild(note);
    }
    const name = input.value.trim();
    if (!name) { note.textContent = ""; return; }
    fetch("/workouts/last?name=" + encodeURIComponent(name), { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const last = data && data.last && data.last.text ? data.last : null;
        const weeks = last && last.stalled_weeks;
        note.textContent = last ? "Last time: " + last.text : "";
        if (weeks) note.textContent += "\nNo increase in " + weeks + " week" + (weeks === 1 ? "" : "s") + " (weight or reps)";
        note.style.whiteSpace = "pre-line";
      })
      .catch(() => { note.textContent = ""; });
  }
  rows.addEventListener("change", (event) => { if (event.target.matches('input[name$="-exercise"]')) showLast(event.target); });
  rows.addEventListener("input", (event) => {
    if (!event.target.matches('input[name$="-exercise"]')) return;
    clearTimeout(lastTimer);
    lastTimer = setTimeout(() => showLast(event.target), 400);
  });
  rows.querySelectorAll('input[name$="-exercise"]').forEach((input) => { if (input.value.trim()) showLast(input); });

  addButton.addEventListener("click", () => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(count));
    });
    const row = fragment.querySelector("[data-extra-row]");
    count += 1;
    rows.appendChild(fragment);
    wire(row);
    wireSetWeights(row);
    row.querySelector("input").focus();
  });
})();


// Submitting a workout with exercises not ticked Done asks first (the form carries data-confirm-incomplete). Free workouts have no Done boxes, so they never ask.
(() => {
  const form = document.querySelector("form[data-confirm-incomplete]");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    const boxes = [...form.querySelectorAll('input[type="checkbox"][name^="completed["]')];
    const open = boxes.filter((box) => !box.checked);
    if (!open.length) return;
    const names = open.map((box) => (box.closest("tr")?.querySelector("strong")?.textContent || "").trim()).filter(Boolean);
    const message = `${open.length} of ${boxes.length} exercise${boxes.length === 1 ? " is" : "s are"} not marked done` + (names.length ? `: ${names.join(", ")}` : "") + ".\n\nSubmit the workout anyway?";
    if (!window.confirm(message)) event.preventDefault();
  });
})();
