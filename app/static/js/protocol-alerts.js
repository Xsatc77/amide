// Protocol alerts: the icon beside Print opens the report in a dialog; the builder's panel previews the form. Informational only.
(() => {
  function render(target, data) {
    target.textContent = "";
    if (data.incomplete) { target.textContent = "Finish the protocol (name, dates and at least one item) to check it."; return; }
    if (!data.findings.length) { target.textContent = "No alerts. That does not mean there is no interaction; the checks are short on purpose."; return; }
    const list = document.createElement("ul");
    list.className = "alert-list";
    data.findings.forEach((f) => {
      const item = document.createElement("li");
      item.className = `alert-item alert-${f.severity}`;
      const label = document.createElement("strong");
      label.textContent = f.severity === "caution" ? "Caution " : "Note ";
      item.append(label, document.createTextNode(f.message));
      if (f.peptide_id) {
        const link = document.createElement("a");
        link.href = `/library/${f.peptide_id}`;
        link.textContent = " Library card";
        item.append(link);
      }
      list.append(item);
    });
    target.append(list);
  }

  const dialog = document.getElementById("alerts-dialog");
  if (dialog) {
    document.querySelectorAll(".alerts-btn").forEach((button) => button.addEventListener("click", async () => {
      const id = button.dataset.protocolId;
      const body = document.getElementById("alerts-body");
      body.textContent = "Checking…";
      document.getElementById("alerts-page").href = `/protocols/${id}/alerts`;
      dialog.showModal();
      try {
        const data = await (await fetch(`/protocols/${id}/alerts.json`)).json();
        render(body, data);
        document.getElementById("alerts-disclaimer").textContent = data.disclaimer;
      } catch (error) { body.textContent = "Could not load the alerts."; }
    }));
    dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("mousedown", (e) => { dialog._backdrop = e.target === dialog; });
    dialog.addEventListener("click", (e) => { if (dialog._backdrop && e.target === dialog) dialog.close(); });
  }

  const check = document.getElementById("alerts-check");
  if (check) check.addEventListener("click", async () => {
    const out = document.getElementById("alerts-out");
    out.textContent = "Checking…";
    try {
      const form = document.getElementById("builder-form");
      const response = await fetch("/protocols/alerts-preview", { method: "POST", body: new URLSearchParams(new FormData(form)) });
      const data = await response.json();
      render(out, data);
      const note = document.createElement("p");
      note.className = "small muted";
      note.textContent = data.disclaimer;
      out.append(note);
    } catch (error) { out.textContent = "Could not check right now."; }
  });
})();
