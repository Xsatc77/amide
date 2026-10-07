// Orders tab: copy buttons (a tracking number, or the number before opening a site that cannot take it in its address).
document.querySelectorAll("[data-copy]").forEach((el) => {
  el.addEventListener("click", () => {
    const text = el.dataset.copy;
    if (!text) return;
    try { navigator.clipboard.writeText(text); } catch (e) { /* clipboard blocked: the number is on screen to copy by hand */ }
    if (el.tagName === "BUTTON") {
      const label = el.textContent;
      el.textContent = "Copied";
      setTimeout(() => { el.textContent = label; }, 1500);
    }
  });
});
