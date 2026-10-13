// Body photos: click to reveal, optional authenticator-code unlock with a 10-minute countdown. The server enforces all of
// it; this only drives the page.
(() => {
  const root = document.getElementById("body-photos");
  if (!root) return;
  const mode = root.dataset.mode;                 // "click" (no 2FA setting) or "code" (setting on)
  const sharp = root.dataset.sharp === "1";       // the server rendered the photos sharp (unlocked)
  const upload = document.getElementById("photo-upload-dialog");
  const unlock = document.getElementById("photo-unlock-dialog");

  document.querySelectorAll("[data-photo-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  root.querySelector("[data-photo-add]").addEventListener("click", () => upload.showModal());
  if (upload.hasAttribute("data-open-on-load")) upload.showModal();

  // Upload: shrink the photo in the browser first (a phone photo is several MB; the server keeps at most 2000 px anyway), send it, and say
  // plainly when it fails. A plain form post of a big photo through a proxy with a small upload limit fails without a word.
  const uploadForm = upload.querySelector("form");
  const uploadError = upload.querySelector("[data-photo-upload-error]");
  const saveButton = upload.querySelector("[data-photo-save]");
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

  function uploadFailed(message) {
    uploadError.textContent = message;
    uploadError.hidden = false;
    saveButton.disabled = false;
    saveButton.textContent = "Save photo";
  }

  uploadForm.addEventListener("submit", async (event) => {
    const input = uploadForm.querySelector("input[type=file]");
    if (!input.files.length) return;                 // the browser's own "choose a photo" message
    event.preventDefault();
    uploadError.hidden = true;
    saveButton.disabled = true;
    saveButton.textContent = "Saving…";
    const data = new FormData(uploadForm);
    try {
      const small = await shrink(input.files[0]);
      if (small !== input.files[0]) data.set("file", small, "photo.jpg");
    } catch (error) { /* the browser could not read it: send the original and let the server decide */ }
    let response;
    try {
      response = await fetch(uploadForm.action, { method: "POST", body: data, credentials: "same-origin" });
    } catch (error) {
      uploadFailed("Could not reach Amide. Check your connection and try again.");
      return;
    }
    if (response.status === 413) { uploadFailed("The server or a proxy in front of it refused a photo this large. Its upload limit needs raising, or try a smaller photo."); return; }
    if (response.status === 422) {
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const reason = page.querySelector("#photo-upload-dialog .error");
      uploadFailed(reason ? reason.textContent.trim() : "That photo could not be saved.");
      return;
    }
    if (!response.ok) { uploadFailed("Something went wrong saving the photo (error " + response.status + "). Try again."); return; }
    window.location.assign("/measurements?tab=measurements#body-photos");
    window.location.reload();
  });

  root.querySelectorAll("form[data-photo-delete]").forEach((form) => form.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this photo? This cannot be undone.")) event.preventDefault();
  }));

  root.querySelectorAll(".photo-open").forEach((tile) => tile.addEventListener("click", () => {
    if (sharp) return;
    if (mode === "code") { unlock.showModal(); unlock.querySelector("input[name=code]").focus(); return; }
    const img = tile.querySelector("img");
    const hint = tile.querySelector(".photo-hint");
    const id = tile.dataset.photoId;
    const showing = img.dataset.revealed === "1";
    img.src = "/measurements/photos/" + id + (showing ? "/preview" : "/full");
    img.dataset.revealed = showing ? "0" : "1";
    if (hint) hint.hidden = !showing;
  }));

  const form = unlock.querySelector("[data-photo-unlock-form]");
  const error = unlock.querySelector("[data-photo-unlock-error]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    const response = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
    if (response.ok) { window.location.reload(); return; }
    const body = await response.json().catch(() => ({}));
    error.textContent = body.error || "Something went wrong. Try again.";
    error.hidden = false;
  });

  const lockButton = root.querySelector("[data-photo-lock]");
  if (lockButton) lockButton.addEventListener("click", async () => {
    await fetch("/measurements/photos/lock", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  });

  const countdown = root.querySelector("[data-photo-countdown]");
  if (countdown) {
    let left = Number(root.dataset.seconds);
    const tick = setInterval(() => {
      left -= 1;
      if (left <= 0) { clearInterval(tick); window.location.reload(); return; }
      const m = Math.floor(left / 60), s = String(left % 60).padStart(2, "0");
      countdown.textContent = m + ":" + s;
    }, 1000);
  }
})();
