// Quick activity dialog: opens from any [data-quick-open] button. The server validates everything.
(() => {
  const dialog = document.getElementById("quick-activity-dialog");
  if (!dialog) return;
  document.querySelectorAll("[data-quick-open]").forEach((b) => b.addEventListener("click", () => dialog.showModal()));
  dialog.querySelectorAll("[data-quick-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("mousedown", (e) => { dialog._backdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (dialog._backdrop && e.target === dialog) dialog.close(); });
})();
