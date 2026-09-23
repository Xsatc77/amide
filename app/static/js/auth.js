// Sign-in screens: the notice checkbox, show/hide password eyes, and the live password checklist.
(() => {
  // Legal notice: Okay only works once the box is ticked.
  const check = document.getElementById("notice-check");
  const ok = document.getElementById("notice-ok");
  if (check && ok) {
    const sync = () => { ok.disabled = !check.checked; };
    check.addEventListener("change", sync);
    sync();
  }

  // Eye buttons toggle between hidden and visible password text.
  document.querySelectorAll("[data-eye]").forEach((btn) => {
    const input = document.getElementById(btn.dataset.eye);
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-pressed", String(show));
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      input.focus();
    });
  });

  // New user: tick each password rule as it's met.
  const rules = document.getElementById("pw-rules");
  if (rules) {
    const pw = document.getElementById("password");
    const confirm = document.getElementById("confirm");
    const min = Number(rules.dataset.min);
    const tests = {
      length: (p) => p.length >= min,
      upper: (p) => /[A-Z]/.test(p),
      lower: (p) => /[a-z]/.test(p),
      number: (p) => /[0-9]/.test(p),
      special: (p) => /[^A-Za-z0-9]/.test(p),
      match: (p, c) => p.length > 0 && p === c,
    };
    const update = () => {
      for (const li of rules.querySelectorAll("[data-rule]")) {
        li.classList.toggle("met", tests[li.dataset.rule](pw.value, confirm.value));
      }
    };
    pw.addEventListener("input", update);
    confirm.addEventListener("input", update);
    update();
  }
})();
