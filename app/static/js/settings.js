// Settings page behaviour: two-step delete-user confirmation with a deliberate backup prompt.
document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  const action = trigger.dataset.action;

  if (action === "delete-user") {
    const step1 = document.getElementById("delete-user-step1");
    const step2 = document.getElementById("delete-user-step2");
    const username = trigger.dataset.username;
    const userId = trigger.dataset.userId;
    for (const dialog of [step1, step2]) {
      dialog.querySelectorAll('[data-fill="username"]').forEach((el) => { el.textContent = username; });
    }
    step2.querySelector("[data-delete-form]").action = `/settings/admin/users/${userId}/delete`;
    step2.querySelector("[data-confirm-input]").value = "";
    step2.querySelector("[data-action=continue-delete]").disabled = true;
    step1.showModal();
  } else if (action === "delete-step2") {
    document.getElementById("delete-user-step1").close();
    document.getElementById("delete-user-step2").showModal();
  } else if (action === "cancel-delete") {
    const dialog = trigger.closest("dialog");
    if (dialog) dialog.close();
  }
});

document.addEventListener("input", (event) => {
  if (!event.target.matches("[data-confirm-input]")) return;
  const dialog = event.target.closest("dialog");
  const expected = dialog.querySelector('[data-fill="username"]').textContent;
  dialog.querySelector("[data-action=continue-delete]").disabled = event.target.value !== expected;
});
