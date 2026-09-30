// Weight & Measurements: the chart-range dropdown(s), the Overview chart's metric dropdown, and
// the Body silhouette's hover tooltip + click-through into that same chart.
(() => {
  // ---- Chart range: submit its form on change (mirrors calendar.js's view-select pattern) ----
  // A plain GET-form <select> already works with JS disabled via the <noscript> Show button next
  // to it; this just removes the extra click when JS is available.
  document.querySelectorAll(".range-select").forEach((sel) => {
    sel.addEventListener("change", () => sel.form.submit());
  });

  // ---- Overview chart: metric dropdown ----
  // All metrics' charts are already rendered server-side (the same data the "All measurements"
  // grid below uses) -- this just toggles which one is visible, so switching metrics is instant
  // and needs no round-trip. The chosen metric is remembered per-browser (localStorage) so it
  // survives a reload/range change, matching this codebase's other client-side "remember what I
  // picked" conveniences.
  const select = document.getElementById("overview-metric-select");
  const panels = document.querySelectorAll(".overview-chart-panel");
  const STORAGE_KEY = "amide-overview-metric";

  function syncPanels() {
    panels.forEach((p) => { p.hidden = p.dataset.metric !== select.value; });
  }

  function selectMetric(key) {
    if (![...panels].some((p) => p.dataset.metric === key)) return false;
    select.value = key;
    syncPanels();
    try { localStorage.setItem(STORAGE_KEY, key); } catch { /* non-fatal */ }
    return true;
  }

  if (select) {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) selectMetric(saved);
    } catch { /* private browsing / storage disabled -- fall back to the default selection */ }
    syncPanels();
    select.addEventListener("change", () => selectMetric(select.value));
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
    if (!value) return `<strong>${label}</strong><span class="muted">No data</span>`;
    const deltaText = delta ? ` (${Number(delta) > 0 ? "+" : ""}${delta})` : "";
    const parts = [`<strong>${label}</strong>`, `${value} in${deltaText}`];
    if (prior) parts.push(`<span class="muted">Previous: ${prior} in</span>`);
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
