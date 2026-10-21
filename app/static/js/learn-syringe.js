// Learn page: a teaching aid for syringe units. Two vials of the same size are mixed with different amounts of water; the same number of
// units drawn from each holds a different amount of peptide. U-100 syringes only (100 units = 1 mL). Every number here is made up.
(function () {
  var host = document.querySelector("[data-learn-syringe]");
  if (!host) return;
  var NS = "http://www.w3.org/2000/svg";
  var UNITS_PER_ML = 100;
  var state = { size: 1, vialMg: 10, waterA: 2, waterB: 1, units: 10, wanted: 0.5 };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }
  function svg(tag, attrs, text) {
    var node = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }
  function fmt(x, places) {
    var s = (Math.round(x * Math.pow(10, places)) / Math.pow(10, places)).toString();
    return s;
  }

  function drawn(units, mg, water) {
    var conc = mg / water;               // mg per mL
    var ml = units / UNITS_PER_ML;
    return { conc: conc, ml: ml, mg: conc * ml };
  }

  function syringe(cap, units, label) {
    var X0 = 56, LEN = 210, TOP = 18, H = 40;
    var s = svg("svg", { viewBox: "0 0 330 76", class: "learn-syringe-svg", role: "img", "aria-label": label + ": " + units + " units drawn on a " + cap + "-unit syringe" });
    var stopperX = X0 + (units / cap) * LEN;
    s.appendChild(svg("line", { x1: 4, y1: TOP + H / 2, x2: 44, y2: TOP + H / 2, class: "sy-needle" }));
    s.appendChild(svg("rect", { x: 40, y: TOP + H / 2 - 6, width: 16, height: 12, class: "sy-hub" }));
    s.appendChild(svg("rect", { x: X0, y: TOP, width: LEN, height: H, rx: 2, class: "sy-barrel" }));
    if (units > 0) s.appendChild(svg("rect", { x: X0, y: TOP + 1, width: stopperX - X0, height: H - 2, class: "sy-liquid" }));
    var minorEvery = cap <= 50 ? 1 : 2, majorEvery = cap <= 30 ? 5 : 10;
    for (var u = 0; u <= cap; u += minorEvery) {
      var x = X0 + (u / cap) * LEN, major = u % majorEvery === 0;
      s.appendChild(svg("line", { x1: x, y1: TOP, x2: x, y2: TOP + (major ? 12 : 6), class: major ? "sy-tick sy-major" : "sy-tick" }));
      if (major) s.appendChild(svg("text", { x: x, y: TOP + 24, class: "sy-num", "text-anchor": "middle" }, String(u)));
    }
    s.appendChild(svg("rect", { x: stopperX - 6, y: TOP + 2, width: 6, height: H - 4, class: "sy-stopper" }));
    s.appendChild(svg("rect", { x: stopperX, y: TOP + H / 2 - 2.5, width: 304 - stopperX, height: 5, class: "sy-rod" }));
    s.appendChild(svg("rect", { x: X0 + LEN, y: TOP - 6, width: 6, height: H + 12, rx: 1, class: "sy-flange" }));
    s.appendChild(svg("rect", { x: 304, y: TOP + 2, width: 6, height: H - 4, rx: 1, class: "sy-thumb" }));
    s.appendChild(svg("text", { x: 4, y: 72, class: "sy-cap" }, label));
    return s;
  }

  var root = el("div", { class: "learn-syringe-card" });
  host.appendChild(root);

  function field(labelText, input) {
    var l = el("label", { class: "learn-sy-field" });
    l.appendChild(el("span", {}, labelText));
    l.appendChild(input);
    return l;
  }
  function number(value, min, max, step, onInput) {
    var i = el("input", { type: "number", inputmode: "decimal", min: min, max: max, step: step, value: value });
    i.addEventListener("input", function () { onInput(parseFloat(i.value)); });
    return i;
  }

  var controls = el("div", { class: "learn-sy-controls" });
  var sizeSel = el("select", {});
  [[0.3, "0.3 mL (30 units)"], [0.5, "0.5 mL (50 units)"], [1, "1 mL (100 units)"]].forEach(function (o) {
    var opt = el("option", { value: o[0] }, o[1]);
    if (o[0] === state.size) opt.selected = true;
    sizeSel.appendChild(opt);
  });
  sizeSel.addEventListener("change", function () {
    state.size = parseFloat(sizeSel.value);
    var cap = state.size * UNITS_PER_ML;
    range.max = cap;
    if (state.units > cap) { state.units = cap; range.value = cap; }
    update();
  });
  controls.appendChild(field("Syringe", sizeSel));
  controls.appendChild(field("Peptide in each vial (mg)", number(state.vialMg, 0.1, 100, "any", function (v) { state.vialMg = v; update(); })));
  controls.appendChild(field("Water in vial A (mL)", number(state.waterA, 0.1, 10, "any", function (v) { state.waterA = v; update(); })));
  controls.appendChild(field("Water in vial B (mL)", number(state.waterB, 0.1, 10, "any", function (v) { state.waterB = v; update(); })));
  root.appendChild(controls);

  var range = el("input", { type: "range", min: 0, max: state.size * UNITS_PER_ML, step: 1, value: state.units, "aria-label": "Units drawn" });
  range.addEventListener("input", function () { state.units = parseInt(range.value, 10); update(); });
  var unitsLabel = el("div", { class: "learn-sy-units" });
  root.appendChild(unitsLabel);
  root.appendChild(range);

  var message = el("p", { class: "learn-sy-message", role: "alert" });
  root.appendChild(message);
  var picture = el("div", { class: "learn-sy-pictures" });
  root.appendChild(picture);

  var back = el("div", { class: "learn-sy-back" });
  back.appendChild(el("h4", {}, "Working Backward"));
  back.appendChild(el("p", {}, "Say a label or a note tells you an amount in mg, and you want to know how many units that is for each vial."));
  var wantedInput = number(state.wanted, 0, 100, "any", function (v) { state.wanted = v; update(); });
  back.appendChild(field("Amount wanted (mg)", wantedInput));
  var backOut = el("p", { class: "learn-sy-backout" });
  back.appendChild(backOut);
  root.appendChild(back);

  root.appendChild(el("p", { class: "learn-sy-note" }, "These are U-100 syringes, where 100 units is 1 mL. Check what your syringe says before you trust any chart. The numbers are made up and are not dose suggestions."));

  function valid() {
    return [state.vialMg, state.waterA, state.waterB].every(function (v) { return isFinite(v) && v > 0; });
  }

  function mgText(mg) {
    return mg >= 1 ? fmt(mg, 3) + " mg" : fmt(mg, 3) + " mg (" + fmt(mg * 1000, 1) + " mcg)";
  }

  function update() {
    unitsLabel.textContent = state.units + " units drawn = " + fmt(state.units / UNITS_PER_ML, 3) + " mL";
    picture.textContent = "";
    backOut.textContent = "";
    if (!valid()) {
      message.textContent = "Enter an amount of peptide and an amount of water above zero.";
      return;
    }
    message.textContent = "";
    var cap = state.size * UNITS_PER_ML;
    [["Vial A", state.waterA], ["Vial B", state.waterB]].forEach(function (v) {
      var d = drawn(state.units, state.vialMg, v[1]);
      var box = el("div", { class: "learn-sy-one" });
      box.appendChild(el("h4", {}, v[0] + ": " + fmt(state.vialMg, 3) + " mg in " + fmt(v[1], 3) + " mL"));
      box.appendChild(el("p", {}, "Strength: " + fmt(d.conc, 3) + " mg/mL"));
      box.appendChild(syringe(cap, state.units, v[0]));
      box.appendChild(el("p", { class: "learn-sy-result" }, state.units + " units holds " + mgText(d.mg)));
      picture.appendChild(box);
    });
    if (isFinite(state.wanted) && state.wanted > 0) {
      var parts = [["Vial A", state.waterA], ["Vial B", state.waterB]].map(function (v) {
        var units = (state.wanted / (state.vialMg / v[1])) * UNITS_PER_ML;
        var note = units > cap ? " (more than this syringe holds)" : "";
        return v[0] + ": " + fmt(units, 1) + " units" + note;
      });
      backOut.textContent = fmt(state.wanted, 3) + " mg ÷ strength = mL, then × 100 = units. " + parts.join("; ") + ".";
    }
  }
  update();
})();
