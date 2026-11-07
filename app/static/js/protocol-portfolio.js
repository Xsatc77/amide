// Protocols page: the "Protocol Portfolio" button makes the PDF on the server and saves it, saying so while it works.
(() => {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".portfolio-btn");
    if (!button || button.disabled) return;
    const label = button.querySelector(".portfolio-label");
    const original = label.textContent;
    button.disabled = true;
    label.textContent = "Making The PDF…";
    try {
      const response = await fetch(button.dataset.portfolioUrl, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Server error " + response.status);
      const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await response.blob());
      link.download = name ? name[1] : "protocol-portfolio.pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
      label.textContent = "Saved";
    } catch (err) {
      label.textContent = "Could Not Make The PDF (" + (err && /^Server error/.test(err.message) ? err.message : "no answer from the server") + "). Try Again";
    }
    setTimeout(() => { label.textContent = original; button.disabled = false; }, 2500);
  });
})();
