/* eslint-disable @typescript-eslint/no-explicit-any */
import { createChart, CrosshairMode, LineStyle, type IChartApi, type ISeriesApi, type IPriceLine } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { getJSON } from "@/lib/api";

// The classic stock chart (static/chart.js) in React: candles, volume, 50/200-day averages, your
// position's lines and fills, plus an optional strategy overlay from /api/strategy (its trades as
// markers, its stop/target and indicator lines, and a summary panel with every trade).

const RANGES: [number, string][] = [[0.5, "6M"], [1, "1Y"], [3, "3Y"], [8, "Max"]];
const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pctText = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`);
const numText = (x: number | null | undefined) => (x == null ? "—" : Number(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export function StockChart({ market, symbol, choices }: { market: string; symbol: string; choices: [string, [string, string][]][] }) {
  const el = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; candles: ISeriesApi<"Candlestick">; volume: ISeriesApi<"Histogram">; sma50: ISeriesApi<"Line">; sma200: ISeriesApi<"Line"> } | null>(null);
  const overlay = useRef<ISeriesApi<"Line">[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const baseMarkers = useRef<any[]>([]);
  const [years, setYears] = useState(1);
  const [strategy, setStrategy] = useState<string>(() => { try { return localStorage.getItem("tm-chart-strategy") ?? ""; } catch { return ""; } });
  const [panel, setPanel] = useState<any>(null);
  const [legend, setLegend] = useState<React.ReactNode>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: css("--tm-surface") }, textColor: css("--tm-muted") },
      grid: { vertLines: { color: css("--tm-border") }, horzLines: { color: css("--tm-border") } },
      rightPriceScale: { borderColor: css("--tm-border") }, timeScale: { borderColor: css("--tm-border") },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const up = css("--tm-up"), down = css("--tm-down");
    const candles = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
    const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const sma50 = chart.addLineSeries({ color: "#e0662f", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const sma200 = chart.addLineSeries({ color: "#6250d6", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    api.current = { chart, candles, volume, sma50, sma200 };
    return () => { chart.remove(); api.current = null; };
  }, []);

  const applyMarkers = (extra: any[]) => {
    const all = [...baseMarkers.current, ...extra].sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    api.current?.candles.setMarkers(all);
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      const a = api.current;
      if (!a) return;
      const up = css("--tm-up"), down = css("--tm-down"), accent = css("--tm-accent");
      const data = await getJSON<any>(`/api/bars/${market}/${encodeURIComponent(symbol)}?years=${years}`);
      if (!alive || !api.current) return;
      a.candles.setData(data.candles);
      a.volume.setData(data.volume.map((v: any) => ({ time: v.time, value: v.value, color: `${v.up ? up : down}55` })));
      a.sma50.setData(data.sma50);
      a.sma200.setData(data.sma200);
      lines.current.forEach((l) => a.candles.removePriceLine(l));
      lines.current = (data.lines ?? []).map((l: any) => a.candles.createPriceLine({
        price: l.price, title: l.title, lineWidth: 1, axisLabelVisible: true,
        color: l.kind === "stop" ? down : l.kind === "target" ? up : accent,
        lineStyle: l.kind === "entry" ? LineStyle.Solid : LineStyle.Dashed,
      }));
      baseMarkers.current = (data.markers ?? []).map((m: any) => ({ time: m.time, position: m.position, shape: m.shape, text: m.text, color: m.buy ? up : down }));

      overlay.current.forEach((s) => a.chart.removeSeries(s));
      overlay.current = [];
      setPanel(null);
      setLegend(null);
      try { localStorage.setItem("tm-chart-strategy", strategy); } catch { /* storage blocked */ }
      if (!strategy) { applyMarkers([]); a.chart.timeScale().fitContent(); return; }
      setLegend("loading…");
      let s: any;
      try { s = await getJSON<any>(`/api/strategy/${market}/${encodeURIComponent(symbol)}?id=${encodeURIComponent(strategy)}&years=${years}`); }
      catch { if (alive) { setLegend("could not load this strategy"); applyMarkers([]); } return; }
      if (!alive || !api.current) return;
      for (const line of s.lines ?? []) {
        const series = a.chart.addLineSeries({ color: line.color, lineWidth: line.dashed ? 2 : 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid });
        series.setData(line.data);
        overlay.current.push(series);
      }
      applyMarkers((s.markers ?? []).map((m: any) => ({ time: m.time, position: m.position, shape: m.shape, text: m.text, color: m.buy ? "#2563eb" : "#ea580c" })));
      setLegend(
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {(s.lines ?? []).map((l: any) => <span key={l.name} className="inline-flex items-center gap-1"><i className="inline-block h-0.5 w-4" style={{ background: l.color }} />{l.name}</span>)}
          {s.markers?.length > 0 && <><span className="inline-flex items-center gap-1"><i className="inline-block size-2 rounded-full bg-[#2563eb]" />{s.signals ? "signal" : "buy"}</span><span className="inline-flex items-center gap-1"><i className="inline-block size-2 rounded-full bg-[#ea580c]" />sell</span></>}
        </span>,
      );
      setPanel(s);
      a.chart.timeScale().fitContent();
    })();
    return () => { alive = false; };
  }, [market, symbol, years, strategy]);

  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        {RANGES.map(([y, label]) => (
          <button key={label} type="button" onClick={() => setYears(y)} aria-pressed={years === y} className={`rounded-tile px-3 py-1 text-xs ${years === y ? "bg-accent text-white dark:text-bg" : "bg-surface-2 text-muted-foreground hover:text-text"}`}>{label}</button>
        ))}
        <span className="ml-1 text-xs text-muted-foreground"><i className="mr-1 inline-block h-0.5 w-4 bg-[#e0662f] align-middle" />50-day average <i className="mr-1 ml-2 inline-block h-0.5 w-4 bg-[#6250d6] align-middle" />200-day average</span>
      </div>
      <div className="mb-2.5 flex flex-wrap items-center gap-3 text-xs">
        <label htmlFor="strategy" className="text-muted-foreground">Apply a strategy</label>
        <select id="strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)} className="max-w-full rounded-tile border border-line bg-surface px-2.5 py-1.5 text-[13px]">
          <option value="">None</option>
          {choices.map(([group, items]) => <optgroup key={group} label={group}>{items.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</optgroup>)}
        </select>
        <span className="text-muted-foreground">{legend}</span>
      </div>
      <div ref={el} className="h-[420px]" />
      {panel && <StrategyPanel s={panel} />}
    </div>
  );
}

function StrategyPanel({ s }: { s: any }) {
  return (
    <div className="mt-4 border-t border-line pt-3 text-[13px]">
      <h3 className="m-0 text-sm font-semibold">{s.name}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{s.summary} {s.exit}</p>
      {s.signals ? (
        s.signals.length ? (
          <div className="overflow-x-auto"><table className="w-full min-w-[480px]"><thead className="text-left text-xs text-muted-foreground"><tr className="[&_th]:px-2 [&_th]:py-2"><th>Date</th><th>Signal</th><th className="text-right">Price</th><th className="text-right">Stop</th><th>Status</th></tr></thead>
            <tbody>{s.signals.map((r: any, i: number) => <tr key={i} className="border-t border-line [&_td]:px-2 [&_td]:py-2"><td>{r.date}</td><td>{r.action}</td><td className="text-right tabular-nums">{numText(r.price)}</td><td className="text-right tabular-nums">{numText(r.stop)}</td><td>{r.status}</td></tr>)}</tbody></table></div>
        ) : <p>No core-strategy signals for this stock yet.</p>
      ) : (<>
        <div className="my-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[["Trades in view", s.stats.trades], ["Won", s.stats.closed ? `${s.stats.wins} of ${s.stats.closed}` : "—"], ["Average trade", pctText(s.stats.avg_ret)], ["All trades together", pctText(s.stats.compounded)]].map(([k, v], i) => (
            <div key={k} className="rounded-tile bg-surface-2/60 px-3 py-2"><div className="text-xs text-muted-foreground">{k}</div><b className={i >= 2 ? ((i === 2 ? s.stats.avg_ret : s.stats.compounded) ?? 0) >= 0 ? "text-up" : "text-down" : ""}>{v}</b>{i === 2 && <div className="text-xs text-muted-foreground">after costs</div>}</div>
          ))}
        </div>
        {s.today ? <p className="rounded-tile border-l-4 border-l-accent bg-accent-soft px-3 py-2"><b>Signal today:</b> buy if it trades above {numText(s.today.entry)}, stop {numText(s.today.stop)}{s.today.target ? `, target ${numText(s.today.target)}` : ""}.</p>
          : s.open ? <p className="rounded-tile border-l-4 border-l-accent bg-accent-soft px-3 py-2">A trade from {s.open.entry_date} (bought {numText(s.open.entry)}) is still open by this setup's rules.</p> : null}
        {s.record && <p className="text-xs text-muted-foreground"><b>Track record across all stocks:</b> {s.record.verdict}</p>}
        <p className="text-xs text-muted-foreground">One stock's history is a small sample; the track record above, across the whole universe, is the better guide. Liquidity filters are ignored here.</p>
        {s.trades.length ? (
          <div className="overflow-x-auto"><table className="w-full min-w-[640px]"><thead className="text-left text-xs text-muted-foreground"><tr className="[&_th]:px-2 [&_th]:py-2"><th>Bought</th><th className="text-right">At</th><th className="text-right">Stop</th><th>Sold</th><th className="text-right">At</th><th>Why</th><th className="text-right">Days</th><th className="text-right">Return</th></tr></thead>
            <tbody>{s.trades.map((t: any, i: number) => <tr key={i} className="border-t border-line [&_td]:px-2 [&_td]:py-2"><td>{t.entry_date}</td><td className="text-right tabular-nums">{numText(t.entry)}</td><td className="text-right tabular-nums">{numText(t.stop)}</td><td>{t.exit_date}</td><td className="text-right tabular-nums">{numText(t.exit)}</td><td>{t.reason}</td><td className="text-right tabular-nums">{t.days}</td><td className={`text-right tabular-nums ${t.ret >= 0 ? "text-up" : "text-down"}`}>{pctText(t.ret)}</td></tr>)}</tbody></table></div>
        ) : <p>No trades by this setup in the period shown. Try 3Y or Max.</p>}
      </>)}
    </div>
  );
}
