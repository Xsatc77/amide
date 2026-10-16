// Learn page: hover/tap definition cards on glossary words, and the saved vendor scorecard.
(function () {
  var glossaryNode = document.getElementById("learn-glossary");
  var sectionsNode = document.getElementById("learn-sections");
  if (!glossaryNode || !sectionsNode) return;
  var glossary = JSON.parse(glossaryNode.textContent);
  var sectionTerms = JSON.parse(sectionsNode.textContent);

  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // ---- underline the first use of each listed term, per section ----------------------------------------------------------------------
  function patternFor(term) {
    var entry = glossary[term];
    if (!entry) return null;
    var parts = entry.aliases.map(function (a) {
      var caseSensitive = a.length <= 4 && a === a.toUpperCase() && /[A-Z]/.test(a);
      return { text: a, cs: caseSensitive };
    });
    return parts.sort(function (a, b) { return b.text.length - a.text.length; });
  }

  function findIn(text, parts) {
    var best = null;
    parts.forEach(function (p) {
      var re = new RegExp("(^|[^A-Za-z0-9])(" + escapeRegExp(p.text) + ")(?![A-Za-z0-9])", p.cs ? "" : "i");
      var m = re.exec(text);
      if (m) {
        var start = m.index + m[1].length;
        if (!best || start < best.start) best = { start: start, end: start + m[2].length };
      }
    });
    return best;
  }

  function textNodes(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var el = n.parentElement;
        if (el.closest("a, button, h1, h2, h3, h4, .term, noscript, [data-learn-syringe], .learn-formula, script")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var out = [];
    while (walker.nextNode()) out.push(walker.currentNode);
    return out;
  }

  Object.keys(sectionTerms).forEach(function (slug) {
    var root = document.querySelector('[data-learn-section="' + slug + '"]');
    if (!root) return;
    sectionTerms[slug].forEach(function (term) {
      var parts = patternFor(term);
      if (!parts) return;
      var nodes = textNodes(root);
      for (var i = 0; i < nodes.length; i++) {
        var hit = findIn(nodes[i].nodeValue, parts);
        if (!hit) continue;
        var node = nodes[i];
        var after = node.splitText(hit.end);
        var middle = node.splitText(hit.start);
        var span = document.createElement("span");
        span.className = "term";
        span.tabIndex = 0;
        span.setAttribute("data-term", term);
        span.textContent = middle.nodeValue;
        middle.parentNode.replaceChild(span, middle);
        void after;
        break;
      }
    });
  });

  // ---- the card ------------------------------------------------------------------------------------------------------------------------
  var card = document.createElement("div");
  card.className = "learn-card";
  card.setAttribute("role", "tooltip");
  card.hidden = true;
  document.body.appendChild(card);
  var current = null, hideTimer = null;

  function show(span) {
    clearTimeout(hideTimer);
    var term = span.getAttribute("data-term");
    var entry = glossary[term];
    card.textContent = "";
    var title = document.createElement("strong");
    title.textContent = term;
    var def = document.createElement("p");
    def.textContent = entry.definition;
    var link = document.createElement("a");
    link.href = "#" + entry.id;
    link.textContent = "See in the glossary";
    link.addEventListener("click", hide);
    card.appendChild(title); card.appendChild(def); card.appendChild(link);
    card.hidden = false;
    current = span;
    var r = span.getBoundingClientRect();
    var w = Math.min(320, window.innerWidth - 16);
    card.style.width = w + "px";
    var left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + window.innerWidth - w - 8));
    card.style.left = left + "px";
    var below = window.scrollY + r.bottom + 6;
    card.style.top = below + "px";
    var h = card.offsetHeight;
    if (r.bottom + h + 12 > window.innerHeight && r.top > h + 12) card.style.top = (window.scrollY + r.top - h - 6) + "px";
  }
  function hide() { card.hidden = true; current = null; }
  function hideSoon() { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 180); }

  var canHover = window.matchMedia && window.matchMedia("(hover: hover)").matches;
  document.addEventListener("mouseover", function (e) {
    if (!canHover) return;
    var t = e.target.closest && e.target.closest(".term");
    if (t) show(t);
    else if (e.target.closest && e.target.closest(".learn-card")) clearTimeout(hideTimer);
  });
  document.addEventListener("mouseout", function (e) {
    if (!canHover) return;
    if (e.target.closest && (e.target.closest(".term") || e.target.closest(".learn-card"))) hideSoon();
  });
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (t) { e.preventDefault(); if (current === t && !card.hidden) hide(); else show(t); return; }
    if (!(e.target.closest && e.target.closest(".learn-card"))) hide();
  });
  document.addEventListener("focusin", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (t) show(t);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hide();
    else if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("term")) {
      e.preventDefault();
      if (current === e.target && !card.hidden) hide(); else show(e.target);
    }
  });
  window.addEventListener("scroll", function () { if (!canHover) hide(); }, { passive: true });

  // ---- the vendor scorecard, saved on this device only ---------------------------------------------------------------------------------
  var KEY = "amide-learn-scorecard";
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (err) { saved = {}; }
  document.querySelectorAll("[data-learn-check]").forEach(function (box) {
    var id = box.getAttribute("data-learn-check");
    box.checked = !!saved[id];
    box.addEventListener("change", function () {
      saved[id] = box.checked;
      try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (err) { /* private mode: it just will not be remembered */ }
    });
  });
})();
