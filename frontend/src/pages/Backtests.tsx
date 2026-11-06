/* eslint-disable @typescript-eslint/no-explicit-any */
import { createChart } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { Empty, H2, Tip } from "@/components/bits";
import { SortTable } from "@/components/SortTable";
import { Badge } from "@/components/wensity/badge";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { getJSON } from "@/lib/api";
import { money, num, pct, tone, vs } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic Backtests list and run pages (templates/backtests.html, backtest.html).

export function Backtests() {
  const { data, error } = usePage<Record<string, any>>("/backtests");
  if (error) return <Empty>Could not load backtests: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Backtests<Tip k="backtest" label="a backtest" /></h1>
      <Card className="mt-4 p-5">
        <h2 className="m-0 text-base font-semibold">Research verdicts<Tip k="oos" label="honest tests" /></h2>
        <p className="mt-1 text-xs text-muted-foreground">Each study fixed its pass bar before running, then tested on history it was never tuned on.</p>
        <ul className="m-0 list-none space-y-1.5 p-0 text-[13px]">{data.research.map((r: any) => (
          <li key={r.doc}><Badge size="sm" variant={r.verdict === "pass" ? "success" : "danger"}>{r.verdict}</Badge> <a href={`https://github.com/Mayank9mare/TradeMare/blob/main/docs/research/${r.doc}`} className="font-semibold text-accent">{r.title}</a> <span className="text-xs text-muted-foreground">{r.note}</span></li>
        ))}</ul>
      </Card>
      <p className="text-[13px] text-muted-foreground">Green ▲ means better than simply holding the index over the same dates; red ▼ means worse. <Badge size="sm" variant="info">your settings</Badge> marks runs that used the rules you trade now.</p>
      {!data.runs.length ? <Empty>No backtests yet. Run <code>uv run trademare backtest -m US</code>.</Empty> : (
        <SortTable<any>
          rows={data.runs} rowKey={(r) => r.id} minWidth={1150}
          cols={[
            { key: "run", head: <>Run<Tip k="overrides" label="what each run tested" /></>, sort: (r) => r.created_at, cell: (r) => <><Link to={`/backtests/${r.id}`} className="font-semibold text-accent">{r.label}</Link>{r.current && <> <Badge size="sm" variant="info">your settings</Badge></>}<div className="text-xs text-muted-foreground">{String(r.created_at).replace("T", " ").slice(0, 16)}</div></> },
            { key: "mk", head: "Market", sort: (r) => r.market, cell: (r) => r.market },
            { key: "p", head: "Period", sort: (r) => r.start, cell: (r) => <span className="whitespace-nowrap">{r.start} → {r.end}</span> },
            { key: "cagr", head: <>CAGR<Tip k="cagr" label="CAGR" /></>, num: true, sort: (r) => r.summary.strategy.cagr, cell: (r) => <span className={vs(r.summary.strategy.cagr, r.summary.benchmark?.cagr)}>{pct(r.summary.strategy.cagr, 1, true)}</span> },
            { key: "icagr", head: "Index CAGR", num: true, sort: (r) => r.summary.benchmark?.cagr ?? null, cell: (r) => pct(r.summary.benchmark?.cagr, 1, true) },
            { key: "sh", head: <>Sharpe<Tip k="sharpe" label="Sharpe" /></>, num: true, sort: (r) => r.summary.strategy.sharpe, cell: (r) => <span className={vs(r.summary.strategy.sharpe, r.summary.benchmark?.sharpe)}>{num(r.summary.strategy.sharpe, 2)}</span> },
            { key: "ish", head: "Index Sharpe", num: true, sort: (r) => r.summary.benchmark?.sharpe ?? null, cell: (r) => num(r.summary.benchmark?.sharpe, 2) },
            { key: "dd", head: <>Max DD<Tip k="max_drawdown" label="max drawdown" /></>, num: true, sort: (r) => r.summary.strategy.max_drawdown, cell: (r) => <span className={vs(r.summary.strategy.max_drawdown, r.summary.benchmark?.max_drawdown)}>{pct(r.summary.strategy.max_drawdown, 1)}</span> },
            { key: "tr", head: "Trades", num: true, sort: (r) => r.summary.strategy.trades, cell: (r) => num(r.summary.strategy.trades, 0) },
            { key: "wr", head: <>Win rate<Tip k="win_rate" label="win rate" /></>, num: true, sort: (r) => r.summary.strategy.win_rate, cell: (r) => pct(r.summary.strategy.win_rate, 0) },
            { key: "oos", head: <>OOS CAGR<Tip k="oos" label="out-of-sample" /></>, num: true, sort: (r) => r.summary.out_of_sample?.strategy?.cagr ?? null, cell: (r) => <span className={tone(r.summary.out_of_sample?.strategy?.cagr)}>{pct(r.summary.out_of_sample?.strategy?.cagr, 1, true)}</span> },
          ]}
        />
      )}
    </>
  );
}

