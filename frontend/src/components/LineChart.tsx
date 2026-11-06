import { createChart, type IChartApi, type LineData } from "lightweight-charts";
import { useEffect, useRef } from "react";

// A TradingView Lightweight Charts line (the classic portfolio equity chart), coloured from the
// current theme's tokens. Keeps at least ±2% of the value on the axis so a flat account looks flat.
export function LineChart({ data, height = 260, minPoints = 5, emptyText }: { data: LineData[]; height?: number; minPoints?: number; emptyText: string }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || data.length < minPoints) return;
    const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: css("--tm-surface") }, textColor: css("--tm-muted") },
      grid: { vertLines: { color: css("--tm-border") }, horzLines: { color: css("--tm-border") } },
    });
    chart.addLineSeries({
      color: css("--tm-accent"), lineWidth: 2,
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = original();
        if (!r) return r;
        const lo = r.priceRange.minValue, hi = r.priceRange.maxValue, mid = (lo + hi) / 2;
        const half = Math.max((hi - lo) / 2, Math.abs(mid) * 0.02);
        return { priceRange: { minValue: mid - half, maxValue: mid + half } };
      },
    }).setData(data);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, minPoints]);
  if (data.length < minPoints) return <div className="rounded-tile bg-surface-2 px-4 py-3.5 text-center text-[13px] text-muted-foreground">{emptyText}</div>;
  return <div ref={el} style={{ height }} />;
}
