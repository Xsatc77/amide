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
