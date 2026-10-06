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

  function render() {
    const size = data.sizes[Number(select.value)];
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
      row.appendChild(who);
      const price = el("div", "price-compare-price");
      price.appendChild(el("strong", "", "$" + v.per_vial));
      price.appendChild(el("span", "price-compare-unit", " per vial"));
      row.appendChild(price);
      list.appendChild(row);
    });
  }

  select.addEventListener("change", render);
  document.querySelectorAll("[data-price-compare]").forEach((button) => button.addEventListener("click", () => {
    select.value = String(data.default);
    render();
    dialog.showModal();
  }));
  dialog.querySelector("[data-price-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
})();
