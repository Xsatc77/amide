// The Add A General Note button opens its popup.
(() => {
  const dialog = document.getElementById("general-note-dialog");
  if (!dialog) return;
  document.addEventListener("click", (e) => {
    if (e.target.closest('[data-action="open-general-note"]')) {
      dialog.showModal();
      const title = dialog.querySelector('input[name="title"]');
      if (title) title.focus();
    } else if (e.target.closest('[data-action="close-general-note"]')) dialog.close();
  });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
})();
