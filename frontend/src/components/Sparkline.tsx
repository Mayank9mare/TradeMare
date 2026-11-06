import type { Point } from "@/lib/api";

// A small line with a soft fill; the label underneath says what it is and how much it moved.
export function Sparkline({ points, label }: { points: Point[]; label: string }) {
  if (points.length < 2) {
    return <div className="rounded-tile bg-surface-2 px-4 py-6 text-center text-xs text-muted">{label}: not enough data yet</div>;
  }
  const w = 240, h = 52, pad = 4;
  const vs = points.map((p) => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const xy = vs.map((v, i) => [pad + ((w - 2 * pad) * i) / (vs.length - 1), pad + (h - 2 * pad) * (1 - (v - lo) / span)]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const change = vs[vs.length - 1] / vs[0] - 1;
  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${w} ${h}`} className="h-14 w-full" preserveAspectRatio="none" role="img" aria-label={`${label}, ${(change * 100).toFixed(1)}%`}>
        <path d={`${line} L${w - pad} ${h} L${pad} ${h} Z`} className="fill-accent-soft" />
        <path d={line} className="fill-none stroke-accent" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-muted">
        <span>{label}</span>
        <span className={change > 0 ? "text-up" : change < 0 ? "text-down" : ""}>{`${change > 0 ? "+" : ""}${(change * 100).toFixed(1)}%`.replace("-", "−")}</span>
      </figcaption>
    </figure>
  );
}
