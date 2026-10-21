// The Apple Health choices popup: a section switch turns its metrics on or off, the count follows, and it opens by itself when there is something new to decide.
(() => {
  const dialog = document.getElementById("health-choices-dialog");
  if (!dialog) return;
  const metrics = () => Array.from(dialog.querySelectorAll('input[name="metric"]'));
  const refresh = () => {
    dialog.querySelectorAll("[data-section]").forEach((section) => {
      const boxes = Array.from(section.querySelectorAll('input[name="metric"]'));
      section.querySelector("[data-section-switch]").checked = boxes.every((b) => b.checked);
    });
    dialog.querySelector("[data-count]").textContent = metrics().filter((b) => b.checked).length + " of " + metrics().length + " on";
  };
  dialog.addEventListener("change", (event) => {
    const target = event.target;
    if (target.matches("[data-section-switch]")) {
      target.closest("[data-section]").querySelectorAll('input[name="metric"]').forEach((b) => { b.checked = target.checked; });
    }
    refresh();
  });
  dialog.querySelector("[data-all-off]").addEventListener("click", () => { metrics().forEach((b) => { b.checked = false; }); refresh(); });
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));      // nothing saved
  dialog.querySelector("[data-use-recommended]").addEventListener("click", () => { metrics().forEach((b) => { b.checked = b.dataset.recommended === "1"; }); refresh(); });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-health-choices]") && !dialog.open) { event.preventDefault(); dialog.showModal(); }
  });
  if (dialog.hasAttribute("data-open-on-load") && !dialog.open) dialog.showModal();
})();
