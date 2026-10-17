// Equity vs benchmark and drawdown charts for a backtest run.
(async function () {
  const el = document.getElementById("equity");
  if (!el || !window.LightweightCharts) return;
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const base = {
    autoSize: true,
    layout: { background: { color: css("--surface") }, textColor: css("--muted") },
    grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border") },
  };
  const data = await (await fetch(`/api/backtests/${encodeURIComponent(el.dataset.run)}`)).json();
  const eq = LightweightCharts.createChart(el, base);
  eq.addLineSeries({ color: css("--accent"), lineWidth: 2 }).setData(data.equity);
  if (data.benchmark) eq.addLineSeries({ color: css("--muted"), lineWidth: 1.5 }).setData(data.benchmark);
  eq.timeScale().fitContent();
  const dd = LightweightCharts.createChart(document.getElementById("drawdown"), base);
  // Shade only between 0% and the drawdown line.
  dd.addBaselineSeries({
    baseValue: { type: "price", price: 0 }, lineWidth: 1,
    bottomLineColor: css("--down"), bottomFillColor1: css("--down") + "22", bottomFillColor2: css("--down") + "66",
    priceFormat: { type: "custom", formatter: v => v.toFixed(1) + "%" },
  }).setData(data.drawdown);
  dd.timeScale().fitContent();
  // Keep both charts scrolled together.
  eq.timeScale().subscribeVisibleLogicalRangeChange(r => r && dd.timeScale().setVisibleLogicalRange(r));
  dd.timeScale().subscribeVisibleLogicalRangeChange(r => r && eq.timeScale().setVisibleLogicalRange(r));
})();
