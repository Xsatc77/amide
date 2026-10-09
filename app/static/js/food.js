// Food tab: the Add food dialog (search, create, quick add), the custom-split fields, and delete confirmations. The server
// validates everything.
(() => {
  const diet = document.querySelector("[data-food-diet]");
  const custom = document.querySelector("[data-food-custom]");
  if (diet && custom) diet.addEventListener("change", () => { custom.hidden = diet.value !== "custom"; });

  document.querySelectorAll("[data-food-delete]").forEach((f) => f.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this? This cannot be undone.")) event.preventDefault();
  }));

  // Recommend buttons: foods that fill what is left of protein, carbs or fiber with the fewest carbs.
  const box = document.querySelector("[data-food-recommend-box]");
  const NAMES = { protein: "protein", carb: "carbs", fiber: "fiber" };
  document.querySelectorAll("[data-food-recommend]").forEach((button) => button.addEventListener("click", async () => {
    const nutrient = button.dataset.foodRecommend;
    box.hidden = false;
    box.textContent = "Looking…";
    try {
      const response = await fetch(`/food/recommend?nutrient=${nutrient}&date=${button.dataset.day}`, { headers: { Accept: "application/json" } });
      const data = await response.json();
      box.textContent = "";
      if (data.status !== "ok") { box.textContent = "Fill in your profile and a weigh-in above first, so Amide knows what is left."; return; }
      if (!data.foods.length) { box.textContent = `Nothing to suggest: you have reached your ${NAMES[nutrient]} for today, or no food fits the calories left.`; return; }
      const heading = document.createElement("p");
      heading.className = "small";
      heading.textContent = `About ${data.remaining} g of ${NAMES[nutrient]} left. These get close, with the fewest carbs first:`;
      const list = document.createElement("ul");
      data.foods.forEach((food) => {
        const item = document.createElement("li");
        item.textContent = `${food.servings} × ${food.name} (${food.serving}): ${food.calories} kcal, protein ${food.protein_g} g, carbs ${food.carb_g} g, fiber ${food.fiber_g} g`;
        list.append(item);
      });
      box.append(heading, list);
    } catch (error) {
      box.textContent = "Could not get suggestions.";
    }
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

  // Search USDA: only when the person presses the button. A result is added to My foods, then picked from the list above.
  const usda = form.querySelector("[data-usda-search]");
  if (usda) {
    const usdaResults = usda.querySelector("[data-usda-results]");
    const note = usda.querySelector("[data-usda-note]");
    usda.querySelector("[data-usda-run]").addEventListener("click", async () => {
      note.textContent = "";
      usdaResults.replaceChildren();
      const response = await fetch("/food/usda?q=" + encodeURIComponent(search.value), { credentials: "same-origin" });
      if (!response.ok) { note.textContent = "The USDA database could not be reached."; return; }
      const found = await response.json();
      if (!found.length) note.textContent = "Nothing found.";
      usdaResults.replaceChildren(...found.map((food) => {
        const li = document.createElement("li");
        const add = document.createElement("button");
        add.type = "button";
        add.className = "food-result";
        add.textContent = food.name + " (" + food.serving + ") " + Math.round(food.calories) + " kcal, P " + food.protein_g + " C " + food.carb_g + " F " + food.fat_g + " — add to My foods";
        add.addEventListener("click", async () => {
          const body = new URLSearchParams({ name: food.name, serving: food.serving, serving_g: food.serving_g, calories: food.calories,
            protein_g: food.protein_g, carb_g: food.carb_g, fat_g: food.fat_g, fiber_g: food.fiber_g, date: document.querySelector("input[name=date]")?.value || "" });
          const saved = await fetch("/food/foods", { method: "POST", body, credentials: "same-origin", redirect: "manual" });
          add.textContent = (saved.ok || saved.type === "opaqueredirect") ? "Added to My foods: search above to pick it" : "Could not add";
          add.disabled = true;
        });
        li.appendChild(add);
        return li;
      }));
    });
  }

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
