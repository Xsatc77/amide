// The AI summary can take a minute: show that it is working and stop a second press.
document.querySelectorAll("form[action='/progress/summary']").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit]");
    if (!button) return;
    button.disabled = true;
    button.textContent = button.dataset.busyText || "Working...";
  });
});
