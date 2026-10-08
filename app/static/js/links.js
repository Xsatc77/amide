// Links page: the Add / Edit dialog (one form, filled from the row's data attributes) and the delete confirmation.
(() => {
  const dialog = document.getElementById("link-dialog");
  if (!dialog) return;
  const form = document.getElementById("link-form");
  const title = document.getElementById("link-dialog-title");

  function open(mode, data) {
    form.action = mode === "edit" ? `/links/${data.id}` : "/links";
    title.textContent = mode === "edit" ? "Edit link" : "Add a link";
    form.querySelectorAll(".error").forEach((e) => e.remove());
    form.querySelectorAll(".has-error").forEach((e) => e.classList.remove("has-error"));
    form.name.value = data.name || "";
    form.url.value = data.url || "";
    form.link_type.value = data.type || "Other";
    form.description.value = data.description || "";
    dialog.showModal();
    form.name.focus();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "add-link") open("add", {});
    else if (btn.dataset.action === "edit-link") open("edit", btn.dataset);
    else if (btn.dataset.action === "close" && dialog.contains(btn)) dialog.close();
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });

  document.querySelectorAll("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
})();
