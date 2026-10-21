// TRT calculator: reads the choices, asks the server for the arithmetic on every change and shows what comes back (no sums happen here). The address
// is kept in step with the choices, so "Copy a link" shares exactly this result.
(() => {
  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("trt-data").textContent);
  const capacityFor = new Map(data.capacities);
  const state = { ...data.state };
  const fmt = (n, digits = 4) => Number(Number(n).toPrecision(digits)).toString();

  // A row of preset chips with an "Other" box: a value that matches no preset opens the box.
  function presets(chipsId, otherWrapId, inputId, key) {
    const chips = $(chipsId), wrap = $(otherWrapId), input = $(inputId);
    const values = Array.from(chips.querySelectorAll("[data-value]")).map((c) => c.dataset.value);
    const press = (chip, on) => chip.setAttribute("aria-pressed", String(on));
    const show = () => {
      const isPreset = values.includes(String(state[key]));
      chips.querySelectorAll("[data-value]").forEach((c) => press(c, c.dataset.value === String(state[key])));
      press(chips.querySelector("[data-other]"), state[key] !== "" && !isPreset);
      wrap.hidden = isPreset || (state[key] === "" && !wrap.dataset.open);
      input.value = isPreset ? "" : state[key];
    };
    chips.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      if (chip.hasAttribute("data-other")) {
        wrap.dataset.open = "1";
        wrap.hidden = false;
        chips.querySelectorAll("[data-value]").forEach((c) => press(c, false));
        press(chip, true);
        input.focus();
        return;
      }
      state[key] = chip.dataset.value;
      delete wrap.dataset.open;
      show();
      recompute();
    });
    input.addEventListener("input", () => {
      state[key] = input.value;
      chips.querySelectorAll("[data-value]").forEach((c) => press(c, false));
      press(chips.querySelector("[data-other]"), true);
      recompute();
    });
    show();
  }

  function singleChoice(chipsId, key) {
    const chips = $(chipsId);
    chips.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      state[key] = chip.dataset.value;
      chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      buildScale();
      recompute();
    });
  }

  function buildScale() {
    const capacity = capacityFor.get(Number(state.syringe_ml)) || 100;
    const step = capacity <= 30 ? 5 : capacity <= 50 ? 10 : 20;
    $("trt-scale").replaceChildren();
    $("trt-ticks").replaceChildren();
    for (let u = 0; u <= capacity; u += step) {
      const pct = (u / capacity) * 100;
      const label = document.createElement("span");
      label.style.left = `${pct}%`;
      label.textContent = u;
      $("trt-scale").append(label);
      const tick = document.createElement("span");
      tick.className = "calc-tick";
      tick.style.left = `${pct}%`;
      $("trt-ticks").append(tick);
    }
  }

  const query = () => new URLSearchParams({ conc: state.conc, weekly: state.weekly, freq: state.freq, vial_ml: state.vial_ml, syringe_ml: state.syringe_ml });

  async function recompute() {
    const params = query();
    history.replaceState(null, "", `${location.pathname}?${params}`);
    const r = await (await fetch(`/api/calculator/trt?${params}`)).json();
    render(r);
  }

  function render(r) {
    const capacity = capacityFor.get(Number(state.syringe_ml)) || 100;
    const warning = $("trt-warning");
    if (r.problems.length) {
      warning.hidden = false;
      warning.textContent = `Enter ${r.problems.join(", ")} to see the draw.`;
      $("trt-units").innerHTML = '—<span class="calc-units-label">units per injection</span>';
      ["trt-per-week", "trt-mg", "trt-ml", "trt-whole", "trt-weekly-ml", "trt-vial-days"].forEach((id) => { $(id).textContent = "—"; });
      $("trt-fill").style.width = "0%";
      return;
    }
    warning.hidden = !r.over_capacity;
    if (r.over_capacity) {
      warning.textContent = `This draw (${fmt(r.units, 3)} units) is more than the ${state.syringe_ml} mL syringe holds (${capacity} units). Use a bigger syringe, a stronger vial or more injections a week.`;
    }
    $("trt-units").innerHTML = `${fmt(r.units, 3)}<span class="calc-units-label">units per injection</span>`;
    $("trt-per-week").textContent = fmt(r.injections_per_week);
    $("trt-mg").textContent = `${fmt(r.mg_per_injection)} mg`;
    $("trt-ml").textContent = `${fmt(r.ml_per_injection)} mL`;
    $("trt-whole").textContent = `${r.units_whole} units = ${fmt(r.mg_at_whole_units)} mg`;
    $("trt-weekly-ml").textContent = `${fmt(r.weekly_ml)} mL`;
    $("trt-vial-days").textContent = r.vial_days == null ? "—" : `${fmt(r.vial_days, 3)} days (about ${fmt(r.vial_days / 7, 3)} weeks)`;
    $("trt-fill").style.width = `${Math.min(100, (r.units / capacity) * 100)}%`;
    $("trt-fill").classList.toggle("over", r.over_capacity);
  }

  presets("trt-conc-chips", "trt-conc-other", "trt-conc", "conc");
  presets("trt-weekly-chips", "trt-weekly-other", "trt-weekly", "weekly");
  singleChoice("trt-freq-chips", "freq");
  singleChoice("trt-syringe-chips", "syringe_ml");
  $("trt-vial").addEventListener("input", (e) => { state.vial_ml = e.target.value; recompute(); });

  $("trt-copy").addEventListener("click", async () => {
    const link = `${location.origin}${location.pathname}?${query()}`;
    try {
      await navigator.clipboard.writeText(link);
      $("trt-copy-note").textContent = "Link copied.";
    } catch {
      $("trt-copy-note").textContent = link;
    }
  });

  buildScale();
  recompute();
})();
