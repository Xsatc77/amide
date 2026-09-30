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

  // "Log dose" itself opens the picker for any item that needs a site -- picking one both fills
  // it in AND submits, so there's a single action per row instead of a separate "Pick site" button
  // easy to miss next to it (a real mis-click the owner ran into: hitting "Log dose" logged the
  // item on its bare recommended default instead of opening the picker).
  document.querySelectorAll('[data-action="log-dose"][data-needs-site]').forEach((btn) => btn.addEventListener("click", () => {
    activeForm = btn.closest("form");
    paintDots(btn.dataset.itemId);
    dialog.showModal();
  }));

  dialog.querySelectorAll('circle[data-site]').forEach((circle) => circle.addEventListener("click", () => {
    if (circle.classList.contains("site-dot-ineligible") || !activeForm) return;
    const input = activeForm.querySelector("[data-site-input]");
    if (input) input.value = circle.dataset.site;
    dialog.close();
    activeForm.submit();
  }));

  dialog.querySelectorAll('[data-action="close-site"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));
  // Close only when BOTH the press and release land on the backdrop itself -- a `click` event's
  // target is the dialog element whenever the mouseup lands on the backdrop, even if the mousedown
  // that started a text-selection drag began inside the dialog.
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });
})();

// Today view: dismiss the empty-vial banner (shown after a dose emptied its vial) without
// discarding it -- just drop the query param and hide the banner, same "no reload needed" pattern
// the Inventory page's expiry-prompt flow uses for its own query param.
(() => {
  const banner = document.querySelector("[data-empty-vial-banner]");
  if (!banner) return;
  const dismissBtn = banner.querySelector('[data-action="dismiss-empty-vial"]');
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      banner.remove();
      if (window.location.search.includes("empty_vial")) {
        window.history.replaceState({}, "", "/today");
      }
    });
  }
})();
