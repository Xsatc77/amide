// Today view: injection-site picker dialog.
(() => {
  const dialog = document.getElementById("site-dialog");
  if (!dialog) return;  // No due items need a site today
  const siteDataEl = document.getElementById("site-data");
  const siteData = siteDataEl ? JSON.parse(siteDataEl.textContent) : {};
  let activeForm = null;

  function paintDots(itemId) {
    const data = siteData[itemId];
    dialog.querySelectorAll("circle[data-site]").forEach((circle) => {
      const site = circle.dataset.site;
      circle.classList.remove("site-dot-last", "site-dot-recommended", "site-dot-available", "site-dot-ineligible");
      if (!data || !data.sites.some((s) => s.value === site)) {
        circle.classList.add("site-dot-ineligible");
      } else if (data.last === site) {
        circle.classList.add("site-dot-last");
      } else if (data.recommended === site) {
        circle.classList.add("site-dot-recommended");
      } else {
        circle.classList.add("site-dot-available");
      }
    });
  }

  document.querySelectorAll('[data-action="pick-site"]').forEach((btn) => btn.addEventListener("click", () => {
    activeForm = btn.closest("form");
    paintDots(btn.dataset.itemId);
    dialog.showModal();
  }));

  dialog.querySelectorAll('circle[data-site]').forEach((circle) => circle.addEventListener("click", () => {
    if (circle.classList.contains("site-dot-ineligible") || !activeForm) return;
    const input = activeForm.querySelector("[data-site-input]");
    if (input) input.value = circle.dataset.site;
    dialog.close();
  }));

  dialog.querySelectorAll('[data-action="close-site"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
})();
