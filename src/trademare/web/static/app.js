// Table sorting, filtering and tabs. No framework needed.
(function () {
  for (const table of document.querySelectorAll("table.sortable")) {
    const headers = [...table.tHead.rows[0].cells];
    headers.forEach((th, col) => th.addEventListener("click", () => {
      const asc = th.getAttribute("aria-sort") !== "ascending";
      headers.forEach(h => h.removeAttribute("aria-sort"));
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      const text = th.dataset.type === "text";
      const body = table.tBodies[0];
      const rows = [...body.rows];
      const key = r => r.cells[col].dataset.v ?? r.cells[col].textContent;
      rows.sort((a, b) => {
        const x = key(a), y = key(b);
        if (text) return asc ? x.localeCompare(y) : y.localeCompare(x);
        // Blank values always sink to the bottom.
        if (x === "" || y === "") return (x === "") - (y === "");
        return asc ? x - y : y - x;
      });
      body.append(...rows);
    }));
  }

  const q = document.getElementById("q"), sector = document.getElementById("sector");
  const table = document.getElementById("universe"), count = document.getElementById("count");
  if (q && table) {
    const apply = () => {
      const term = q.value.trim().toLowerCase(), s = sector.value;
      let shown = 0;
      for (const row of table.tBodies[0].rows) {
        const ok = (!term || row.dataset.search.includes(term)) && (!s || row.dataset.sector === s);
        row.hidden = !ok; shown += ok;
      }
      count.textContent = `${shown} shown`;
    };
    q.addEventListener("input", apply); sector.addEventListener("change", apply); apply();
  }

  for (const btn of document.querySelectorAll(".tabs button")) {
    btn.addEventListener("click", () => {
      btn.parentElement.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".tab-panel").forEach(p => p.hidden = p.dataset.panel !== btn.dataset.tab);
    });
  }
})();

// ⓘ explanations. One floating bubble, shown on hover or keyboard focus, toggled by tap/click.
(function () {
  const box = document.createElement("div");
  box.id = "tipbox"; box.setAttribute("role", "tooltip"); box.hidden = true;
  document.body.append(box);
  let current = null;

  function show(btn) {
    current = btn;
    box.textContent = btn.dataset.tip;
    box.hidden = false;
    btn.setAttribute("aria-describedby", "tipbox");
    btn.setAttribute("aria-expanded", "true");
    const r = btn.getBoundingClientRect(), b = box.getBoundingClientRect(), pad = 12;
    let left = Math.min(Math.max(pad, r.left + r.width / 2 - b.width / 2), innerWidth - b.width - pad);
    let top = r.bottom + 8;
    if (top + b.height > innerHeight - pad) top = r.top - b.height - 8; // no room below: flip above
    box.style.left = left + "px"; box.style.top = Math.max(pad, top) + "px";
  }
  function hide() {
    if (!current) return;
    current.removeAttribute("aria-describedby");
    current.setAttribute("aria-expanded", "false");
    current = null; box.hidden = true;
  }

  // Focus (and mouse hover) open the bubble before the click arrives, so remember the state at
  // pointerdown: a tap on an open bubble closes it, a mouse click just keeps it open.
  let wasOpen = false, pointer = "mouse";
  document.addEventListener("pointerdown", e => {
    wasOpen = current !== null && current === e.target.closest(".tip");
    pointer = e.pointerType;
  }, true);
  document.addEventListener("click", e => {
    const btn = e.target.closest(".tip");
    if (!btn) { hide(); return; }
    e.preventDefault(); e.stopPropagation(); // don't sort a table or open a <details>
    if (wasOpen && pointer !== "mouse") hide(); else show(btn);
    wasOpen = false;
  }, true);
  document.addEventListener("pointerover", e => {
    const btn = e.target.closest(".tip");
    if (btn && e.pointerType === "mouse") show(btn);
  });
  document.addEventListener("pointerout", e => {
    if (e.target.closest(".tip") && e.pointerType === "mouse") hide();
  });
  document.addEventListener("focusin", e => { if (e.target.matches(".tip")) show(e.target); });
  document.addEventListener("focusout", e => { if (e.target.matches(".tip")) hide(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") hide(); });
  addEventListener("scroll", hide, true);

  // "New here?" card on the overview: dismissed once, remembered in this browser.
  const intro = document.getElementById("newcomer");
  if (intro) {
    let seen = false;
    try { seen = localStorage.getItem("tm-newcomer-dismissed") === "1"; } catch (_) {}
    intro.hidden = seen;
    intro.querySelector(".dismiss")?.addEventListener("click", () => {
      intro.hidden = true;
      try { localStorage.setItem("tm-newcomer-dismissed", "1"); } catch (_) {}
    });
    document.getElementById("show-newcomer")?.addEventListener("click", e => { e.preventDefault(); intro.hidden = false; });
  }
})();

// Signal cards: keep the trade summary in step with the quantity, limit and stop being edited.
(function () {
  for (const card of document.querySelectorAll("article.idea[data-currency]")) {
    const form = card.querySelector(".approve-form");
    if (!form || !form.limit) continue; // exits have no limit or stop to edit
    const money = new Intl.NumberFormat(card.dataset.currency === "INR" ? "en-IN" : "en-US",
      { style: "currency", currency: card.dataset.currency });
    const set = (k, v) => { const el = card.querySelector(`[data-live="${k}"]`); if (el) el.textContent = v; };
    const update = () => {
      const q = +form.quantity.value, lim = +form.limit.value, stop = +form.stop.value;
      if (!(q > 0 && lim > 0 && stop > 0)) return;
      set("qty", q); set("limit", money.format(lim)); set("cost", money.format(q * lim));
      set("stop", money.format(stop)); set("risk", money.format(q * Math.abs(lim - stop)));
      const pct = (stop / lim - 1) * 100;
      set("stoppct", (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%");
    };
    form.addEventListener("input", update);
  }
})();

// Phone: the "More" tab opens the full navigation; tapping a link or More again closes it.
(() => {
  const more = document.querySelector(".tab-more");
  if (!more) return;
  const toggle = (open) => {
    document.body.classList.toggle("nav-open", open);
    more.setAttribute("aria-expanded", String(open));
  };
  more.addEventListener("click", () => toggle(!document.body.classList.contains("nav-open")));
  document.querySelectorAll("#sidebar a").forEach(a => a.addEventListener("click", () => toggle(false)));
  document.addEventListener("keydown", e => { if (e.key === "Escape") toggle(false); });
})();

// Wide tables: fade the right edge while there are columns off-screen.
(() => {
  const update = w => w.classList.toggle("more-right", w.scrollLeft + w.clientWidth < w.scrollWidth - 4);
  for (const w of document.querySelectorAll(".table-wrap")) {
    update(w);
    w.addEventListener("scroll", () => update(w), { passive: true });
  }
  window.addEventListener("resize", () => document.querySelectorAll(".table-wrap").forEach(update));
})();
