// Apple Health card: Copy buttons (data-copy). Falls back to selecting the text beside the button when the clipboard is unavailable.
document.querySelectorAll("#apple-health [data-copy]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const label = btn.textContent;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      btn.textContent = "Copied";
    } catch {
      const code = btn.previousElementSibling;
      if (code && code.tagName === "CODE") {
        const range = document.createRange();
        range.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      btn.textContent = "Select and copy";
    }
    setTimeout(() => { btn.textContent = label; }, 1500);
  })
);
// Forms that ask first (Revoke a token).
document.querySelectorAll("#apple-health form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (event) => { if (!window.confirm(f.dataset.confirm)) event.preventDefault(); }));
