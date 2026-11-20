// A form marked data-confirm asks first (used by the Passkeys page; other pages have their own copy of this in their script).
document.querySelectorAll("form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (event) => { if (!window.confirm(f.dataset.confirm)) event.preventDefault(); }));
