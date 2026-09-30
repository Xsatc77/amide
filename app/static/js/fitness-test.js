// Fitness Test: the "Log a new test" dialog open/close (mirrors labs.js's/measurements.js's
// own dialog-open pattern -- a plain button opens it, no rows to repeat here).
(() => {
  const dialog = document.getElementById("fitness-test-dialog");
  if (!dialog) return;

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "open-fitness-test-dialog") {
      dialog.showModal();
    } else if (btn.dataset.action === "close" && dialog.contains(btn)) {
      dialog.close();
    }
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });
})();
