// Vendor card: the product drop-down shows one product's price chart at a time. All charts are drawn by the
// server; this only toggles which one is visible. Nothing is stored in the browser.
(function () {
  const select = document.getElementById("price-product-select");
  if (!select) return;
  const panels = document.querySelectorAll(".price-panel");
  function show() {
    panels.forEach((panel) => { panel.hidden = panel.dataset.product !== select.value; });
  }
  select.addEventListener("change", show);
  show();
})();
