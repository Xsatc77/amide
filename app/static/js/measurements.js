// Weight & Measurements: the chart-range dropdown(s), the Overview chart's metric dropdown, and
// the Body silhouette's hover tooltip + click-through into that same chart.
(() => {
  // ---- Chart range: submit its form on change (mirrors calendar.js's view-select pattern) ----
  // A plain GET-form <select> already works with JS disabled via the <noscript> Show button next
  // to it; this just removes the extra click when JS is available.
  document.querySelectorAll(".range-controls select").forEach((sel) => {
    sel.addEventListener("change", () => sel.form.submit());
  });

  // ---- Overview chart: metric dropdown ----
  // All metrics' charts are already rendered server-side (the same data the "All measurements"
  // grid below uses) -- this just toggles which one is visible, so switching metrics is instant
  // and needs no round-trip. Nothing is stored in the browser, so a reload or range change
  // returns to the default metric.
  const select = document.getElementById("overview-metric-select");
  const panels = document.querySelectorAll(".overview-chart-panel");

  function syncPanels() {
    panels.forEach((p) => { p.hidden = p.dataset.metric !== select.value; });
  }

  function selectMetric(key) {
    if (![...panels].some((p) => p.dataset.metric === key)) return false;
    select.value = key;
    syncPanels();
    return true;
  }

  if (select) {
    syncPanels();
    select.addEventListener("change", () => selectMetric(select.value));
  }

  // ---- Overview chart: hover a plotted point to see its exact date and value ----
  const chartTooltip = document.getElementById("chart-tooltip");
  const chartSlot = document.getElementById("overview-chart-slot");
  if (chartTooltip && chartSlot) {
    chartSlot.addEventListener("mouseover", (e) => {
      const pt = e.target.closest(".chart-point");
      if (!pt) return;
      chartTooltip.textContent = `${pt.dataset.date}: ${pt.dataset.value}`;
      chartTooltip.hidden = false;
      const r = pt.getBoundingClientRect();
      chartTooltip.style.left = `${r.left + r.width / 2}px`;
      chartTooltip.style.top = `${r.top}px`;
      chartTooltip.style.transform = "translate(-50%, calc(-100% - 8px))";
    });
    chartSlot.addEventListener("mouseout", (e) => {
      if (e.target.closest(".chart-point")) chartTooltip.hidden = true;
    });
  }

  // ---- Log-a-measurement dialog: open/close (mirrors labs.js's open-lab-panel pattern) ----
  const entryDialog = document.getElementById("measurement-dialog");
  if (entryDialog) {
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      if (btn.dataset.action === "open-measurement-dialog") {
        entryDialog.showModal();
      } else if (btn.dataset.action === "close" && entryDialog.contains(btn)) {
        entryDialog.close();
      }
    });
    // See labs.js's identical backdrop-close comment: mousedown is tracked too so a
    // text-selection drag that starts inside the form and ends on the backdrop doesn't close it.
    let mousedownOnBackdrop = false;
    entryDialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === entryDialog; });
    entryDialog.addEventListener("click", (e) => {
      if (mousedownOnBackdrop && e.target === entryDialog) entryDialog.close();
    });
    if (entryDialog.hasAttribute("data-open-on-load")) entryDialog.showModal();
  }

  // ---- Body silhouette: hover tooltip + click-through to the Overview chart ----
  const tooltip = document.getElementById("silhouette-tooltip");
  const wrap = document.querySelector(".silhouette-wrap");
  if (!tooltip || !wrap) return;

  function tooltipHtml(point) {
    const label = point.dataset.label;
    const value = point.dataset.value;
    const prior = point.dataset.prior;
    const delta = point.dataset.delta;
    const asOf = point.dataset.asOf;
    const unit = point.dataset.unit || "in";
    if (!value) return `<strong>${label}</strong><span class="muted">No data</span>`;
    const deltaText = delta ? ` (${Number(delta) > 0 ? "+" : ""}${delta})` : "";
    const parts = [`<strong>${label}</strong>`, `${value} ${unit}${deltaText}`];
    if (prior) parts.push(`<span class="muted">Previous: ${prior} ${unit}</span>`);
    if (asOf) parts.push(`<span class="muted">As of ${asOf}</span>`);
    return parts.join("<br>");
  }

  function showTooltip(point) {
    tooltip.innerHTML = tooltipHtml(point);
    tooltip.hidden = false;
    const wrapRect = wrap.getBoundingClientRect();
    const pointRect = point.getBoundingClientRect();
    let left = pointRect.left - wrapRect.left + pointRect.width / 2 + 10;
    let top = pointRect.top - wrapRect.top - 6;
    // Keep the tooltip inside the wrap's own box rather than spilling past its right edge.
    const maxLeft = wrapRect.width - tooltip.offsetWidth - 4;
    if (left > maxLeft) left = pointRect.left - wrapRect.left - tooltip.offsetWidth - 10;
    tooltip.style.left = `${Math.max(4, left)}px`;
    tooltip.style.top = `${Math.max(4, top)}px`;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  document.querySelectorAll(".silhouette-point").forEach((point) => {
    point.addEventListener("mouseenter", () => showTooltip(point));
    point.addEventListener("mouseleave", hideTooltip);
    point.addEventListener("focus", () => showTooltip(point));
    point.addEventListener("blur", hideTooltip);
    point.addEventListener("click", () => selectMetric(point.dataset.chartKey));
    point.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectMetric(point.dataset.chartKey);
      }
    });
  });
})();

// Delete buttons on this page (measurements, journal entries, water entries) ask first.
document.querySelectorAll("form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (event) => { if (!window.confirm(f.dataset.confirm)) event.preventDefault(); }));
