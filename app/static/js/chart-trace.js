// Dashboard charts marked data-trace: a short light runs along the data line, a little slower than the body outline's.
//   data-trace="blue"       Apple Health: a blue light; each data point pulses bright as the light reaches it, then fades.
//   data-trace="direction"  Weight: one light, green while the line falls, red while it rises (the colour changes mid-run, the motion does not);
//                           its points pulse the same way, in the light's own colour.
(() => {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;
  const NS = "http://www.w3.org/2000/svg";
  const PASS_MS = 14000, PAUSE_MS = 1200, GREEN = "#3ddc6f", RED = "#ef4444", BLUE = "#4aa8ff";

  document.querySelectorAll("svg[data-trace]").forEach((svg) => {
    const line = svg.querySelector("polyline:not(.chart-trace-line)[points]");
    const dots = [...svg.querySelectorAll("circle.chart-point:not(.dose-dot)")];
    if (!line || dots.length < 2) return;
    const pts = dots.map((c) => [parseFloat(c.getAttribute("cx")), parseFloat(c.getAttribute("cy"))]);
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const total = cum[cum.length - 1];
    if (!(total > 0)) return;
    const mode = svg.dataset.trace;
    const trace = document.createElementNS(NS, "polyline");
    trace.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
    trace.setAttribute("class", "chart-trace-line");
    line.after(trace);
    const tail = total * 0.12;                                   // the lit stretch behind the head
    const colourAt = (d) => {
      if (mode !== "direction") return BLUE;
      let i = 1;
      while (i < cum.length - 1 && cum[i] < d) i++;
      const dy = pts[i][1] - pts[i - 1][1];                      // SVG y grows downward: a larger y is a lower weight
      return dy > 0.5 ? GREEN : dy < -0.5 ? RED : trace.style.color || GREEN;
    };
    let last = 0, start = null, nextDot = 0;
    const frame = (now) => {
      if (document.hidden || !svg.isConnected) { start = null; requestAnimationFrame(frame); return; }
      if (start === null) { start = now; nextDot = 0; last = 0; }
      const t = (now - start) % (PASS_MS + PAUSE_MS);
      if (t > PASS_MS) { trace.setAttribute("stroke-dasharray", "0 " + (total * 2)); nextDot = 0; requestAnimationFrame(frame); return; }
      const head = (t / PASS_MS) * (total + tail);
      const from = Math.max(0, head - tail), to = Math.min(total, head);
      if (head < last) nextDot = 0;
      last = head;
      const colour = colourAt(Math.min(total, Math.max(from, to - 0.01)));
      trace.style.color = colour;
      trace.setAttribute("stroke", colour);
      trace.setAttribute("stroke-dasharray", `${Math.max(0, to - from)} ${total * 2}`);
      trace.setAttribute("stroke-dashoffset", String(-from));
      while (nextDot < dots.length && cum[nextDot] <= to) {      // the light reached this point: energize it
        { const c = dots[nextDot]; c.style.setProperty("--pulse", colour); c.classList.remove("chart-energized"); void c.getBoundingClientRect(); c.classList.add("chart-energized"); }
        nextDot++;
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
})();
