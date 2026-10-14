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
