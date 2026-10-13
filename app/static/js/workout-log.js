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
        note.textContent = data && data.last && data.last.text ? "Last time: " + data.last.text : "";
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
    row.querySelector("input").focus();
  });
})();
