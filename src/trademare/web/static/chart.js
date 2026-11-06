// Candlestick chart with volume and moving averages (TradingView Lightweight Charts v4),
// plus an optional strategy overlay: its trades as markers, its stop/target and indicator lines.
(function () {
  const el = document.getElementById("chart");
  if (!el || !window.LightweightCharts) return;
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { color: css("--surface") }, textColor: css("--muted") },
    grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border") },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const up = css("--up"), down = css("--down");
  const candles = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
  const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const sma50 = chart.addLineSeries({ color: css("--sma50"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  const sma200 = chart.addLineSeries({ color: css("--sma200"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });

  const select = document.getElementById("strategy-select");
  const panel = document.getElementById("strategy-panel");
  const legend = document.getElementById("strategy-legend");
  let years = 1, priceLines = [], baseMarkers = [], strategyMarkers = [], overlaySeries = [];

  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = x => x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const num = x => x == null ? "—" : Number(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function applyMarkers() {
    const all = baseMarkers.concat(strategyMarkers).sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    candles.setMarkers(all);
  }

  async function load() {
    const url = `/api/bars/${el.dataset.market}/${encodeURIComponent(el.dataset.symbol)}?years=${years}`;
    const data = await (await fetch(url)).json();
    candles.setData(data.candles);
    volume.setData(data.volume.map(v => ({ time: v.time, value: v.value, color: (v.up ? up : down) + "55" })));
    sma50.setData(data.sma50);
    sma200.setData(data.sma200);
    // Your position and orders as labelled lines, your past fills as arrows.
    for (const l of priceLines) candles.removePriceLine(l);
    priceLines = (data.lines || []).map(l => candles.createPriceLine({
      price: l.price, title: l.title, lineWidth: 1, axisLabelVisible: true,
      color: l.kind === "stop" ? down : l.kind === "target" ? up : css("--accent"),
      lineStyle: l.kind === "entry" ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
    }));
    baseMarkers = (data.markers || []).map(m => ({
      time: m.time, position: m.position, shape: m.shape, text: m.text, color: m.buy ? up : down,
    }));
    await loadStrategy();
    chart.timeScale().fitContent();
  }

  function clearStrategy() {
    for (const s of overlaySeries) chart.removeSeries(s);
    overlaySeries = [];
    strategyMarkers = [];
    legend.textContent = "";
    panel.hidden = true;
    panel.innerHTML = "";
  }

  async function loadStrategy() {
    clearStrategy();
    const id = select.value;
    try { localStorage.setItem("tm-chart-strategy", id); } catch (e) { /* storage blocked */ }
    if (!id) { applyMarkers(); return; }
    legend.textContent = "loading…";
    const url = `/api/strategy/${el.dataset.market}/${encodeURIComponent(el.dataset.symbol)}?id=${encodeURIComponent(id)}&years=${years}`;
    const resp = await fetch(url);
    if (!resp.ok) { legend.textContent = "could not load this strategy"; applyMarkers(); return; }
    const s = await resp.json();
    if (select.value !== id) return; // the owner picked another one meanwhile
    for (const line of s.lines || []) {
      const series = chart.addLineSeries({
        color: line.color, lineWidth: line.dashed ? 2 : 1.5, priceLineVisible: false, lastValueVisible: false,
        lineStyle: line.dashed ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
        crosshairMarkerVisible: false,
      });
      series.setData(line.data);
      overlaySeries.push(series);
    }
    strategyMarkers = (s.markers || []).map(m => ({
      time: m.time, position: m.position, shape: m.shape, text: m.text, color: m.buy ? "#2563eb" : "#ea580c",
    }));
    applyMarkers();
    legend.innerHTML = (s.lines || []).map(l => `<i class="sw" style="background:${l.color}"></i>${esc(l.name)}`).join(" ")
      + (s.markers && s.markers.length ? ` <i class="sw" style="background:#2563eb"></i>${s.signals ? "signal" : "buy"} <i class="sw" style="background:#ea580c"></i>sell` : "");
    panel.innerHTML = renderPanel(s);
    panel.hidden = false;
  }

  function renderPanel(s) {
    let h = `<h3>${esc(s.name)}</h3><p class="sub">${esc(s.summary)} ${esc(s.exit)}</p>`;
    if (s.signals) {
      if (!s.signals.length) return h + `<p>No core-strategy signals for this stock yet.</p>`;
      h += `<div class="table-wrap"><table class="data"><thead><tr><th>Date</th><th>Signal</th><th class="num">Price</th><th class="num">Stop</th><th>Status</th></tr></thead><tbody>`;
      for (const r of s.signals) h += `<tr><td>${esc(r.date)}</td><td>${esc(r.action)}</td><td class="num">${num(r.price)}</td><td class="num">${num(r.stop)}</td><td>${esc(r.status)}</td></tr>`;
      return h + `</tbody></table></div>`;
    }
    const st = s.stats;
    h += `<div class="levels"><div><span>Trades in view</span><b>${st.trades}</b></div>`
      + `<div><span>Won</span><b>${st.closed ? `${st.wins} of ${st.closed}` : "—"}</b></div>`
      + `<div><span>Average trade</span><b class="${(st.avg_ret || 0) >= 0 ? "up" : "down"}">${pct(st.avg_ret)}</b><small>after costs</small></div>`
      + `<div><span>All trades together</span><b class="${(st.compounded || 0) >= 0 ? "up" : "down"}">${pct(st.compounded)}</b></div></div>`;
    if (s.today) h += `<p class="callout"><b>Signal today:</b> buy if it trades above ${num(s.today.entry)}, stop ${num(s.today.stop)}${s.today.target ? `, target ${num(s.today.target)}` : ""}.</p>`;
    else if (s.open) h += `<p class="callout">A trade from ${esc(s.open.entry_date)} (bought ${num(s.open.entry)}) is still open by this setup's rules.</p>`;
    if (s.record) h += `<p class="sub"><b>Track record across all stocks:</b> ${esc(s.record.verdict || "")}</p>`;
    h += `<p class="sub">One stock's history is a small sample; the track record above, across the whole universe, is the better guide. Liquidity filters are ignored here.</p>`;
    if (s.trades.length) {
      h += `<div class="table-wrap"><table class="data"><thead><tr><th>Bought</th><th class="num">At</th><th class="num">Stop</th><th>Sold</th><th class="num">At</th><th>Why</th><th class="num">Days</th><th class="num">Return</th></tr></thead><tbody>`;
      for (const t of s.trades) h += `<tr><td>${esc(t.entry_date)}</td><td class="num">${num(t.entry)}</td><td class="num">${num(t.stop)}</td><td>${esc(t.exit_date)}</td><td class="num">${num(t.exit)}</td><td>${esc(t.reason)}</td><td class="num">${t.days}</td><td class="num ${t.ret >= 0 ? "up" : "down"}">${pct(t.ret)}</td></tr>`;
      h += `</tbody></table></div>`;
    } else h += `<p>No trades by this setup in the period shown. Try 3Y or Max.</p>`;
    return h;
  }

  for (const btn of document.querySelectorAll(".chart-controls button")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chart-controls button").forEach(b => b.classList.toggle("active", b === btn));
      years = btn.dataset.years;
      load();
    });
  }
  select.addEventListener("change", loadStrategy);
  try { const saved = localStorage.getItem("tm-chart-strategy"); if (saved && select.querySelector(`option[value="${CSS.escape(saved)}"]`)) select.value = saved; } catch (e) { /* storage blocked */ }
  load();
})();
