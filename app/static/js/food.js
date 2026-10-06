// Food tab: the Add food dialog (search, create, quick add), the custom-split fields, and delete confirmations. The server
// validates everything.
(() => {
  const diet = document.querySelector("[data-food-diet]");
  const custom = document.querySelector("[data-food-custom]");
  if (diet && custom) diet.addEventListener("change", () => { custom.hidden = diet.value !== "custom"; });

  document.querySelectorAll("[data-food-delete]").forEach((f) => f.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this? This cannot be undone.")) event.preventDefault();
  }));

  const dialog = document.getElementById("food-dialog");
  if (!dialog) return;
  const form = dialog.querySelector("[data-food-form]");
  const mealInput = form.querySelector("[data-food-meal]");
  const modeInput = form.querySelector("[data-food-mode]");
  const idInput = form.querySelector("[data-food-id]");
  const search = form.querySelector("[data-food-search]");
  const results = form.querySelector("[data-food-results]");
  const picked = form.querySelector("[data-food-picked]");
  const panes = form.querySelectorAll("[data-food-pane]");
  let timer = null;

  function setMode(mode) {
    modeInput.value = mode;
    panes.forEach((pane) => {
      const on = pane.dataset.foodPane === mode || (mode === "quick" && pane.dataset.foodPane === "create");
      pane.hidden = !on;
      pane.querySelectorAll("input").forEach((input) => { input.disabled = !on; });
    });
    form.querySelector("[data-food-quick-note]").hidden = mode !== "quick";
    form.querySelector("[data-food-create-note]").hidden = mode === "quick";
  }
  dialog.querySelectorAll("input[name=food-mode]").forEach((radio) => radio.addEventListener("change", () => setMode(radio.value)));

  function row(food) {
    const li = document.createElement("li");
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "food-result";
    pick.textContent = food.name + " (" + food.serving + ") " + Math.round(food.calories) + " kcal, P " + food.protein_g + " C " + food.carb_g + " F " + food.fat_g;
    pick.addEventListener("click", () => {
      idInput.value = food.id;
      picked.textContent = "Selected: " + food.name + " (" + food.serving + ")";
    });
    li.appendChild(pick);
    if (food.starter) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "btn btn-ghost small";
      copy.textContent = "Copy to My foods";
      copy.addEventListener("click", async () => {
        const r = await fetch("/food/foods/" + food.id + "/copy", { method: "POST", credentials: "same-origin" });
        copy.textContent = r.ok ? "Copied" : "Could not copy";
        copy.disabled = true;
      });
      li.appendChild(copy);
    }
    return li;
  }

  async function runSearch() {
    const response = await fetch("/food/search?q=" + encodeURIComponent(search.value), { credentials: "same-origin" });
    if (!response.ok) return;
    results.replaceChildren(...(await response.json()).map(row));
  }
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(runSearch, 200); });

  document.querySelectorAll("[data-food-add]").forEach((button) => button.addEventListener("click", () => {
    mealInput.value = button.dataset.meal;
    dialog.showModal();
    search.focus();
    runSearch();
  }));
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
  dialog.querySelector("[data-food-close]").addEventListener("click", () => dialog.close());

  form.addEventListener("submit", (event) => {
    if (modeInput.value === "existing" && !idInput.value) {
      event.preventDefault();
      picked.textContent = "Pick a food from the list first.";
    }
  });
  setMode("existing");
})();
