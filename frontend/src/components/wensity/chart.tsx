"use client";

import * as React from "react";
import { useReducedMotion } from "framer-motion";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";

/* ─── Chart Config & Theming ────────────────────────────────── */

export type ChartConfig = Record<
  string,
  {
    label: React.ReactNode;
    color?: string;
  }
>;

/**
 * Six-slot categorical order (E-06), validated for CVD-safe adjacent contrast.
 * Light/dark values live on `--primitive-chart-N`; slot order is the safety
 * mechanism — always assign in this fixed order, never reshuffle per-chart.
 */
const CHART_PALETTE = [
  "var(--primitive-chart-1)",
  "var(--primitive-chart-2)",
  "var(--primitive-chart-3)",
  "var(--primitive-chart-4)",
  "var(--primitive-chart-5)",
  "var(--primitive-chart-6)",
] as const;

const ChartContext = React.createContext<{ config: ChartConfig } | null>(null);

function useChart() {
  const ctx = React.useContext(ChartContext);
  if (!ctx) throw new Error("Chart subcomponents must be used inside <ChartContainer>.");
  return ctx;
}

/**
 * Resolves the config/color key for a tooltip or legend entry. For
 * multi-series charts (Line/Bar/Area/Radar) Recharts sets `dataKey` to the
 * series key. For Pie, every slice shares the same `dataKey` (the chart's
 * `dataKey="value"`), so the per-slice identity instead lives in `name` —
 * prefer whichever candidate is an actual config key.
 */
function resolveChartKey(config: ChartConfig, ...candidates: Array<string | number | undefined>) {
  for (const candidate of candidates) {
    if (candidate !== undefined && config[String(candidate)]) return String(candidate);
  }
  return String(candidates.find((c) => c !== undefined) ?? "");
}

export interface ChartContainerProps extends React.HTMLAttributes<HTMLDivElement> {
  config: ChartConfig;
  height?: number;
  children: React.ReactElement;
}

/**
 * Wraps any Recharts chart with the CVD-validated palette (injected as
 * `--color-<key>` CSS variables pointing at `--primitive-chart-N`) and a
 * responsive frame. Theme switching is handled by the CSS token maps.
 */
export const ChartContainer = React.forwardRef<HTMLDivElement, ChartContainerProps>(
  ({ config, height = 280, className, children, id, ...props }, ref) => {
    const generatedId = React.useId().replace(/:/g, "");
    const chartId = id ?? `chart-${generatedId}`;
    const entries = Object.entries(config);

    const colorVars = entries
      .map(([key, value], index) => {
        const color = value.color ?? CHART_PALETTE[index % CHART_PALETTE.length]!;
        return `--color-${key}: ${color};`;
      })
      .join(" ");

    return (
      <ChartContext.Provider value={{ config }}>
        <div data-wensity-primitive=""
          ref={ref}
          data-chart={chartId}
          className={cn("w-full [&_.recharts-surface]:overflow-visible", className)}
          style={{ height }}
          {...props}
        >
          <style
            // Scoped CSS variables; the fixed palette order is the CVD-safety
            // mechanism, so keys always map to the same slot.
            dangerouslySetInnerHTML={{
              __html: `[data-chart=${chartId}]{${colorVars}}`,
            }}
          />
          <ResponsiveContainer width="100%" height="100%">
            {children}
          </ResponsiveContainer>
        </div>
      </ChartContext.Provider>
    );
  }
);
ChartContainer.displayName = "ChartContainer";

/* ─── Tooltip ────────────────────────────────────────────────── */

export const ChartTooltip = Tooltip;

export interface ChartTooltipContentProps {
  active?: boolean;
  label?: string | number;
  payload?: Array<{ dataKey?: string | number; name?: string; value?: string | number; color?: string }>;
  labelFormatter?: (label: string | number) => React.ReactNode;
  valueFormatter?: (value: string | number, key: string) => React.ReactNode;
  /** Force every row's label to this config key's label (e.g. a shared "views" unit) instead of resolving per-series. */
  nameKey?: string;
  className?: string;
}

