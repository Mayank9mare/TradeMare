// Candlestick chart with volume and moving averages (TradingView Lightweight Charts v4).
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

  async function load(years) {
    const url = `/api/bars/${el.dataset.market}/${encodeURIComponent(el.dataset.symbol)}?years=${years}`;
    const data = await (await fetch(url)).json();
    candles.setData(data.candles);
    volume.setData(data.volume.map(v => ({ time: v.time, value: v.value, color: (v.up ? up : down) + "55" })));
    sma50.setData(data.sma50);
    sma200.setData(data.sma200);
    chart.timeScale().fitContent();
  }

  for (const btn of document.querySelectorAll(".chart-controls button")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chart-controls button").forEach(b => b.classList.toggle("active", b === btn));
      load(btn.dataset.years);
    });
  }
  load(1);
})();
