// The label page: the Print button and, after a check-in, opening the print window by itself and then going on to the next page.
(() => {
  const sheet = document.querySelector(".label-sheet");
  document.querySelectorAll("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));
  if (!sheet || !("autoPrint" in sheet.dataset)) return;
  window.addEventListener("afterprint", () => { window.location.href = sheet.dataset.next || "/inventory"; });
  window.addEventListener("load", () => setTimeout(() => window.print(), 300));
})();
