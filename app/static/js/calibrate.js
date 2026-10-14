// Opens the "how much is in your open vials?" prompt when the page loads (it is only on the page when there is something to ask).
(() => {
  const dialog = document.getElementById("calibrate-dialog");
  if (dialog && dialog.hasAttribute("data-open-on-load") && !dialog.open) dialog.showModal();
})();
