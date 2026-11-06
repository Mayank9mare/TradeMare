/* eslint-disable @typescript-eslint/no-explicit-any */
import { useParams } from "react-router";

import { Empty, H2, TableWrap, Tip } from "@/components/bits";
import { Ratio, Ratios } from "@/components/Ratio";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { money, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic paper-vs-backtest page (templates/tracking.html), same data via ?format=json.

const th = "[&_th]:px-3.5 [&_th]:py-2.5 [&_th]:font-medium";
const td = "border-t border-line [&_td]:px-3.5 [&_td]:py-2.5 hover:bg-row-hover";

export function Tracking() {
  const { code = "IN" } = useParams();
  const { data, error } = usePage<Record<string, any>>(`/portfolio/${code}/tracking`);
  if (error) return <Empty>Could not load the comparison: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const t = data.t, m = code, lines = data.lines;
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Paper vs backtest<Tip k="tracking" label="this comparison" /> <small className="text-sm font-normal text-muted-foreground">{m === "IN" ? "India" : "US"}</small></h1>
      <p className="mt-1 mb-5 text-[13px] text-muted-foreground">The backtest replayed over exactly the days the {data.simulated ? "paper account" : "account"} has been running. If the backtest is an honest model, both should make the same trades and similar returns.</p>
      {t.status === "no_history" ? <Empty>No trading days recorded yet. The comparison starts after the first daily run.</Empty>
        : t.status === "too_short" ? <Empty>{t.sessions} trading day{t.sessions !== 1 ? "s" : ""} recorded since {t.start}. The comparison needs at least {data.min_sessions}; check back after a week of daily runs.</Empty>
        : (<>
          <Ratios>
            <Ratio label="Period">{t.start} → {t.end}</Ratio>
            <Ratio label="Sessions">{t.sessions}</Ratio>
            <Ratio label="Paper return" valueClass={tone(t.paper_return)}>{pct(t.paper_return, 2, true)}</Ratio>
            <Ratio label="Backtest return" valueClass={tone(t.backtest_return)}>{pct(t.backtest_return, 2, true)}</Ratio>
            <Ratio label={<>Gap<Tip k="gap" /></>} valueClass={tone(t.gap)}>{pct(t.gap, 2, true)}</Ratio>
            <Ratio label={<>Tracking error (yearly)<Tip k="tracking_error" label="tracking error" /></>}>{t.tracking_error != null ? pct(t.tracking_error, 1) : "—"}</Ratio>
          </Ratios>
          {lines && (
            <Card className="mt-4 p-5">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 className="m-0 text-base font-semibold">Growth of 1.00</h2>
                <span className="text-xs text-muted-foreground"><span className="mr-1 inline-block h-0.5 w-4 bg-accent align-middle" />Paper <span className="mr-1 ml-3 inline-block h-0.5 w-4 bg-muted-foreground align-middle" />Backtest</span></div>
              <svg viewBox="0 0 600 160" preserveAspectRatio="none" className="h-40 w-full" role="img" aria-label={`Paper ${pct(t.paper_return, 1, true)} vs backtest ${pct(t.backtest_return, 1, true)}`}>
                <path d={lines.backtest} fill="none" stroke="var(--tm-muted)" strokeWidth={2} strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
                <path d={lines.paper} fill="none" stroke="var(--tm-accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
              </svg>
              <div className="mt-1 flex justify-between text-xs text-muted-foreground"><span>{t.start}</span><span>low {lines.low.toFixed(3)} · high {lines.high.toFixed(3)}</span><span>{t.end}</span></div>
            </Card>
          )}
          <H2 count={t.matched.length}>Same trades</H2>
          {t.matched.length ? (
            <TableWrap><table className="w-full min-w-[760px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Symbol</th><th>Paper entry</th><th>Backtest entry</th><th className="text-right">Paper price</th><th className="text-right">Backtest price</th><th className="text-right">Price gap</th><th>Note</th></tr></thead>
              <tbody>{t.matched.map((x: any, i: number) => <tr key={i} className={td}><td><b>{x.symbol}</b></td><td>{x.paper_date}</td><td>{x.backtest_date}</td><td className="text-right tabular-nums">{money(x.paper_price, m, false)}</td><td className="text-right tabular-nums">{money(x.backtest_price, m, false)}</td><td className="text-right tabular-nums">{x.price_gap != null ? pct(x.price_gap, 2, true) : "—"}</td><td>{x.note}</td></tr>)}</tbody>
            </table></TableWrap>
          ) : <Empty>No trades in common yet.</Empty>}
          <H2 count={t.decisions.length}>Differences from your decisions</H2>
          {t.decisions.length ? (
            <TableWrap><table className="w-full min-w-[600px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Symbol</th><th>Backtest entry</th><th className="text-right">Price</th><th>Why paper differs</th></tr></thead>
              <tbody>{t.decisions.map((x: any, i: number) => <tr key={i} className={td}><td><b>{x.symbol}</b></td><td>{x.backtest_date}</td><td className="text-right tabular-nums">{money(x.backtest_price, m, false)}</td><td>{x.note}</td></tr>)}</tbody>
            </table></TableWrap>
          ) : <Empty>None. Every backtest trade was also taken on paper.</Empty>}
          <H2 count={t.unexplained.length}>Unexplained differences<Tip k="unexplained" label="unexplained differences" /></H2>
          {t.unexplained.length ? (<>
            <div className="mb-3 rounded-tile border border-critical/40 px-4 py-3 text-[13px] text-down">These trades happened on one side only, with no decision of yours behind them. That points at a difference between the backtester and live trading, or at data that changed after the fact. Worth investigating before trusting real money to the strategy.</div>
            <TableWrap><table className="w-full min-w-[600px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Symbol</th><th>Paper</th><th>Backtest</th><th>Note</th></tr></thead>
              <tbody>{t.unexplained.map((x: any, i: number) => <tr key={i} className={td}><td><b>{x.symbol}</b></td><td>{x.paper_date ? `${x.paper_date} @ ${money(x.paper_price, m, false)}` : "—"}</td><td>{x.backtest_date ? `${x.backtest_date} @ ${money(x.backtest_price, m, false)}` : "—"}</td><td>{x.note}</td></tr>)}</tbody>
            </table></TableWrap>
          </>) : <Empty>None. The backtester and paper trading agree.</Empty>}
        </>)}
    </>
  );
}
