import { createContext, useContext, useEffect, useState } from "react";

import { getJSON } from "@/lib/api";
import { pct, signed, tone } from "@/lib/fmt";

// Small pieces shared by every page: the (i) explanations, score bars, a small price chart,
// section headings and empty notes, styled like the classic dashboard.

const Glossary = createContext<Record<string, string>>({});

export function GlossaryProvider({ children }: { children: React.ReactNode }) {
  const [terms, setTerms] = useState<Record<string, string>>({});
  useEffect(() => { getJSON<Record<string, string>>("/api/glossary").then(setTerms, () => {}); }, []);
  return <Glossary.Provider value={terms}>{children}</Glossary.Provider>;
}

/** The (i) icon with the plain-English explanation of a term; hover or focus to read it. */
export function Tip({ k, label }: { k: string; label?: string }) {
  const text = useContext(Glossary)[k];
  if (!text) return null;
  return (
    <span className="group/tip relative ml-1 inline-flex align-middle">
      <button type="button" aria-label={`What is ${label ?? k.replace(/_/g, " ")}?`} className="grid size-4 place-items-center rounded-full border border-line-strong text-[10px] leading-none text-muted-foreground hover:border-accent hover:text-accent focus-visible:outline-2 focus-visible:outline-accent">i</button>
      <span role="tooltip" className="pointer-events-none invisible absolute bottom-full left-1/2 z-50 mb-2 w-64 -translate-x-1/2 rounded-tile border border-line bg-surface p-2.5 text-left text-xs font-normal tracking-normal text-text normal-case opacity-0 shadow-card transition-opacity group-focus-within/tip:visible group-focus-within/tip:opacity-100 group-hover/tip:visible group-hover/tip:opacity-100">{text}</span>
    </span>
  );
}

/** A -1..+1 score as a centred bar with its value (the classic score_bar). */
export function ScoreBar({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  const w = Math.min(Math.abs(value), 1) * 50;
  return (
    <span className={`inline-flex items-center gap-2 font-num text-[13px] tabular-nums ${tone(value)}`} title={signed(value)}>
      <span className="relative h-1.5 w-16 rounded-full bg-surface-2">
        <span className={`absolute top-0 h-full rounded-full ${value >= 0 ? "bg-good" : "bg-critical"}`} style={value >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
      </span>
      {signed(value)}
    </span>
  );
}

export interface SparkLine { path: string; last_x: number; change: number; low: number; high: number; start: string; end: string }

/** The classic dashboard's small price line (a precomputed SVG path, 240 x 48). */
export function Spark({ line, label }: { line: SparkLine | null | undefined; label: string }) {
  if (!line) return null;
  return (
    <figure className="m-0">
      <svg viewBox="0 0 240 48" preserveAspectRatio="none" className="h-14 w-full" role="img" aria-label={`${label}: ${pct(line.change, 1, true)} from ${line.start} to ${line.end}`}>
        <title>{`${label} · ${line.start} → ${line.end} · low ${line.low.toFixed(2)} · high ${line.high.toFixed(2)} · ${pct(line.change, 1, true)}`}</title>
        <path d={`${line.path} L${line.last_x} 48 L4 48 Z`} className="fill-accent-soft" />
        <path d={line.path} className="fill-none stroke-accent" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground"><span>{label}</span><span className={tone(line.change)}>{pct(line.change, 1, true)}</span></figcaption>
    </figure>
  );
}

export function H2({ children, count }: { children: React.ReactNode; count?: number }) {
  return <h2 className="mt-8 mb-3 text-lg font-semibold">{children}{count !== undefined && <small className="ml-1.5 text-sm font-normal text-muted-foreground">{count}</small>}</h2>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-tile bg-surface-2 px-4 py-3.5 text-center text-[13px] text-muted-foreground">{children}</div>;
}

export function Pill({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted-foreground ${className}`}>{children}</span>;
}

/** The ?msg= / ?err= banner a classic action leaves behind. */
export function Flash({ msg, err }: { msg?: string | null; err?: string | null }) {
  if (!msg && !err) return null;
  return <div className={`mb-4 rounded-tile border px-4 py-2.5 text-[13px] ${err ? "border-critical/40 text-down" : "border-good/40 text-up"}`}>{err ?? msg}</div>;
}

/** Wraps a wide table so the page itself never scrolls sideways. */
export function TableWrap({ children }: { children: React.ReactNode }) {
  return <div className="overflow-x-auto rounded-card border border-line bg-surface">{children}</div>;
}
