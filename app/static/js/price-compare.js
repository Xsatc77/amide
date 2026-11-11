// Library card: click the price range to see the vendors with the best price PER VIAL, for each vial size.
(() => {
  const dialog = document.getElementById("price-compare");
  const dataEl = document.getElementById("price-compare-data");
  if (!dialog || !dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const select = dialog.querySelector("[data-price-size]");
  const list = dialog.querySelector("[data-price-list]");

  data.sizes.forEach((size, i) => {
    const option = document.createElement("option");
    option.value = String(i);
    option.textContent = size.label + " vials";
    select.appendChild(option);
  });
  select.value = String(data.default);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Optional shot size: with an amount and a unit, the server works out the best vial, the shots it gives and what is wasted if it would expire first.
  const amountInput = dialog.querySelector("[data-shot-amount]");
  const unitSelect = dialog.querySelector("[data-shot-unit]");
  const weekInput = dialog.querySelector("[data-shot-week]");
  const summary = dialog.querySelector("[data-shot-summary]");
  let plan = null, timer = null, sequence = 0;
  const num = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: 3 });

  function shotText(entry) {
    if (entry.shots === null) return ["Not comparable: this vial is not measured in " + plan.unit + ".", false];
    if (entry.shots === 0) return ["Too small for one " + num(plan.amount) + " " + plan.unit + " shot.", false];
    if (!entry.expires) return [entry.shots + " shots per vial, all inside " + plan.window + " days.", false];
    return [entry.shots + " shots per vial, but only " + entry.used + " fit in " + plan.window + " days at " + num(plan.per_week) + " a week: "
            + num(entry.wasted) + " " + plan.unit + " (" + entry.wasted_pct + "%) wasted.", true];
  }

  function refresh(pick) {
    const amount = parseFloat(amountInput.value), unit = unitSelect.value;
    const week = weekInput.value.trim() === "" ? 7 : parseFloat(weekInput.value);
    if (!(amount > 0) || !unit || !(week >= 0.1 && week <= 21)) { plan = null; summary.hidden = true; render(); return; }
    const mine = ++sequence;
    fetch("/library/" + dialog.dataset.peptideId + "/shot-plan?amount=" + encodeURIComponent(amount) + "&unit=" + encodeURIComponent(unit) + "&per_week=" + week)
      .then((r) => (r.ok ? r.json() : null))
      .then((result) => {
        if (mine !== sequence) return;
        plan = result;
        if (plan && pick && plan.best !== null) select.value = String(plan.best);
        render();
      })
      .catch(() => { plan = null; render(); });
  }
  function onShotInput() { clearTimeout(timer); timer = setTimeout(() => refresh(true), 250); }
  [amountInput, weekInput].forEach((input) => input.addEventListener("input", onShotInput));
  unitSelect.addEventListener("change", onShotInput);

  function render() {
    const index = Number(select.value);
    const size = data.sizes[index];
    const entry = plan && plan.sizes[index];
    [...select.options].forEach((option, i) => { option.textContent = data.sizes[i].label + " vials" + (plan && plan.best === i ? " · best for your shots" : ""); });
    summary.hidden = !plan;
    if (plan) {
      const best = plan.best === null ? null : data.sizes[plan.best];
      summary.textContent = best
        ? "Best for " + num(plan.amount) + " " + plan.unit + " shots: the " + best.label + " vial, from $" + plan.sizes[plan.best].per_shot[0].toFixed(2) + " per shot with any waste counted."
        : "No vial size here can be compared with a shot in " + plan.unit + ".";
    }
    list.replaceChildren();
    size.vendors.forEach((v, rank) => {
      const row = el("li", "price-compare-row");
      row.appendChild(el("span", "price-compare-rank", String(rank + 1)));
      const who = el("div", "price-compare-who");
      const name = v.id ? el("a", "", v.name) : el("span", "", v.name);
      if (v.id) name.href = "/vendors/" + v.id;
      who.appendChild(name);
      who.appendChild(el("span", "tag tag-plain", v.warehouse));
      who.appendChild(el("div", "small muted", v.pack + " · list " + v.date));
      let perShot = null;
      if (entry) {
        const [text, waste] = shotText(entry);
        who.appendChild(el("div", "shot-detail" + (waste ? " shot-waste" : " muted"), text));
        perShot = entry.per_shot[rank];
      }
      row.appendChild(who);
      const price = el("div", "price-compare-price");
      if (perShot !== null && perShot !== undefined) {
        price.appendChild(el("strong", "", "$" + perShot.toFixed(2)));
        price.appendChild(el("span", "price-compare-unit", " per shot"));
        price.appendChild(el("div", "small muted", "$" + v.per_vial + " per vial"));
      } else {
        price.appendChild(el("strong", "", "$" + v.per_vial));
        price.appendChild(el("span", "price-compare-unit", " per vial"));
      }
      row.appendChild(price);
      list.appendChild(row);
    });
  }

  select.addEventListener("change", render);       // a different vial size keeps the shot plan: its shots and waste follow the choice
  document.querySelectorAll("[data-price-compare]").forEach((button) => button.addEventListener("click", () => {
    select.value = String(data.default);
    render();
    if (amountInput.value && unitSelect.value) refresh(true);
    dialog.showModal();
  }));
  dialog.querySelector("[data-price-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
})();
