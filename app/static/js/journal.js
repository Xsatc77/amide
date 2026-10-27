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

// Add a photo dialog (Journal tab): opens from the top button or a day's own button, which also sets the day.
(() => {
  const dialog = document.getElementById("journal-photo-dialog");
  if (!dialog) return;
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "open-journal-photo") {
      const day = document.getElementById("journal-photo-date");
      if (day && btn.dataset.date) day.value = btn.dataset.date;
      dialog.showModal();
    } else if (btn.dataset.action === "close-journal-photo" && dialog.contains(btn)) dialog.close();
  });
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });
  // A refused upload re-renders the page: reopen the popup with the reason inside it.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
  // Upload sends the photos to the server, which can take a while on a phone connection: say so, and do not send twice.
  dialog.querySelector("form").addEventListener("submit", (e) => {
    const button = e.currentTarget.querySelector('button[type="submit"]');
    if (button.disabled) { e.preventDefault(); return; }
    button.disabled = true;
    button.textContent = "Uploading...";
  });
})();

// Log Workout dialog (Journal tab): open/close wiring; the form is a plain GET that redirects to the day's log form.
(() => {
  const dialog = document.getElementById("workout-log-dialog");
  if (!dialog) return;
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "open-workout-log") dialog.showModal();
    else if (btn.dataset.action === "close-workout-log" && dialog.contains(btn)) dialog.close();
  });
})();
