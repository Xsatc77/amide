// Protocol builder: renders goals, suggested peptides, dose rows and titration steps from the JSON the
// server embeds, and keeps them as ordinary named form inputs so the form posts like any other.
(() => {
  const data = JSON.parse(document.getElementById("builder-data").textContent);
  const form = document.getElementById("builder-form");
  const $ = (id) => document.getElementById(id);
  const els = {
    goals: $("b-goals"), suggest: $("b-suggest"), items: $("b-items"), itemsEmpty: $("b-items-empty"),
    addInput: $("b-add-input"), addBtn: $("b-add-btn"), addMsg: $("b-add-msg"), datalist: $("b-peptide-list"),
    name: $("b-name"), start: $("b-start"), end: $("b-end"), weeks: $("b-weeks"), titration: $("b-titration"),
  };

  const peptideById = new Map(data.peptides.map((p) => [String(p.id), p]));
  const peptideByName = new Map(data.peptides.map((p) => [p.name.toLowerCase(), p]));
  const goalBySlug = new Map(data.goals.map((g) => [g.slug, g]));
  // Fields rendered by the server template show their own errors.
  const SERVER_FIELDS = new Set(["name", "start_date", "end_date", "weeks"]);

  let uid = 0;
  const newItem = (fields = {}) => ({
    uid: ++uid, peptide_id: "", new_name: "", dose: "", dose_unit: "mg", frequency: "daily", every_n_days: "",
    weekdays: "", time_of_day: "any", route: "subq", inventory_item_id: "", notes: "", steps: [],
    // Blank values from a re-shown form fall back to the defaults above.
    ...Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== "")),
  });

  let goals = [...data.state.goals];
  let items = data.state.items.map((it) => newItem({ ...it, steps: it.steps.map((s) => ({ ...s })) }));
  let errors = { ...data.errors };

  // ------------------------------------------------------------ helpers
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null) el.append(c);
    return el;
  }

  function select(name, options, value, onChange) {
    return h("select", { name, onchange: (e) => onChange(e.target.value) },
      options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
  }

  const itemName = (it) => (it.peptide_id ? peptideById.get(it.peptide_id)?.name ?? "Unknown peptide" : it.new_name);
  const hasPeptide = (id) => items.some((it) => it.peptide_id === String(id));

  function suggestedIds() {
    const out = [];
    for (const g of goals) for (const id of data.stacks[g] || []) if (!out.includes(id)) out.push(id);
    return out;
  }

  function addDays(iso, days) {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function changed() {
    errors = Object.fromEntries(Object.entries(errors).filter(([k]) => SERVER_FIELDS.has(k)));
    render();
  }

  // ------------------------------------------------------------ goals
  function renderGoals() {
    els.goals.replaceChildren(
      ...data.goals.map((g) => {
        const on = goals.includes(g.slug);
        return h("button", {
          type: "button", class: "chip", "aria-pressed": on ? "true" : "false", title: g.description,
          onclick: () => {
            if (on) goals = goals.filter((x) => x !== g.slug);
            else {
              goals.push(g.slug);
              // A new protocol takes the goal's whole stack, like picking a preset.
              if (data.is_new) for (const id of data.stacks[g.slug] || []) if (!hasPeptide(id)) items.push(newItem({ peptide_id: String(id) }));
            }
            suggestName();
            changed();
          },
        }, g.label);
      }),
      ...goals.map((g) => h("input", { type: "hidden", name: "goal", value: g }))
    );
  }

  // ------------------------------------------------------------ suggestions
  function renderSuggest() {
    const ids = suggestedIds();
    if (!ids.length) {
      els.suggest.replaceChildren(h("p", { class: "muted small", text: "Pick a goal to see its suggested stack." }));
      return;
    }
    els.suggest.replaceChildren(
      ...ids.map((id) => {
        const forGoals = goals.filter((g) => (data.stacks[g] || []).includes(id)).map((g) => goalBySlug.get(g).label);
        return h("label", { class: "suggest-item" },
          h("input", {
            type: "checkbox", checked: hasPeptide(id),
            onchange: (e) => {
              if (e.target.checked) items.push(newItem({ peptide_id: String(id) }));
              else items = items.filter((it) => it.peptide_id !== String(id));
              changed();
            },
          }),
          h("span", {}, h("strong", { text: peptideById.get(String(id))?.name ?? "?" }),
            h("span", { class: "small muted", text: forGoals.join(", ") })));
      })
    );
  }

  // ------------------------------------------------------------ add a peptide
  function addPeptide() {
    const name = els.addInput.value.trim();
    els.addMsg.hidden = true;
    if (!name) return;
    const found = peptideByName.get(name.toLowerCase());
    const dupe = found ? hasPeptide(found.id) : items.some((it) => it.new_name.toLowerCase() === name.toLowerCase());
    if (dupe) {
      els.addMsg.textContent = `${found ? found.name : name} is already in this protocol.`;
      els.addMsg.hidden = false;
      return;
    }
    items.push(found ? newItem({ peptide_id: String(found.id) }) : newItem({ new_name: name }));
    els.addInput.value = "";
    changed();
  }

  // ------------------------------------------------------------ item rows
  function field(label, control, cls = "") {
    return h("label", { class: `field ${cls}` }, h("span", { text: label }), control);
  }

  function stepRow(it, i, j, step, unitLabel) {
    const p = `items-${i}-steps-${j}`;
    const num = (name, value, placeholder) => h("input", {
      name: `${p}-${name}`, type: "number", min: name === "dose" ? "0" : "1", step: name === "dose" ? "any" : "1",
      inputmode: name === "dose" ? "decimal" : "numeric", value, placeholder, "aria-label": name.replace("_", " "),
      oninput: (e) => (step[name] = e.target.value),
    });
    return h("div", { class: "step-row" },
      h("span", { class: "small muted", text: `Step ${j + 1}` }),
      h("div", { class: "field" }, h("span", { class: "small", text: "Weeks" }),
        h("div", { class: "inline-inputs" }, num("start_week", step.start_week, "from"), h("span", { text: "–" }),
          num("end_week", step.end_week, "onward"))),
      h("div", { class: "field" }, h("span", { class: "small", text: `Dose (${unitLabel})` }), num("dose", step.dose, "")),
      h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "Remove step",
        onclick: () => { it.steps.splice(j, 1); changed(); } }, "×"));
  }

  function itemCard(it, i) {
    const p = `items-${i}`;
    const set = (name) => (v) => { it[name] = v; };
    const unitLabel = data.options.dose_unit.find(([v]) => v === it.dose_unit)?.[1] ?? it.dose_unit;

    const everyN = field("Every how many days", h("input", {
      name: `${p}-every_n_days`, type: "number", min: "2", step: "1", inputmode: "numeric", value: it.every_n_days,
      oninput: (e) => set("every_n_days")(e.target.value),
    }), "freq-extra");
    const weekdays = h("div", { class: "field freq-extra" }, h("span", { text: "Days" }),
      h("div", { class: "weekday-picks" }, data.options.weekdays.map(([d, label]) =>
        h("label", { class: "day-pick" },
          h("input", {
            type: "checkbox", name: `${p}-weekdays`, value: d, checked: it.weekdays.includes(d),
            onchange: () => {
              it.weekdays = [...form.querySelectorAll(`input[name="${p}-weekdays"]:checked`)].map((x) => x.value).join("");
            },
          }), h("span", { text: label })))));
    const syncFreq = () => {
      everyN.hidden = it.frequency !== "every_n_days";
      weekdays.hidden = it.frequency !== "weekdays";
    };

    const inventoryOptions = [["", "— Not linked —"], ...data.inventory.map((inv) => [String(inv.id),
      [inv.name, inv.vial_size_mg ? `${inv.vial_size_mg} mg` : null, inv.medium].filter(Boolean).join(" · ")])];

    const card = h("div", { class: "item-card" },
      h("div", { class: "item-card-head" },
        h("h3", {}, itemName(it), it.new_name ? h("span", { class: "tag new-tag", text: "New to library" }) : null),
        h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": `Remove ${itemName(it)}`,
          onclick: () => { items = items.filter((x) => x !== it); changed(); } }, "×")),
      it.peptide_id
        ? h("input", { type: "hidden", name: `${p}-peptide_id`, value: it.peptide_id })
        : h("input", { type: "hidden", name: `${p}-new_name`, value: it.new_name }),
      h("div", { class: "grid item-grid" },
        h("div", { class: "field" }, h("span", { text: "Dose" }),
          h("div", { class: "inline-inputs" },
            h("input", { name: `${p}-dose`, type: "number", min: "0", step: "any", inputmode: "decimal",
              value: it.dose, placeholder: "not set", "aria-label": "Dose", oninput: (e) => set("dose")(e.target.value) }),
            select(`${p}-dose_unit`, data.options.dose_unit, it.dose_unit, (v) => { it.dose_unit = v; changed(); }))),
        field("Frequency", select(`${p}-frequency`, data.options.frequency, it.frequency,
          (v) => { it.frequency = v; syncFreq(); })),
        everyN,
        weekdays,
        field("Time of day", select(`${p}-time_of_day`, data.options.time_of_day, it.time_of_day, set("time_of_day"))),
        field("Route", select(`${p}-route`, data.options.route, it.route, set("route"))),
        field("Inventory item", select(`${p}-inventory_item_id`, inventoryOptions, it.inventory_item_id,
          set("inventory_item_id"))),
        field("Notes", h("input", { name: `${p}-notes`, maxlength: "300", value: it.notes,
          oninput: (e) => set("notes")(e.target.value) }), "span-2")),
      h("div", { class: "steps" },
        h("div", { class: "steps-head" }, h("strong", { class: "small", text: "Titration steps" })),
        it.steps.map((s, j) => stepRow(it, i, j, s, unitLabel)),
        h("button", { type: "button", class: "btn btn-ghost", onclick: () => {
          const last = it.steps[it.steps.length - 1];
          const next = last && last.end_week ? String(Number(last.end_week) + 1) : last ? "" : "1";
          it.steps.push({ start_week: next, end_week: "", dose: "" });
          changed();
        } }, "+ Add step")));
    syncFreq();
    return card;
  }

  function renderItems() {
    els.items.replaceChildren(...items.map(itemCard));
    els.itemsEmpty.hidden = items.length > 0;
  }

  // ------------------------------------------------------------ errors
  function applyErrors() {
    for (const [key, msg] of Object.entries(errors)) {
      if (SERVER_FIELDS.has(key)) continue;
      const target = form.querySelector(`[name="${CSS.escape(key)}"]`) || form.querySelector(`[data-err="${CSS.escape(key)}"]`);
      if (!target) continue;
      const box = target.closest(".field") || target;
      box.classList.add("has-error");
      box.append(h("small", { class: "error", text: msg }));
    }
  }

  // ------------------------------------------------------------ protocol settings
  let lastAutoName = "";
  function suggestName() {
    if (!data.is_new) return;
    if (els.name.value === "" || els.name.value === lastAutoName) {
      lastAutoName = goals.map((g) => goalBySlug.get(g).label).join(" + ");
      els.name.value = lastAutoName;
    }
  }

  function fillEndFromWeeks() {
    const n = parseInt(els.weeks.value, 10);
    if (n >= 1 && els.start.value) els.end.value = addDays(els.start.value, n * 7 - 1);
  }
  els.weeks.addEventListener("input", fillEndFromWeeks);
  els.start.addEventListener("change", () => { if (els.weeks.value) fillEndFromWeeks(); });
  els.end.addEventListener("input", () => { els.weeks.value = ""; });

  const syncTitration = () => form.classList.toggle("titration-on", els.titration.checked);
  els.titration.addEventListener("change", syncTitration);

  els.addBtn.addEventListener("click", addPeptide);
  els.addInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addPeptide(); }
  });
  els.datalist.replaceChildren(...data.peptides.map((p) => h("option", { value: p.name })));

  document.querySelectorAll("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); })
  );

  // ------------------------------------------------------------ go
  function render() {
    renderGoals();
    renderSuggest();
    renderItems();
    applyErrors();
  }

  if (data.is_new && !items.length && !Object.keys(errors).length) {
    for (const id of suggestedIds()) items.push(newItem({ peptide_id: String(id) }));
  }
  suggestName();
  syncTitration();
  render();
})();
