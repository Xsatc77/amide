// Workout plan editor (workouts/edit.html): add/remove days and exercise rows, mirroring
// inventory.js's addLine/addContactRow <template> + "__I__" placeholder convention.
//
// The server pairs day_label[]/day_id[] (flat, in order) with exercise_*[N][] by position: the
// N-th day's exercises must be named exercise_*[N][]. So after a day is removed, every remaining
// day is renumbered (renumberDays) to keep that pairing intact, and a new day always takes the
// next index after the current last one.
(() => {
  const form = document.getElementById("workout-plan-form");
  if (!form) return;
  const daysContainer = document.getElementById("days");
  const dayTemplate = document.getElementById("workout-day-template");
  const rowTemplate = document.getElementById("workout-exercise-row-template");
  const hasLoggedHistory = form.hasAttribute("data-has-logged-history");

  function days() {
    return [...daysContainer.querySelectorAll("[data-day]")];
  }

  function setIndex(scope, index) {
    scope.querySelectorAll("[name^='exercise_'], [name^='weekdays[']").forEach((el) => {
      el.name = el.name.replace(/\[(?:\d+|__I__)\]\[\]$/, `[${index}][]`);
    });
  }

  function renumberDays() {
    days().forEach((fieldset, i) => {
      setIndex(fieldset, i);
      fieldset.querySelector("[data-day-number]").textContent = String(i + 1);
    });
  }

  function isSaved(el, idField) {
    // A server-rendered row carries its real id; one added in the browser posts an empty id.
    return !!el.querySelector(`[name^='${idField}']`)?.value;
  }

  function confirmRemoval(el, idField, what) {
    if (!hasLoggedHistory || !isSaved(el, idField)) return true;
    return confirm(`Remove this ${what}? Workouts you already logged stay in your history.`);
  }

  function addExercise(fieldset) {
    const index = days().indexOf(fieldset);
    const fragment = rowTemplate.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(index));
    });
    const row = fragment.querySelector("[data-exercise-row]");
    fieldset.querySelector("[data-exercise-rows]").appendChild(fragment);
    row.querySelector("[name^='exercise_name']").focus();
  }

  function addDay() {
    const index = days().length;
    const fragment = dayTemplate.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace(/__I__/g, String(index));
    });
    const fieldset = fragment.querySelector("[data-day]");
    fieldset.querySelector("[data-day-number]").textContent = String(index + 1);
    daysContainer.appendChild(fragment);
    fieldset.querySelector("[name='day_label[]']").focus();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn || !form.contains(btn)) return;
    const action = btn.dataset.action;
    if (action === "add-day") {
      addDay();
    } else if (action === "remove-day") {
      const fieldset = btn.closest("[data-day]");
      if (!confirmRemoval(fieldset, "day_id", "day")) return;
      fieldset.remove();
      renumberDays();
    } else if (action === "add-exercise") {
      addExercise(btn.closest("[data-day]"));
    } else if (action === "remove-exercise") {
      const row = btn.closest("[data-exercise-row]");
      if (!confirmRemoval(row, "exercise_id", "exercise")) return;
      row.remove();
    }
  });

  // A brand-new plan starts with one blank day rather than an empty form.
  if (form.hasAttribute("data-new-plan") && days().length === 0) addDay();

  // The "Use X" / "Set" estimator buttons submit their own small forms, which would drop anything typed in the plan
  // form above; ask first when there are unsaved changes.
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  document.addEventListener("click", (e) => {
    const btn = e.target.closest('button[form^="match-"]');
    if (btn && dirty && !confirm("You have unsaved changes to this plan; matching now will discard them. Save the plan first, or continue anyway?")) {
      e.preventDefault();
    }
  });
})();