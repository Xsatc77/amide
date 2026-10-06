// Progress tab: changing the exercise picker re-loads the page for that exercise (no state is kept in the browser).
(() => {
  const select = document.querySelector("select[data-autosubmit]");
  if (select) select.addEventListener("change", () => select.form.submit());
})();
