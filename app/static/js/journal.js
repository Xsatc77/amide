// Journal tab (inside Weight & Measurements): the "New Entry" dialog open/close wiring.
(() => {
  const dialog = document.getElementById("journal-dialog");
  if (!dialog) return;  // only present on the Journal tab

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "open-journal") dialog.showModal();
    else if (action === "close" && dialog.contains(btn)) dialog.close();
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });
})();