export function ChartTooltipContent({
  active,
  label,
  payload,
  labelFormatter,
  valueFormatter,
  nameKey,
  className,
}: ChartTooltipContentProps) {
  const { config } = useChart();

  if (!active || !payload?.length) return null;

  return (
    <div
      data-surface="overlay"
      className={cn(
        "min-w-32 rounded-[var(--primitive-radius-popup)] px-2.5 py-2 text-xs text-[var(--foreground)]",
        className
      )}
    >
      {label !== undefined && (
        <div className="mb-1 font-medium text-[var(--foreground)]">
          {labelFormatter ? labelFormatter(label) : label}
        </div>
      )}
      <div className="flex flex-col gap-1">
        {payload.map((item, index) => {
          const seriesKey = resolveChartKey(config, item.name, item.dataKey);
          const labelEntry = config[nameKey ?? seriesKey];
          return (
            <div key={`${seriesKey}-${index}`} className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
                <span
                  className="size-2 shrink-0 rounded-[2px]"
                  style={{ backgroundColor: item.color ?? `var(--color-${seriesKey})` }}
                />
                <span>{labelEntry?.label ?? item.name ?? seriesKey}</span>
              </div>
              <span className="font-medium tabular-nums text-[var(--foreground)]">
                {item.value !== undefined
                  ? valueFormatter?.(item.value, seriesKey) ?? item.value
                  : null}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Legend ─────────────────────────────────────────────────── */

export const ChartLegend = Legend;

export interface ChartLegendContentProps {
  payload?: Array<{ dataKey?: string | number; value?: string; color?: string }>;
  className?: string;
}

export function ChartLegendContent({ payload, className }: ChartLegendContentProps) {
  const { config } = useChart();

  if (!payload?.length) return null;

  return (
    <div className={cn("flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 pt-3", className)}>
      {payload.map((item, index) => {
        const key = resolveChartKey(config, item.value, item.dataKey);
        const entry = config[key];
        return (
          <div key={`${key}-${index}`} className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <span
              className="size-2 shrink-0 rounded-[2px]"
              style={{ backgroundColor: item.color ?? `var(--color-${key})` }}
            />
            <span>{entry?.label ?? item.value ?? key}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Shared axis chrome ─────────────────────────────────────── */

const axisTick = {
  fill: "var(--muted-foreground)",
  fontSize: "var(--primitive-text-hint)",
  fontVariantNumeric: "tabular-nums" as const,
};
const gridStroke = "var(--border)";

function useChartMotion() {
  const reduced = useReducedMotion();
  return {
    isAnimationActive: !reduced,
    animationDuration: reduced ? 0 : 700,
    animationEasing: "ease-out" as const,
  };
}

/* ─── Ready-made chart variants ─────────────────────────────── */

export interface ChartSeriesDatum {
  [key: string]: string | number;
}

export interface ChartVariantProps {
  data: ChartSeriesDatum[];
  config: ChartConfig;
  xKey: string;
  className?: string;
  height?: number;
  showLegend?: boolean;
  showGrid?: boolean;
  stacked?: boolean;
}

/** Line — displays trends and changes over time. */
export function ChartLine({
  data,
  config,
  xKey,
  className,
  height,
  showLegend = true,
  showGrid = true,
}: ChartVariantProps) {
  const motionProps = useChartMotion();
  const seriesKeys = Object.keys(config);

  return (
    <ChartContainer config={config} className={className} height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {showGrid && <CartesianGrid vertical={false} stroke={gridStroke} strokeDasharray="0" />}
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} />
        <YAxis tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} width={32} />
        <Tooltip cursor={{ stroke: gridStroke, strokeWidth: 1 }} content={<ChartTooltipContent />} />
        {showLegend && seriesKeys.length > 1 && <Legend content={<ChartLegendContent />} />}
        {seriesKeys.map((key) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            dot={{ r: 4, fill: `var(--color-${key})`, stroke: "var(--background)", strokeWidth: 2 }}
            activeDot={{ r: 5, stroke: "var(--background)", strokeWidth: 2 }}
            {...motionProps}
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}

/** Bar — compares values across different categories. */
export function ChartBar({
  data,
  config,
  xKey,
  className,
  height,
  showLegend = true,
  showGrid = true,
  stacked = false,
}: ChartVariantProps) {
  const motionProps = useChartMotion();
  const seriesKeys = Object.keys(config);

  return (
    <ChartContainer config={config} className={className} height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={4}>
        {showGrid && <CartesianGrid vertical={false} stroke={gridStroke} strokeDasharray="0" />}
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} />
        <YAxis tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} width={32} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<ChartTooltipContent />} />
        {showLegend && seriesKeys.length > 1 && <Legend content={<ChartLegendContent />} />}
        {seriesKeys.map((key, index) => (
          <Bar
            key={key}
            dataKey={key}
            fill={`var(--color-${key})`}
            stackId={stacked ? "stack" : undefined}
            maxBarSize={24}
            radius={
              stacked
                ? index === seriesKeys.length - 1
                  ? [4, 4, 0, 0]
                  : [0, 0, 0, 0]
                : [4, 4, 0, 0]
            }
            {...motionProps}
          />
        ))}
      </BarChart>
    </ChartContainer>
  );
}

/** Area — shows trends with emphasized data volume. */
export function ChartArea({
  data,
  config,
  xKey,
  className,
  height,
  showLegend = true,
  showGrid = true,
  stacked = false,
}: ChartVariantProps) {
  const motionProps = useChartMotion();
  const seriesKeys = Object.keys(config);

  return (
    <ChartContainer config={config} className={className} height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {showGrid && <CartesianGrid vertical={false} stroke={gridStroke} strokeDasharray="0" />}
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} />
        <YAxis tickLine={false} axisLine={false} tick={axisTick} tickMargin={8} width={32} />
        <Tooltip cursor={{ stroke: gridStroke, strokeWidth: 1 }} content={<ChartTooltipContent />} />
        {showLegend && seriesKeys.length > 1 && <Legend content={<ChartLegendContent />} />}
        {seriesKeys.map((key) => (
          <Area
            key={key}
            type="monotone"
            dataKey={key}
            stackId={stacked ? "stack" : undefined}
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            fill={`var(--color-${key})`}
            fillOpacity={0.1}
            {...motionProps}
          />
        ))}
      </AreaChart>
    </ChartContainer>
  );
}

/** Pie — displays proportional parts of a whole. */
export interface ChartPieDatum {
  name: string;
  value: number;
}

export interface ChartPieProps {
  data: ChartPieDatum[];
  config: ChartConfig;
  className?: string;
  height?: number;
  /** Hole size — number (px) or percentage string (e.g. `"68%"`). Prefer `%` for responsive donuts. */
  innerRadius?: number | string;
  outerRadius?: number | string;
  showLegend?: boolean;
  /**
   * Content centered on the donut hole. Positioned against the chart frame so it
   * stays aligned with the ring (do not overlay from outside ChartPie).
   */
  centerLabel?: React.ReactNode;
  /**
   * Gap between slices in degrees. Defaults to `0` for a continuous ring.
   * Pass a small value (e.g. `2`) for separated wedges.
   */
  paddingAngle?: number;
  /**
   * Slice outline width. Defaults to `0` so adjacent fills meet cleanly.
   * Pair with `stroke` when you want separated segments.
   */
  strokeWidth?: number;
  /** Slice outline color. Only visible when `strokeWidth` > 0. */
  stroke?: string;
}

export function ChartPie({
  data,
  config,
  className,
  height = 280,
  innerRadius = 0,
  outerRadius,
  showLegend = true,
  centerLabel,
  paddingAngle = 0,
  strokeWidth = 0,
  stroke = "var(--background)",
}: ChartPieProps) {
  const motionProps = useChartMotion();
  // Symmetric margins keep the ring’s visual center on the frame mid-point so
  // `centerLabel` (and external overlays on the same box) stay aligned.
  const margin = showLegend
    ? { top: 8, right: 8, left: 8, bottom: 8 }
    : { top: 0, right: 0, left: 0, bottom: 0 };
  const resolvedOuterRadius = outerRadius ?? (showLegend ? "75%" : "90%");

  return (
    <div
      className={cn("relative w-full", className)}
      style={{ height }}
      data-slot="chart-pie"
    >
      <ChartContainer config={config} className="h-full" height={height}>
        <PieChart margin={margin}>
          <Tooltip content={<ChartTooltipContent />} />
          {showLegend ? <Legend content={<ChartLegendContent />} /> : null}
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={innerRadius}
            outerRadius={resolvedOuterRadius}
            paddingAngle={paddingAngle}
            stroke={stroke}
            strokeWidth={strokeWidth}
            {...motionProps}
          >
            {data.map((entry) => (
              <Cell key={entry.name} fill={`var(--color-${entry.name})`} />
            ))}
          </Pie>
        </PieChart>
      </ChartContainer>
      {centerLabel ? (
        <div
          className={cn(
            "pointer-events-none absolute inset-0 z-[1] flex flex-col items-center justify-center text-center",
            // Legend sits under the plot; nudge the hole label up to the ring center.
            showLegend && "-translate-y-3",
          )}
        >
          {centerLabel}
        </div>
      ) : null}
    </div>
  );
}

/** Radar — compares multiple metrics across categories. */
export function ChartRadar({
  data,
  config,
  xKey,
  className,
  height,
  showLegend = true,
}: Omit<ChartVariantProps, "showGrid" | "stacked">) {
  const motionProps = useChartMotion();
  const seriesKeys = Object.keys(config);

  return (
    <ChartContainer config={config} className={className} height={height}>
      <RadarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <PolarGrid stroke={gridStroke} />
        <PolarAngleAxis dataKey={xKey} tick={axisTick} />
        <Tooltip content={<ChartTooltipContent />} />
        {showLegend && seriesKeys.length > 1 && <Legend content={<ChartLegendContent />} />}
        {seriesKeys.map((key) => (
          <Radar
            key={key}
            dataKey={key}
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            fill={`var(--color-${key})`}
            fillOpacity={0.1}
            {...motionProps}
          />
        ))}
      </RadarChart>
    </ChartContainer>
  );
}

/* ─── Interactive bar chart (toggleable series + running totals) ────── */

export interface ChartBarInteractiveProps {
  data: ChartSeriesDatum[];
  config: ChartConfig;
  xKey: string;
  /** Toggleable series keys, in display order. Defaults to every key in `config`. */
  series?: string[];
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Overrides the tooltip row label for every series (e.g. a shared unit like "Page Views"). */
  unitLabel?: React.ReactNode;
  className?: string;
  height?: number;
  /** Format the xKey axis/tooltip label as a date (expects ISO date strings). */
  dateFormat?: boolean;
  /**
   * Force exactly this many data points between shown x-axis ticks (e.g. 4 for
   * daily data = a tick every 4 days). Omit to let ticks auto-space to fit —
   * the safe default for larger datasets, since a fixed gap can overlap when
   * there are many points.
   */
  tickGapDays?: number;
}

const shortDateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const longDateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function ChartBarInteractive({
  data,
  config,
  xKey,
  series,
  title,
  description,
  unitLabel,
  className,
  height = 250,
  dateFormat = false,
  tickGapDays,
}: ChartBarInteractiveProps) {
  const motionProps = useChartMotion();
  const seriesKeys = series ?? Object.keys(config);
  const [active, setActive] = React.useState(seriesKeys[0]);

  const totals = React.useMemo(() => {
    const sums: Record<string, number> = {};
    for (const key of seriesKeys) {
      sums[key] = data.reduce((sum, row) => sum + (Number(row[key]) || 0), 0);
    }
    return sums;
  }, [data, seriesKeys]);

  const chartConfig = React.useMemo(
    () => (unitLabel ? { ...config, __unit: { label: unitLabel } } : config),
    [config, unitLabel]
  );

  return (
    <div
      data-wensity-primitive=""
      data-surface="panel"
      className={cn(
        "overflow-hidden rounded-[var(--primitive-radius-surface)] text-[var(--foreground)]",
        className
      )}
    >
      <div className="flex flex-col items-stretch border-b border-[var(--border)] sm:flex-row">
        <div className="flex flex-1 flex-col justify-center gap-1 px-6 py-4">
          <span className="font-[family-name:var(--primitive-font-display)] font-semibold text-base leading-none tracking-tight text-[var(--foreground)]">
            {title}
          </span>
          {description && (
            <span className="text-xs text-[var(--muted-foreground)]">{description}</span>
          )}
        </div>
        <div className="flex">
          {seriesKeys.map((key) => (
            <button
              key={key}
              type="button"
              data-active={active === key}
              onClick={() => setActive(key)}
              className={cn(
                "relative flex flex-1 flex-col justify-center gap-1 border-t border-[var(--border)] px-6 py-4 text-left transition-[color,background-color,translate] duration-[var(--primitive-motion-fast)] ease-out",
                "even:border-l",
                "active:translate-y-px motion-reduce:active:translate-y-0",
                "sm:border-t-0 sm:border-l sm:px-8 sm:py-6",
                active === key && "bg-[var(--primitive-surface-selected)]"
              )}
            >
              <span className="text-xs text-[var(--muted-foreground)]">
                {config[key]?.label ?? key}
              </span>
              <span className="text-lg font-semibold leading-none tabular-nums text-[var(--foreground)] sm:text-3xl">
                {(totals[key] ?? 0).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="px-2 py-4 sm:p-6">
        <ChartContainer config={chartConfig} height={height}>
          <BarChart data={data} margin={{ left: 12, right: 12 }} barCategoryGap={2}>
            <CartesianGrid vertical={false} stroke={gridStroke} strokeDasharray="0" />
            <XAxis
              dataKey={xKey}
              tickLine={false}
              axisLine={false}
              tick={axisTick}
              tickMargin={8}
              minTickGap={32}
              interval={tickGapDays !== undefined ? Math.max(0, tickGapDays - 1) : "preserveEnd"}
              tickFormatter={dateFormat ? (value) => shortDateFormatter.format(new Date(value)) : undefined}
            />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              content={
                <ChartTooltipContent
                  nameKey={unitLabel ? "__unit" : undefined}
                  labelFormatter={dateFormat ? (value) => longDateFormatter.format(new Date(value)) : undefined}
                />
              }
            />
            <Bar dataKey={active} fill={`var(--color-${active})`} {...motionProps} />
          </BarChart>
        </ChartContainer>
      </div>
    </div>
  );
}