function RunCharts({ id }: { id: string }) {
  const eqEl = useRef<HTMLDivElement>(null), ddEl = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let charts: ReturnType<typeof createChart>[] = [];
    let alive = true;
    getJSON<any>(`/api/backtests/${encodeURIComponent(id)}`).then((data) => {
      if (!alive || !eqEl.current || !ddEl.current) return;
      const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
      const base = { autoSize: true, layout: { background: { color: css("--tm-surface") }, textColor: css("--tm-muted") }, grid: { vertLines: { color: css("--tm-border") }, horzLines: { color: css("--tm-border") } }, rightPriceScale: { borderColor: css("--tm-border") }, timeScale: { borderColor: css("--tm-border") } };
      const eq = createChart(eqEl.current, base);
      eq.addLineSeries({ color: css("--tm-accent"), lineWidth: 2 }).setData(data.equity);
      if (data.benchmark) eq.addLineSeries({ color: css("--tm-muted"), lineWidth: 1 }).setData(data.benchmark);
      eq.timeScale().fitContent();
      const dd = createChart(ddEl.current, base);
      const down = css("--tm-down");
      dd.addBaselineSeries({ baseValue: { type: "price", price: 0 }, lineWidth: 1, bottomLineColor: down, bottomFillColor1: `${down}22`, bottomFillColor2: `${down}66`, priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` } }).setData(data.drawdown);
      dd.timeScale().fitContent();
      eq.timeScale().subscribeVisibleLogicalRangeChange((r) => r && dd.timeScale().setVisibleLogicalRange(r));
      dd.timeScale().subscribeVisibleLogicalRangeChange((r) => r && eq.timeScale().setVisibleLogicalRange(r));
      charts = [eq, dd];
    }, () => setFailed(true));
    return () => { alive = false; charts.forEach((c) => c.remove()); };
  }, [id]);
  if (failed) return <Empty>Could not load the equity curve.</Empty>;
  return (
    <>
      <div className="mb-2 text-xs text-muted-foreground"><i className="mr-1 inline-block h-0.5 w-4 bg-accent align-middle" />Strategy <i className="mr-1 ml-3 inline-block h-0.5 w-4 bg-muted-foreground align-middle" />Index (buy &amp; hold)</div>
      <div ref={eqEl} className="h-[360px]" /><div ref={ddEl} className="mt-2 h-[140px]" />
    </>
  );
}

const PERF: [string, string, string][] = [["total_return", "Total return", "pct"], ["cagr", "CAGR", "pct"], ["volatility", "Volatility", "pct0"], ["sharpe", "Sharpe", "num"], ["sortino", "Sortino", "num"], ["max_drawdown", "Max drawdown", "pct0"], ["calmar", "Calmar", "num"], ["longest_drawdown_days", "Longest drawdown (days)", "int"]];
const fmtKind = (v: any, kind: string) => (kind === "pct" ? pct(v, 1, true) : kind === "pct0" ? pct(v, 1) : kind === "int" ? num(v, 0) : num(v, 2));

export function Backtest() {
  const { id = "" } = useParams();
  const { data, error } = usePage<Record<string, any>>(`/backtests/${encodeURIComponent(id)}`);
  if (error) return <Empty>Could not load this backtest: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const run = data.run, s = run.summary, st = s.strategy, bm = s.benchmark ?? {}, m = run.market;
  return (
    <>
      <p className="m-0 text-xs text-muted-foreground"><Link to="/backtests" className="text-accent">Backtests</Link> / {run.id}</p>
      <h1 className="mt-1 mb-0 text-[26px] font-bold tracking-tight">{m === "IN" ? "India" : "US"} backtest<Tip k="backtest" label="a backtest" /> <small className="text-sm font-normal text-muted-foreground">{run.start} → {run.end}</small></h1>
      <p className="text-[13px] text-muted-foreground">Capital {money(run.initial_capital, m, false)} → <b className="text-text">{money(run.final_equity, m, false)}</b> · {run.open_positions} positions open at end{run.overrides?.length ? <> · overrides<Tip k="overrides" />: <code>{run.overrides.join(" ")}</code></> : null}</p>
      <Card className="p-4"><RunCharts id={run.id} /></Card>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="m-0 mb-2 text-base font-semibold">Performance</h2>
          <table className="w-full text-[13px]"><thead className="text-xs text-muted-foreground"><tr className="[&_th]:py-1.5"><th /><th className="text-right">Strategy</th><th className="text-right">Index<Tip k="index_benchmark" label="the index" /></th></tr></thead>
            <tbody>{PERF.map(([key, label, kind]) => (
              <tr key={key} className="border-t border-line [&_td]:py-1.5"><td>{label}<Tip k={key === "longest_drawdown_days" ? "longest_drawdown" : key} label={label} /></td>
                <td className={`text-right tabular-nums ${vs(st[key], bm[key], !["volatility", "longest_drawdown_days"].includes(key))}`}>{fmtKind(st[key], kind)}</td><td className="text-right tabular-nums">{fmtKind(bm[key], kind)}</td></tr>
            ))}</tbody></table>
        </Card>
        <Card className="p-5">
          <h2 className="m-0 mb-2 text-base font-semibold">Trades</h2>
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px] [&_dd]:m-0 [&_dt]:text-muted-foreground">
            <dt>Trades</dt><dd>{num(st.trades, 0)}</dd>
            <dt>Win rate<Tip k="win_rate" label="win rate" /></dt><dd>{pct(st.win_rate, 1)}</dd>
            <dt>Avg win / loss</dt><dd><span className="text-up">{pct(st.avg_win_pct, 1, true)}</span> / <span className="text-down">{pct(st.avg_loss_pct, 1, true)}</span></dd>
            <dt>Profit factor<Tip k="profit_factor" label="profit factor" /></dt><dd>{num(st.profit_factor, 2)}</dd>
            <dt>Avg R per trade<Tip k="avg_r" label="average R" /></dt><dd>{num(st.avg_r, 2)}</dd>
            <dt>Avg holding</dt><dd>{num(st.avg_bars_held, 1)} sessions</dd>
            <dt>Exposure<Tip k="exposure" /></dt><dd>{pct(s.exposure_avg, 0)} avg · in market {pct(s.time_in_market, 0)} of days</dd>
            <dt>Costs paid</dt><dd>{money(st.total_costs, m, false)}</dd>
            <dt>Exits</dt><dd>{Object.entries(s.exit_reasons ?? {}).map(([k, v]) => `${k} ${v}`).join(", ")}</dd>
          </dl>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="m-0 mb-2 text-base font-semibold">By year</h2>
          <table className="w-full text-[13px]"><thead className="text-xs text-muted-foreground"><tr className="[&_th]:py-1.5"><th className="text-left">Year</th><th className="text-right">Strategy</th><th className="text-right">Index</th><th className="text-right">Trades</th></tr></thead>
            <tbody>{Object.entries(s.by_year ?? {}).map(([y, r]: [string, any]) => <tr key={y} className="border-t border-line [&_td]:py-1.5"><td>{y}</td><td className={`text-right tabular-nums ${vs(r.strategy, r.benchmark)}`}>{pct(r.strategy, 1, true)}</td><td className="text-right tabular-nums">{pct(r.benchmark, 1, true)}</td><td className="text-right tabular-nums">{r.trades}</td></tr>)}</tbody></table>
        </Card>
        {s.in_sample && (
          <Card className="p-5">
            <h2 className="m-0 mb-2 text-base font-semibold">In-sample vs out-of-sample<Tip k="oos" label="in- and out-of-sample" /> <small className="text-xs font-normal text-muted-foreground">split {s.split_date}</small></h2>
            <div className="overflow-x-auto"><table className="w-full min-w-[480px] text-[13px]"><thead className="text-xs text-muted-foreground"><tr className="[&_th]:py-1.5 [&_th]:text-right"><th /><th>CAGR</th><th>Index</th><th>Sharpe</th><th>Index</th><th>Max DD</th><th>Trades</th></tr></thead>
              <tbody>{["in_sample", "out_of_sample"].map((label) => { const seg = s[label]; return (
                <tr key={label} className="border-t border-line [&_td]:py-1.5 [&_td]:text-right [&_td]:tabular-nums"><td className="!text-left">{label.replace(/_/g, "-")}</td>
                  <td className={vs(seg.strategy.cagr, seg.benchmark.cagr)}>{pct(seg.strategy.cagr, 1, true)}</td><td>{pct(seg.benchmark.cagr, 1, true)}</td>
                  <td>{num(seg.strategy.sharpe, 2)}</td><td>{num(seg.benchmark.sharpe, 2)}</td><td className="text-down">{pct(seg.strategy.max_drawdown, 1)}</td><td>{num(seg.strategy.trades, 0)}</td></tr>
              ); })}</tbody></table></div>
            <p className="mb-0 text-xs text-muted-foreground">Out-of-sample is the trailing {Math.round((run.settings?.backtest?.out_of_sample_fraction ?? 0) * 100)}% of the period. Tune parameters on in-sample results only.</p>
          </Card>
        )}
      </div>
      <H2 count={data.trades.length}>Trades <small className="text-sm font-normal text-muted-foreground">newest first</small></H2>
      <div className="max-h-[520px] overflow-y-auto">
        <SortTable<any>
          rows={data.trades} rowKey={(t) => `${t.symbol}-${t.entry_date}-${t.exit_date}`} minWidth={1100}
          cols={[
            { key: "s", head: "Symbol", sort: (t) => t.symbol, cell: (t) => <Link to={`/m/${m}/${encodeURIComponent(t.symbol)}`} className="text-accent">{t.symbol}</Link> },
            { key: "d", head: "Side", sort: (t) => t.direction, cell: (t) => t.direction },
            { key: "e", head: "Entry", sort: (t) => t.entry_date, cell: (t) => String(t.entry_date).slice(0, 10) },
            { key: "x", head: "Exit", sort: (t) => t.exit_date, cell: (t) => String(t.exit_date).slice(0, 10) },
            { key: "ep", head: "Entry px", num: true, sort: (t) => t.entry_price, cell: (t) => num(t.entry_price, 2) },
            { key: "xp", head: "Exit px", num: true, sort: (t) => t.exit_price, cell: (t) => num(t.exit_price, 2) },
            { key: "q", head: "Qty", num: true, sort: (t) => t.qty, cell: (t) => t.qty },
            { key: "pl", head: "P&L", num: true, sort: (t) => t.pnl, cell: (t) => <span className={tone(t.pnl)}>{money(t.pnl, m, false)}</span> },
            { key: "r", head: "Return", num: true, sort: (t) => t.return_pct, cell: (t) => <span className={tone(t.return_pct)}>{pct(t.return_pct, 1, true)}</span> },
            { key: "rm", head: <>R<Tip k="r_multiple" label="R multiple" /></>, num: true, sort: (t) => t.r_multiple, cell: (t) => <span className={tone(t.r_multiple)}>{num(t.r_multiple, 2)}</span> },
            { key: "b", head: "Days", num: true, sort: (t) => t.bars_held, cell: (t) => t.bars_held },
            { key: "why", head: "Exit reason", sort: (t) => t.exit_reason ?? "", cell: (t) => <span className="text-xs text-muted-foreground">{t.exit_reason}</span> },
          ]}
        />
      </div>
      <p className="text-xs text-muted-foreground">Backtests use current index constituents (survivorship bias<Tip k="survivorship" label="survivorship bias" />) and modelled costs/slippage. Past results do not predict future returns.</p>
    </>
  );
}
