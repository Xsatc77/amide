// Weight & Measurements: the Overview chart's metric dropdown. All metrics' charts are already
// rendered server-side (the same data the "All measurements" grid below uses) -- this just toggles
// which one is visible, so switching metrics is instant and needs no round-trip. The chosen metric
// is remembered per-browser (localStorage) so it survives a reload/range change, matching this
// codebase's other client-side "remember what I picked" conveniences.
(() => {
  const select = document.getElementById("overview-metric-select");
  if (!select) return;
  const panels = document.querySelectorAll(".overview-chart-panel");
  const STORAGE_KEY = "amide-overview-metric";

  function sync() {
    panels.forEach((p) => { p.hidden = p.dataset.metric !== select.value; });
  }

  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && [...panels].some((p) => p.dataset.metric === saved)) select.value = saved;
  } catch { /* private browsing / storage disabled -- fall back to the default selection */ }
  sync();

  select.addEventListener("change", () => {
    sync();
    try { localStorage.setItem(STORAGE_KEY, select.value); } catch { /* non-fatal */ }
  });
})();
