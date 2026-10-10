// Dashboard: the "Log water" dialog (open/close mirrors fitness-test.js's own dialog pattern)
// plus its three quick-pick buttons, which fill the ounces field and submit rather than needing
// their own same-named form fields (avoids ambiguity over which "ounces" value wins).
(() => {
  // A select marked data-autosubmit (the Compliance time window) reloads the page as soon as it changes.
  document.querySelectorAll("select[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => select.form.submit());
  });

  const dialog = document.getElementById("water-dialog");
  if (!dialog) return;
  const ouncesInput = document.getElementById("water-ounces-input");

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action], [data-quick-oz]");
    if (!btn) return;
    if (btn.dataset.action === "open-water-dialog") {
      dialog.showModal();
    } else if (btn.dataset.action === "close" && dialog.contains(btn)) {
      dialog.close();
    } else if (btn.dataset.quickOz) {
      ouncesInput.value = btn.dataset.quickOz;
      dialog.querySelector("form").submit();
    }
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => {
    if (mousedownOnBackdrop && e.target === dialog) dialog.close();
  });
})();

// Forms that ask first (Reset today's water).
document.querySelectorAll("form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (event) => { if (!window.confirm(f.dataset.confirm)) event.preventDefault(); }));
