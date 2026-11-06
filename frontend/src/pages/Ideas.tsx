/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { Link, useParams } from "react-router";

import { Empty, ScoreBar, Tip } from "@/components/bits";
import { RegimeBadge } from "@/components/RegimeBadge";
import { SortTable } from "@/components/SortTable";
import { Badge } from "@/components/wensity/badge";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/wensity/toggle";
import { useChrome } from "@/lib/chrome";
import { money, pct } from "@/lib/fmt";
import { getDensity } from "@/lib/theme";
import { usePage } from "@/lib/page";

// The classic Ideas page (templates/ideas.html): regime, how ideas are ranked, and every idea as a
// card (comfortable) or a ranked table (compact). Same data via /m/<code>/ideas?format=json.

function Level({ label, value, sub, cls = "" }: { label: React.ReactNode; value: string; sub?: string; cls?: string }) {
  return <div className="rounded-tile bg-surface-2/60 px-3 py-2.5"><div className="text-xs text-muted-foreground">{label}</div><div className={`font-num text-base font-semibold tabular-nums ${cls}`}>{value}</div>{sub && <div className="text-xs text-muted-foreground">{sub}</div>}</div>;
}

export function Ideas() {
  const { code = "IN" } = useParams();
  const { data, error } = usePage<Record<string, any>>(`/m/${code}/ideas`);
  const { chrome } = useChrome();
  const [view, setView] = useState<"cards" | "table">(getDensity() === "compact" ? "table" : "cards");
  if (error) return <Empty>Could not load ideas: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const m = code, scan = data.scan, cfg = data.cfg, tx = data.texts ?? {};
  const label = scan?.regime?.label;
  const minBuy = data.config_min + (label === "risk_off" ? data.penalty : 0);
  const minShort = data.config_min + (label === "risk_on" ? data.penalty : 0);
  const dir = (d: string) => (chrome?.shorts_on || d !== "LONG" ? <Badge size="sm" variant="outline">{d}</Badge> : null);
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">{m === "IN" ? "India" : "US"} · trade ideas</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">Ideas are every stock the strategy ranks well today. Signals are the trades it actually proposes to you, after the risk rules. {data.vs_signals} <Link to="/signals" className="text-accent">Signals →</Link></p>
      {!scan ? <Empty>No scan yet. Run <code>uv run trademare scan -m {m}</code>.</Empty> : (
        <>
          <Card className="mt-4 p-5">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 className="m-0 text-base font-semibold">Market regime<Tip k="regime" label="market regime" /> · {scan.asof}</h2><RegimeBadge regime={scan.regime} /></div>
            <ul className="m-0 list-disc pl-5 text-[13px]">{(tx.regime_reasons ?? []).map((r: string) => <li key={r}>{r}</li>)}</ul>
            <p className="mt-2 mb-0 text-xs text-muted-foreground">
              Ranked on<Tip k="weights" />: {(tx.weights ?? []).map(([name, w]: [string, number]) => `${name} (${pct(w, 0)})`).join(", ")}.
              {" "}A buy needs a score of at least {minBuy.toFixed(2)}{label === "risk_off" && " (raised because the market is risk-off)"}.
              {" "}{cfg.allow_short ? `A short needs −${minShort.toFixed(2)} or lower.` : "Short selling is off."}
              {" "}Exits: {tx.stop_rule}.
            </p>
          </Card>
          {scan.ideas.length === 0 ? <div className="mt-4"><Empty>No symbol passed the strategy rules on {scan.asof}.</Empty></div> : (
            <>
              <div className="mt-5 mb-3 flex items-center justify-between">
                <span className="text-[13px] text-muted-foreground">{scan.ideas.length} ideas</span>
                <ToggleGroup type="single" size="sm" value={view} onValueChange={(v) => { const x = Array.isArray(v) ? v[0] : v; if (x) setView(x as "cards" | "table"); }}>
                  <ToggleGroupItem value="cards">Cards</ToggleGroupItem><ToggleGroupItem value="table">Table</ToggleGroupItem>
                </ToggleGroup>
              </div>
              {view === "table" ? (
                <SortTable<any>
                  rows={scan.ideas.map((i: any, n: number) => ({ ...i, rank: n + 1 }))} rowKey={(i) => i.symbol} minWidth={1000}
                  cols={[
                    { key: "rank", head: "#", num: true, sort: (i) => i.rank, cell: (i) => i.rank },
                    { key: "sym", head: "Stock", sort: (i) => i.symbol, cell: (i) => <><Link to={`/m/${m}/${encodeURIComponent(i.symbol)}`} className="font-semibold text-accent">{i.symbol}</Link> {dir(i.direction)}</> },
                    { key: "sector", head: "Sector", sort: (i) => i.sector ?? "", cell: (i) => <span className="text-xs text-muted-foreground">{i.sector}</span> },
                    { key: "score", head: <>Score<Tip k="score" /></>, num: true, sort: (i) => i.composite, cell: (i) => <ScoreBar value={i.composite} /> },
                    { key: "entry", head: <>Entry<Tip k="entry_limit" label="the entry limit" /></>, num: true, sort: (i) => i.levels.entry, cell: (i) => money(i.levels.entry, m, false) },
                    { key: "stop", head: <>Stop-loss<Tip k="stop" label="the stop-loss" /></>, num: true, sort: (i) => i.levels.stop / i.levels.entry - 1, cell: (i) => <>{money(i.levels.stop, m, false)} <span className="text-xs text-muted-foreground">{pct(i.levels.stop / i.levels.entry - 1, 1, true)}</span></> },
                    { key: "risk", head: "Risk / share", num: true, sort: (i) => i.levels.risk_per_share, cell: (i) => money(i.levels.risk_per_share, m, false) },
                    { key: "pc", head: "For · against", cell: (i) => { const rs = tx.splits?.[i.symbol] ?? { pro: [], con: [] }; return <span title={[...rs.pro, ...rs.con].join(" · ")}><span className="text-up">▲ {rs.pro.length}</span> <span className="text-down">▼ {rs.con.length}</span>{rs.pro[0] && <span className="ml-1 text-xs text-muted-foreground">{rs.pro[0].length > 46 ? `${rs.pro[0].slice(0, 45)}…` : rs.pro[0]}</span>}</span>; } },
                  ]}
                />
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,420px),1fr))] gap-4">
                  {scan.ideas.map((i: any) => {
                    const lv = i.levels, rs = tx.splits?.[i.symbol] ?? { pro: [], con: [], neutral: [] };
                    return (
                      <Card key={i.symbol} className="p-5">
                        <div className="flex items-start justify-between gap-3">
                          <div><Link to={`/m/${m}/${encodeURIComponent(i.symbol)}`} className="text-lg font-bold text-accent">{i.symbol}</Link> {dir(i.direction)}<div className="text-xs text-muted-foreground">{i.name} · {i.sector}</div></div>
                          <div className="text-right"><ScoreBar value={i.composite} /><Tip k="score" /><div className="text-xs text-muted-foreground">confidence {Math.round(i.confidence * 100)}%<Tip k="confidence" /></div></div>
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                          <Level label={<>Entry (limit)<Tip k="entry_limit" label="the entry limit" /></>} value={money(lv.entry, m, false)} />
                          <Level label={<>Stop-loss<Tip k="stop" label="the stop-loss" /></>} value={money(lv.stop, m, false)} cls="text-down" sub={pct(lv.stop / lv.entry - 1, 1, true)} />
                          <Level label={<>Target<Tip k="target" /></>} value={money(lv.target, m, false)} cls="text-up" sub={lv.target ? pct(lv.target / lv.entry - 1, 1, true) : undefined} />
                          <Level label={<>Risk / share<Tip k="risk_per_share" label="risk per share" /></>} value={money(lv.risk_per_share, m, false)} />
                        </div>
                        <div className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3 text-[13px]">
                          {rs.pro.length > 0 && <div><div className="mb-1 text-xs font-semibold uppercase text-up">For</div><ul className="m-0 list-disc pl-5">{rs.pro.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
                          {rs.con.length > 0 && <div><div className="mb-1 text-xs font-semibold uppercase text-down">Against</div><ul className="m-0 list-disc pl-5">{rs.con.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
                          {rs.neutral?.length > 0 && <div><div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Also noted</div><ul className="m-0 list-disc pl-5">{rs.neutral.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
                        </div>
                      </Card>
                    );
                  })}
                </div>
              )}
              <p className="mt-4 text-xs text-muted-foreground">Ideas are not orders: signals with sizes and your approval come from these. Not financial advice.</p>
            </>
          )}
        </>
      )}
    </>
  );
}
