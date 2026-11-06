import { Area, AreaChart, Tooltip, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltipContent } from "@/components/wensity/chart";
import type { Point } from "@/lib/api";
import { shortDay } from "@/lib/format";

// A small area chart (Wensity chart on Recharts) with a date/value tooltip, no axes or grid:
// a trend at a glance. The caption says what it is and how much it moved.
export function MiniArea({ points, label, format }: { points: Point[]; label: string; format: (v: number) => string }) {
  if (points.length < 2) {
    return <div className="rounded-tile bg-surface-2 px-4 py-6 text-center text-xs text-muted-foreground">{label}: not enough data yet</div>;
  }
  const change = points[points.length - 1].v / points[0].v - 1;
  const changeText = `${change > 0 ? "+" : ""}${(change * 100).toFixed(1)}%`.replace("-", "−");
  return (
    <figure className="m-0">
      <ChartContainer config={{ v: { label } }} height={64} aria-label={`${label}, ${changeText}`}>
        <AreaChart data={points} margin={{ top: 4, right: 2, left: 2, bottom: 0 }}>
          <XAxis dataKey="t" hide />
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip
            cursor={{ stroke: "var(--tm-border-strong)", strokeWidth: 1 }}
            content={<ChartTooltipContent labelFormatter={(t) => shortDay(String(t))} valueFormatter={(v) => format(Number(v))} />}
          />
          <Area type="monotone" dataKey="v" stroke="var(--color-v)" strokeWidth={1.75} fill="var(--color-v)" fillOpacity={0.12} isAnimationActive={false} />
        </AreaChart>
      </ChartContainer>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className={change > 0 ? "text-up" : change < 0 ? "text-down" : ""}>{changeText}</span>
      </figcaption>
    </figure>
  );
}
