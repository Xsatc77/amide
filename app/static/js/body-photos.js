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
