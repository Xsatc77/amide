// Protocol builder: renders goals, suggested peptides, dose rows and titration steps from the JSON the
// server embeds, and keeps them as ordinary named form inputs so the form posts like any other.
(() => {
  const data = JSON.parse(document.getElementById("builder-data").textContent);
  const form = document.getElementById("builder-form");
  const $ = (id) => document.getElementById(id);
  const els = {
    goals: $("b-goals"), suggest: $("b-suggest"), items: $("b-items"), itemsEmpty: $("b-items-empty"),
    addInput: $("b-add-input"), addBtn: $("b-add-btn"), addMsg: $("b-add-msg"), addList: $("b-add-list"),
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
    weekdays: "", time_of_day: "any", route: "subq", inventory_item_id: "", notes: "", steps: [], cycle_offs: [],
    // Blank values from a re-shown form fall back to the defaults above.
    ...Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== "")),
  });

  let goals = [...data.state.goals];
  let items = data.state.items.map((it) => newItem({
    ...it, steps: it.steps.map((s) => ({ ...s })), cycle_offs: (it.cycle_offs || []).map((c) => ({ ...c })),
  }));
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

  // ------------------------------------------------------------ add a peptide (typeahead over the library)
  let matches = [];     // [{peptide} | {newName}] currently listed
  let activeIndex = -1;

  function searchLibrary(query) {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const scored = [];
    for (const p of data.peptides) {
      if (hasPeptide(p.id)) continue;
      const name = p.name.toLowerCase();
      const aliases = (p.aliases || "").toLowerCase();
      // Names starting with the text first, then names containing it, then alias matches.
      const rank = name.startsWith(q) ? 0 : name.includes(q) ? 1 : aliases.includes(q) ? 2 : -1;
      if (rank >= 0) scored.push([rank, p]);
    }
    scored.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name));
    const out = scored.slice(0, 8).map(([, p]) => ({ peptide: p }));
    const exact = peptideByName.has(q) || items.some((it) => it.new_name.toLowerCase() === q);
    if (!exact) out.push({ newName: query.trim() });
    return out;
  }

  function closeList() {
    els.addList.hidden = true;
    els.addInput.setAttribute("aria-expanded", "false");
    els.addInput.removeAttribute("aria-activedescendant");
    activeIndex = -1;
  }

  function renderList() {
    matches = searchLibrary(els.addInput.value);
    if (!matches.length) return closeList();
    els.addList.replaceChildren(...matches.map((m, i) => h("li", {
      id: `b-add-opt-${i}`, role: "option", class: "typeahead-item", "aria-selected": i === activeIndex ? "true" : "false",
      // mousedown (not click) so the input keeps focus and the list doesn't close first
      onmousedown: (e) => { e.preventDefault(); choose(m); },
    }, m.peptide
      ? [h("strong", { text: m.peptide.name }),
         h("span", { class: "small muted", text: [
           m.peptide.card_class,
           m.peptide.aliases,
           m.peptide.library_specifications ? `Available: ${m.peptide.library_specifications}` : null
         ].filter(Boolean).join(" · ") })]
      : [h("span", { text: `Add "${m.newName}" as a new peptide` })])));
    els.addList.hidden = false;
    els.addInput.setAttribute("aria-expanded", "true");
    if (activeIndex >= 0) els.addInput.setAttribute("aria-activedescendant", `b-add-opt-${activeIndex}`);
  }

  function choose(m) {
    els.addInput.value = m.peptide ? m.peptide.name : m.newName;
    closeList();
    addPeptide();
  }

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

  // "Ramp up": start dose, increase, weeks per step and a target fill in the step rows (the arithmetic is on the server).
  function rampHelper(it) {
    const values = { start: "", increase: "", weeks: "", target: "" };
    const input = (name, label) => h("label", { class: "small ramp-field" }, h("span", { text: label }),
      h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", "aria-label": label, oninput: (e) => (values[name] = e.target.value) }));
    const note = h("span", { class: "small muted", role: "status" });
    return h("details", { class: "ramp-helper" },
      h("summary", { class: "small", text: "Fill the steps from a ramp" }),
      h("div", { class: "inline-inputs" }, input("start", "Start"), input("increase", "Increase"), input("weeks", "Weeks per step"), input("target", "Target")),
      h("button", { type: "button", class: "btn btn-ghost", onclick: async () => {
        note.textContent = "";
        const response = await fetch(`/protocols/titration-steps?${new URLSearchParams(values)}`);
        const body = await response.json();
        if (!response.ok) { note.textContent = body.detail || "Check the numbers."; return; }
        it.steps = body.steps.map((s) => ({ start_week: String(s.start_week), end_week: s.end_week === null ? "" : String(s.end_week), dose: String(s.dose) }));
        changed();
      } }, "Fill steps"), note);
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

  function cycleOffRow(it, i, j, off) {
    const p = `items-${i}-cycle_offs-${j}`;
    const num = (name, value, placeholder) => h("input", {
      name: `${p}-${name}`, type: "number", min: "1", step: "1", inputmode: "numeric", value, placeholder,
      "aria-label": name.replace("_", " "), oninput: (e) => (off[name] = e.target.value),
    });
    return h("div", { class: "cycle-off-row" },
      h("span", { class: "small muted", text: `Off ${j + 1}` }),
      h("div", { class: "field" }, h("span", { class: "small", text: "Starts week" }), num("start_week", off.start_week, "")),
      h("div", { class: "field" }, h("span", { class: "small", text: "Weeks off" }), num("weeks", off.weeks, "")),
      h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "Remove cycle-off",
        onclick: () => { it.cycle_offs.splice(j, 1); changed(); } }, "×"));
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
        h("h3", {}, itemName(it), it.new_name ? h("span", { class: "tag new-tag", text: "New to library" }) : null,
          it.peptide_id ? h("a", { class: "small view-card", href: `/library/${it.peptide_id}`, target: "_blank",
            rel: "noopener", text: "View card" }) : null),
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
          (v) => { it.frequency = v; changed(); })),
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
        } }, "+ Add step"),
        rampHelper(it)),
      it.frequency === "as_needed" ? null : h("div", { class: "cycle-offs" },
        h("div", { class: "steps-head" }, h("strong", { class: "small", text: "Cycle on/off" })),
        it.cycle_offs.map((c, j) => cycleOffRow(it, i, j, c)),
        h("button", { type: "button", class: "btn btn-ghost", onclick: () => {
          // Resuming dosing after an off period needs no action here at all -- is_due() already
          // picks it back up on schedule the week the off period ends (see app/calendar/schedule.py).
          // Ramping the dose back in is just "+ Add step" above, independently -- a cycle-off is
          // allowed to overlap a step's range, so there's no ordering requirement between them.
          // This button always does one thing: start another off period.
          const lastOff = it.cycle_offs[it.cycle_offs.length - 1];
          const lastStep = it.steps[it.steps.length - 1];
          const afterOff = lastOff ? Number(lastOff.start_week) + Number(lastOff.weeks || 0) : 0;
          const afterStep = lastStep && lastStep.end_week ? Number(lastStep.end_week) + 1 : 0;
          const next = Math.max(afterOff, afterStep, 1);
          it.cycle_offs.push({ start_week: String(next), weeks: "" });
          changed();
        } }, "+ Cycle off")));
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

  els.addBtn.addEventListener("click", () => { closeList(); addPeptide(); });
  els.addInput.addEventListener("input", () => { activeIndex = -1; els.addMsg.hidden = true; renderList(); });
  els.addInput.addEventListener("focus", renderList);
  els.addInput.addEventListener("blur", closeList);
  els.addInput.addEventListener("keydown", (e) => {
    const open = !els.addList.hidden;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) renderList();
      if (!matches.length) return;
      activeIndex = (activeIndex + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length;
      renderList();
      $(`b-add-opt-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();  // never submit the whole form from here
      if (open && activeIndex >= 0) choose(matches[activeIndex]);
      else if (open && matches[0]?.peptide && matches[0].peptide.name.toLowerCase().startsWith(els.addInput.value.trim().toLowerCase())) choose(matches[0]);
      else { closeList(); addPeptide(); }
    } else if (e.key === "Escape") {
      closeList();
    }
  });

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
