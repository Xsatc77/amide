// Every chart: hover a plotted point (mouse) or tap it (touch) to see its date and value in a small popup. A point carries data-date and data-value,
// or an SVG <title> (the pie slices, bars and a few older charts); the <title> is moved into data-tip so the browser's own slow tooltip does not double up.
(() => {
  const tip = document.createElement("div");
  tip.className = "chart-tooltip";
  tip.hidden = true;
  tip.setAttribute("role", "status");
  document.body.appendChild(tip);

  const SHAPES = "circle, rect, path, g";
  const TOUCH_REACH = 26;                      // px: a fingertip is bigger than a 2.5px dot, so a tap picks the nearest point this close

  function textOf(el) {
    if (el.dataset.tip) return el.dataset.tip;
    if (el.dataset.date && el.dataset.value) return `${el.dataset.date}: ${el.dataset.value}`;
    const title = Array.from(el.children).find((c) => c.tagName.toLowerCase() === "title");
    if (!title) return "";
    el.dataset.tip = title.textContent.trim();
    title.remove();
    return el.dataset.tip;
  }

  function pointAt(target) {
    const el = target instanceof Element ? target.closest(SHAPES) : null;
    return el && el.closest("svg") && textOf(el) ? el : null;
  }

  function nearest(svg, x, y) {
    let best = null, bestD = TOUCH_REACH;
    svg.querySelectorAll("circle").forEach((c) => {
      if (!textOf(c)) return;
      const r = c.getBoundingClientRect();
      const d = Math.hypot(r.left + r.width / 2 - x, r.top + r.height / 2 - y);
      if (d < bestD) { best = c; bestD = d; }
    });
    return best;
  }

  function show(el, x, y) {
    tip.textContent = textOf(el);
    tip.hidden = false;
    const r = el.tagName.toLowerCase() === "circle" ? el.getBoundingClientRect() : null;
    const cx = r ? r.left + r.width / 2 : x, top = r ? r.top : y;
    const w = tip.offsetWidth;
    tip.style.left = `${Math.max(4, Math.min(window.innerWidth - w - 4, cx - w / 2))}px`;
    tip.style.top = `${Math.max(4, top - tip.offsetHeight - 8)}px`;
  }

  document.addEventListener("pointerover", (e) => {
    if (e.pointerType === "touch") return;
    const el = pointAt(e.target);
    if (el) show(el, e.clientX, e.clientY);
  });
  document.addEventListener("pointerout", (e) => {
    if (e.pointerType !== "touch" && pointAt(e.target)) tip.hidden = true;
  });
  document.addEventListener("click", (e) => {
    if (e.pointerType !== "touch" && e.detail !== 0 && !matchMedia("(hover: none)").matches) return;      // a mouse already has hover
    const svg = e.target instanceof Element ? e.target.closest("svg") : null;
    const el = svg && (pointAt(e.target) || nearest(svg, e.clientX, e.clientY));
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  window.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
})();
