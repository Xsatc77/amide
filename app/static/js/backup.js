// Backup & restore page: passphrase confirmation, the whole-installation toggle and the typed RESTORE confirmation.
// Nothing is stored in the browser; the server checks everything again.
(() => {
  document.querySelectorAll("[data-backup-form]").forEach((form) => {
    const first = form.querySelector("[data-passphrase]");
    const second = form.querySelector("[data-passphrase-confirm]");
    if (first && second) {
      const check = () => second.setCustomValidity(first.value === second.value ? "" : "The two passphrases do not match.");
      first.addEventListener("input", check);
      second.addEventListener("input", check);
    }
    const sections = form.querySelector("[data-sections]");
    form.querySelectorAll("[data-scope]").forEach((radio) => {
      radio.addEventListener("change", () => {
        if (sections) sections.hidden = form.querySelector("[data-scope]:checked").value === "installation";
      });
    });
  });

  const restore = document.querySelector("[data-restore-form]");
  if (restore) {
    const word = restore.querySelector("[data-restore-word]");
    const button = restore.querySelector("[data-restore-submit]");
    const sync = () => { button.disabled = word.value.trim() !== word.dataset.restoreWord; };
    word.addEventListener("input", sync);
    sync();
  }
})();
