// Protocols page: the "Shop this protocol" dialog. The plan comes from the server (cheapest cover from the vendors' current price
// lists); editing a shipping fee asks for a fresh plan, and "Save as my defaults" stores the two fees in Settings.
(() => {
  const dialog = document.getElementById("shop-dialog");
  if (!dialog) return;
  const body = document.getElementById("shop-body");
  const china = document.getElementById("shop-china");
  const us = document.getElementById("shop-us");
  const saveBtn = document.getElementById("shop-save-defaults");
  const saveNote = document.getElementById("shop-save-note");
  const title = document.getElementById("shop-title");
  const download = document.getElementById("shop-download");
  const emailBtn = document.getElementById("shop-email");
  const shareNote = document.getElementById("shop-share-note");
  const MAILTO_LIMIT = 1800;
  let protocolId = null;
  let timer = null;
  let sizes = {};                       // peptide id -> {ml, per_box}: vial sizes typed for oils the price lists sell by strength only

  const money = (n) => `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const warehouse = (w) => (w === "us" ? "US warehouse" : "China warehouse");
  const shortDate = (iso) => { const [y, m, d] = iso.split("-"); return `${m}/${d}/${y}`; };

  function planBlock(plan, heading, headingClass) {
    const box = el("section", "shop-plan");
    box.append(el("h3", headingClass || "shop-plan-title", heading));
    for (const s of plan.sources) {
      const card = el("div", "shop-source");
      const head = el("div", "shop-source-head");
      head.append(el("strong", "", s.vendor), el("span", "muted small", ` ${warehouse(s.warehouse)} · list dated ${shortDate(s.list_date)}`));
      card.append(head);
      const table = el("table", "shop-table");
      const thead = el("thead");
      const hr = el("tr");
      for (const h of ["Item", "Buy", "Per vial", "Cost", "Left over"]) hr.append(el("th", "", h));
      thead.append(hr);
      table.append(thead);
      const tbody = el("tbody");
      for (const l of s.lines) {
        const tr = el("tr");
        const pack = l.pack_type || (l.pack_size === 1 ? "single" : `pack of ${l.pack_size}`);
        tr.append(el("td", "", `${l.peptide} ${l.size_label}`),
                  el("td", "", `${l.packs} × ${pack} (${l.vials_needed} vial${l.vials_needed === 1 ? "" : "s"} needed)`),
                  el("td", "", money(l.per_vial)), el("td", "", money(l.cost)),
                  el("td", "", l.leftover_vials ? `${l.leftover_vials} vial${l.leftover_vials === 1 ? "" : "s"}` : "none"));
        tbody.append(tr);
      }
      table.append(tbody);
      card.append(table);
      card.append(el("p", "shop-ship small", `Items ${money(s.items_total)} + shipping ${money(s.shipping)} = ${money(s.total)}`));
      box.append(card);
    }
    box.append(el("p", "shop-total", `Total ${money(plan.total)}`));
    if (plan.missing.length) box.append(el("p", "calc-note", `Not covered by these vendors: ${plan.missing.join(", ")}`));
    return box;
  }

  // Oils sold by strength (250 mg/mL) with no vial size on the sheet: ask, every time, then price them.
  function sizeQuestions(items) {
    const box = el("section", "shop-plan shop-sizes");
    box.append(el("h3", "shop-plan-title", "Vial size needed"));
    box.append(el("p", "small muted", "These are oil suspensions, so no BAC water is added. The price list gives the strength but not how much is in a vial."));
    for (const a of items) {
      const row = el("div", "shop-size-row");
      row.append(el("p", "", `${a.name}: ${a.strength}, sold as ${a.pack_size} ${a.pack_type} for ${money(a.pack_price)}.`));
      const ml = el("input"); ml.type = "number"; ml.min = "0.1"; ml.step = "any"; ml.setAttribute("aria-label", `${a.name}: mL per vial`); ml.placeholder = "mL per vial";
      const per = el("input"); per.type = "number"; per.min = "1"; per.step = "1"; per.setAttribute("aria-label", `${a.name}: vials per ${a.pack_type}`); per.placeholder = `vials per ${a.pack_type}`;
      const go = el("button", "btn btn-primary", "Price it"); go.type = "button";
      go.addEventListener("click", () => {
        if (!(Number(ml.value) > 0) || !(Number(per.value) >= 1)) { ml.focus(); return; }
        sizes[a.peptide_id] = { ml: ml.value, per_box: per.value };
        load(true);
      });
      row.append(ml, per, go);
      box.append(row);
    }
    return box;
  }

  function render(data) {
    body.replaceChildren();
    if (data.status === "no_end_date") {
      body.append(el("p", "calc-note", "This protocol has no end date, so there are no course totals to shop from. Give it an end date first."));
      return;
    }
    if (data.status === "nothing_to_buy" || !data.plan) {
      if (!(data.needs_volume && data.needs_volume.length)) {
        body.append(el("p", "calc-note", data.unshoppable.length ? "Nothing on this protocol could be found on the current price lists." : "There is nothing to buy for this protocol."));
      }
    } else {
      body.append(el("p", "shop-reason", data.plan.reason + "."));
      body.append(planBlock(data.plan, "Best plan"));
      if (data.alternatives.length) {
        const alt = el("details", "shop-alts");
        alt.append(el("summary", "", "Other plans to compare"));
        for (const p of data.alternatives) {
          const diff = p.vs_chosen === 0 ? "same total" : p.vs_chosen < 0 ? `${money(-p.vs_chosen)} cheaper` : `${money(p.vs_chosen)} more`;
          alt.append(planBlock(p, `${p.sources.length === 1 ? "One order" : "Two orders"}: ${money(p.total)} (${diff})`, "shop-alt-title"));
        }
        body.append(alt);
      }
    }
    if (data.needs_volume && data.needs_volume.length) body.append(sizeQuestions(data.needs_volume));
    if (data.unshoppable.length) {
      const box = el("div", "shop-unavailable");
      box.append(el("strong", "", "Not available on the current price lists"));
      const ul = el("ul");
      for (const u of data.unshoppable) ul.append(el("li", "", `${u.name}: ${u.reason}`));
      box.append(ul);
      body.append(box);
    }
    if (data.bac && data.bac.buy) {
      const b = data.bac.buy;
      const box = el("section", "shop-plan");
      box.append(el("h3", "shop-plan-title", `BAC water (about ${data.bac.ml} mL for the course)`));
      box.append(el("p", "", `${b.vendor} ${warehouse(b.warehouse)}: ${b.product} ${b.size_label}, ${b.packs} × ${b.pack_label} (${b.units} bottle${b.units === 1 ? "" : "s"}).`));
      box.append(el("p", "shop-ship small", b.shipping ? `Cost ${money(b.cost)} + shipping ${money(b.shipping)} (its own order) = ${money(b.extra)}`
                                                      : `Cost ${money(b.cost)}, added to the order from this vendor`));
      body.append(box);
    } else if (data.bac) {
      body.append(el("p", "shop-bac small", `No BAC water brand you rank is on the current price lists. This course uses about ${data.bac.ml} mL: ${data.bac.bottles} bottle${data.bac.bottles === 1 ? "" : "s"} of 30 mL. Buy it separately.`));
    }
    if (data.plan && data.grand_total !== null && data.bac && data.bac.buy) body.append(el("p", "shop-total", `Grand total with BAC water ${money(data.grand_total)}`));
    body.append(el("p", "muted small", "Prices come from each vendor's newest price list, with a 5% buffer on the total dose. Confirm with the vendor before ordering."));
  }

  // The fees as typed in the dialog (the share text must match the plan on screen).
  function feeParams() {
    const params = new URLSearchParams();
    if (china.value !== "") params.set("china", china.value);
    if (us.value !== "") params.set("us", us.value);
    for (const [id, s] of Object.entries(sizes)) { params.set(`vial_ml_${id}`, s.ml); params.set(`box_vials_${id}`, s.per_box); }
    return params;
  }

  function setDownloadLink() {
    const params = feeParams();
    params.set("download", "1");
    download.href = `/protocols/${protocolId}/shop.txt?${params}`;
  }

  async function load(useFields) {
    const params = useFields ? feeParams() : new URLSearchParams();
    body.replaceChildren(el("p", "muted", "Looking for the cheapest way to buy this course…"));
    try {
      const response = await fetch(`/protocols/${protocolId}/shop?${params}`);
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      if (!useFields && data.shipping) { china.value = data.shipping.china; us.value = data.shipping.us; }
      setDownloadLink();
      render(data);
    } catch (err) {
      body.replaceChildren(el("p", "calc-issue", response_message(err)));
    }
  }

  const response_message = () => "The shopping plan could not be loaded. Check the shipping fees are numbers and try again.";

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".shop-btn");
    if (!btn) return;
    protocolId = btn.dataset.protocolId;
    sizes = {};
    title.textContent = `Shop this protocol: ${btn.getAttribute("aria-label").replace(/^Shop this protocol: /, "")}`;
    saveNote.textContent = "";
    shareNote.textContent = "";
    dialog.showModal();
    load(false);
  });
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("mousedown", (e) => { dialog._backdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (dialog._backdrop && e.target === dialog) dialog.close(); });

  [china, us].forEach((input) => input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(true), 250);
  }));

  emailBtn.addEventListener("click", async () => {
    shareNote.textContent = "";
    try {
      const response = await fetch(`/protocols/${protocolId}/shop.txt?${feeParams()}`);
      if (!response.ok) throw new Error(String(response.status));
      const text = await response.text();
      const subject = text.split("\n")[0];
      let body = text;
      if (encodeURIComponent(body).length > MAILTO_LIMIT) {            // some email programs cut long links: send the file instead
        body = "The shopping plan is too long for an email draft, so it was saved as a text file. Attach it to this message.";
        download.click();
        shareNote.textContent = "Too long for a draft: the text file was downloaded. Attach it to the email.";
      }
      window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    } catch (err) {
      shareNote.textContent = "The plan could not be loaded to share. Check the shipping fees and try again.";
    }
  });

  saveBtn.addEventListener("click", async () => {
    const response = await fetch("/protocols/shop/defaults", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ china: china.value, us: us.value }),
    });
    saveNote.textContent = response.ok ? "Saved. These are now your defaults." : "Enter both fees as numbers from 0 to 10,000.";
  });
})();
