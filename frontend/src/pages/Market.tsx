/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { Empty, ScoreBar, Tip } from "@/components/bits";
import { RegimeBadge } from "@/components/RegimeBadge";
import { SortTable, type Col } from "@/components/SortTable";
import { Badge } from "@/components/wensity/badge";
import { SkeletonTable } from "@/components/wensity/skeleton";
import { money, num, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic market list (templates/market.html): every stock in the universe, searchable,
// filterable by sector and sortable by any column. Same data via /m/<code>?format=json.

const UNIVERSE: Record<string, string> = { nifty500: "Nifty 500", nifty200: "Nifty 200", sp500: "S&P 500" };

export function Market() {
  const { code = "IN" } = useParams();
  const { data, error } = usePage<Record<string, any>>(`/m/${code}`);
  const [q, setQ] = useState("");
  const [sector, setSector] = useState("");
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return (data.rows as any[]).filter((r) => (!sector || r.sector === sector) && (!needle || `${r.symbol} ${r.name}`.toLowerCase().includes(needle)));
  }, [data, q, sector]);
  if (error) return <Empty>Could not load the market: {error}</Empty>;
  if (!data) return <SkeletonTable />;
  const m = code, scores = data.scores as Record<string, number>, ideas = data.ideas as Record<string, string>;
  const ret = (k: string): Col<any> => ({
    key: k, num: true, head: <>{k.slice(4).toUpperCase()}<Tip k={k} label={k.slice(4).toUpperCase()} /></>,
    sort: (r) => r.returns[k] ?? null, cell: (r) => <span className={tone(r.returns[k])}>{pct(r.returns[k], 1, true)}</span>,
  });
  const ratio = (k: string, head: React.ReactNode, f: (v: any) => React.ReactNode, colour = false): Col<any> => ({
    key: k, num: true, head, sort: (r) => r.ratios?.[k] ?? null, cell: (r) => <span className={colour ? tone(r.ratios?.[k]) : ""}>{f(r.ratios?.[k])}</span>,
  });
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">{m === "IN" ? "India" : "US"} · {UNIVERSE[data.cfg.universe] ?? data.cfg.universe} <small className="text-sm font-normal text-muted-foreground">{data.rows.length} symbols</small></h1>
      {data.scan && (
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[13px] text-muted-foreground">
          Scores from scan {data.scan.asof} · <RegimeBadge regime={data.scan.regime} /><Tip k="regime" label="market regime" /> · <Link to={`/m/${m}/ideas`} className="text-accent">{data.scan.ideas.length} ideas →</Link>
        </p>
      )}
      <div className="my-4 flex flex-wrap items-center gap-3">
        <input type="search" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol or name…" aria-label="Search symbol or name" className="w-64 max-w-full rounded-tile border border-line bg-surface px-3 py-2 text-[13px]" />
        <select value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector" className="rounded-tile border border-line bg-surface px-3 py-2 text-[13px]">
          <option value="">All sectors</option>{(data.sectors as string[]).map((s) => <option key={s}>{s}</option>)}
        </select>
        <span className="text-[13px] text-muted-foreground">{rows.length} of {data.rows.length}</span>
      </div>
      <SortTable<any>
        rows={rows} rowKey={(r) => r.symbol} minWidth={1300}
        cols={[
          { key: "sym", head: "Symbol", sort: (r) => r.symbol, cell: (r) => <><Link to={`/m/${m}/${encodeURIComponent(r.symbol)}`} className="font-semibold text-accent">{r.symbol}</Link>{ideas[r.symbol] && <> <Badge size="sm" variant="info">{ideas[r.symbol] === "LONG" ? "idea" : "short idea"}</Badge></>}<div className="text-xs text-muted-foreground">{r.name}</div></> },
          { key: "score", head: <>Score<Tip k="score" /></>, num: true, sort: (r) => scores[r.symbol] ?? null, cell: (r) => <ScoreBar value={scores[r.symbol]} /> },
          { key: "sector", head: <>Sector<Tip k="sector" /></>, sort: (r) => r.sector ?? "", cell: (r) => <span className="text-xs text-muted-foreground">{r.sector}</span> },
          { key: "close", head: "Close", num: true, sort: (r) => r.close ?? null, cell: (r) => num(r.close, 2) },
          ret("chg_1d"), ret("chg_1m"), ret("chg_6m"), ret("chg_1y"),
          { key: "hi", head: <>52W hi<Tip k="from_52w_high" label="52-week high" /></>, num: true, sort: (r) => r.from_52w_high ?? null, cell: (r) => pct(r.from_52w_high, 0) },
          ratio("market_cap", <>Mkt cap<Tip k="market_cap" label="market cap" /></>, (v) => money(v, m)),
          ratio("pe", <>P/E<Tip k="pe" label="P/E" /></>, (v) => num(v, 1)),
          ratio("revenue_growth", <>Rev gr.<Tip k="revenue_growth" label="revenue growth" /></>, (v) => pct(v, 0, true), true),
          ratio("net_margin", <>Net mgn<Tip k="net_margin" label="net margin" /></>, (v) => pct(v, 0)),
          ratio("roe", <>ROE<Tip k="roe" label="ROE" /></>, (v) => pct(v, 0)),
          ratio("debt_to_equity", <>D/E<Tip k="debt_to_equity" label="debt to equity" /></>, (v) => num(v, 2)),
        ]}
      />
    </>
  );
}
