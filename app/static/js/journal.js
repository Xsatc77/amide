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
      const title = dialog.querySelector("[data-photo-day]");
      const setDay = () => {
        if (!day || !btn.dataset.date) return;
        day.defaultValue = day.value = btn.dataset.date;
        day.dispatchEvent(new Event("change", { bubbles: true }));
        if (title) title.textContent = new Date(btn.dataset.date + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
      };
      setDay();
      dialog.showModal();
      setDay();                                    // again once the popup is showing: some phone browsers ignore a date set while it was closed
    } else if (btn.dataset.action === "close-journal-photo" && dialog.contains(btn)) dialog.close();
  });
  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });
  // A refused upload re-renders the page: reopen the popup with the reason inside it.
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
  // Upload: shrink each photo in the browser first (a phone photo is several MB, and the server keeps at most 2000 px anyway), send them
  // with fetch, and say plainly in the popup when it fails. A plain form post from a phone can arrive with no file in it and no explanation
  // (the same fix the body photos use).
  const form = dialog.querySelector("form");
  const input = form.querySelector('input[type="file"]');
  const button = form.querySelector('button[type="submit"]');
  const note = form.querySelector("[data-photo-upload-error]");
  const MAX_SIDE = 2000;

  async function shrink(file) {
    if (!file.type.startsWith("image/") && !/\.(heic|heif)$/i.test(file.name)) return file;
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
      const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.88));
      return blob && blob.size < file.size ? blob : file;
    } finally { URL.revokeObjectURL(url); }
  }

  function failed(message) {
    note.textContent = message;
    note.hidden = false;
    button.disabled = false;
    button.textContent = "Upload";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (button.disabled) return;
    note.hidden = true;
    if (!input.files.length) { failed("Choose a photo first."); return; }
    button.disabled = true;
    button.textContent = "Uploading...";
    const data = new FormData(form);
    data.delete("file");
    for (const file of input.files) {
      let sent = file;
      try { sent = await shrink(file); } catch (error) { /* the browser could not read it: send the original and let the server decide */ }
      if (!sent.size) { failed(`${file.name} is empty. If it is in iCloud, open it in Photos until it has downloaded, then choose it again.`); return; }
      data.append("file", sent, sent === file ? file.name : file.name.replace(/\.[^.]+$/, "") + ".jpg");
    }
    let response;
    try {
      response = await fetch(form.action, { method: "POST", body: data, credentials: "same-origin" });
    } catch (error) {
      failed("Could not reach Amide. Check your connection and try again.");
      return;
    }
    if (response.status === 413) { failed("The server or a proxy in front of it refused a photo this large. Its upload limit needs raising, or try a smaller photo."); return; }
    if (response.status === 422) {
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const reason = page.querySelector("#journal-photo-dialog div.alert");
      failed(reason ? reason.textContent.trim() : "That photo could not be saved.");
      return;
    }
    if (!response.ok) { failed(`Something went wrong saving the photo (error ${response.status}). Try again.`); return; }
    window.location.assign("/measurements?tab=journal");
    window.location.reload();
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
