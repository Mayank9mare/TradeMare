/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { Empty, Flash, H2, TableWrap, Tip } from "@/components/bits";
import { LineChart } from "@/components/LineChart";
import { Ratio, Ratios } from "@/components/Ratio";
import { SortTable } from "@/components/SortTable";
import { Badge } from "@/components/wensity/badge";
import { Button } from "@/components/wensity/button";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { useToastManager } from "@/components/wensity/toast";
import { postForm } from "@/lib/actions";
import { getJSON } from "@/lib/api";
import { useChrome } from "@/lib/chrome";
import { localTime, money, num, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic Portfolio page (templates/portfolio.html), same data via /portfolio/<m>?format=json.

const th = "[&_th]:px-3.5 [&_th]:py-2.5 [&_th]:font-medium";
const td = "border-t border-line [&_td]:px-3.5 [&_td]:py-2.5 [&_td]:align-top hover:bg-row-hover";
const setupLabel = (s: string) => (s.startsWith("setup:") ? s.slice(6).replace(/_/g, " ").toUpperCase() : s);
const STAGE: Record<string, "success" | "info" | "warning" | "neutral"> = { filled: "success", waiting: "info", "not filled": "warning", cancelled: "neutral", rejected: "neutral" };

function Equity({ code }: { code: string }) {
  const [points, setPoints] = useState<{ time: string; value: number }[] | null>(null);
  useEffect(() => { getJSON<{ equity: { time: string; value: number }[] }>(`/api/portfolio/${code}/equity`).then((d) => setPoints(d.equity), () => setPoints([])); }, [code]);
  if (!points) return <div className="h-[260px]" />;
  return <LineChart data={points as any} emptyText={`The equity chart starts after 5 daily runs (${points.length} recorded so far).`} />;
}

export function Portfolio() {
  const { code = "IN" } = useParams();
  const { data, error, reload } = usePage<Record<string, any>>(`/portfolio/${code}`);
  const { chrome, refresh } = useChrome();
  const toasts = useToastManager();
  const [busy, setBusy] = useState("");
  if (error) return <Empty>Could not load the portfolio: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const m = code;
  const { cfg, state, positions, orders, ended, closed, outside, events } = data as any;
  const act = async (path: string, label: string) => {
    if (!chrome) return;
    setBusy(label);
    const r = await postForm(path, chrome.csrf_token);
    setBusy("");
    toasts.add({ title: r.message, data: { variant: r.ok ? "success" : "error" } });
    refresh();
    reload();
  };
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">
        {m === "IN" ? "India" : "US"} portfolio{" "}
        <small className="text-sm font-normal text-muted-foreground">
          {data.simulated ? "paper account" : cfg.broker}{cfg.validated ? "" : " · not validated"} · <Link to={`/portfolio/${m}/tracking`} className="text-accent">compare with backtest →</Link>
        </small>
      </h1>
      <div className="mt-4"><Flash msg={data.msg} err={data.err} /></div>
      {state.kill_switch && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-tile border border-critical/40 px-4 py-3 text-[13px] text-down">
          <span><b>Kill switch active</b> since {state.kill_at ? String(state.kill_at).slice(0, 16).replace("T", " ") : "?"}: {state.kill_reason}. New entries are blocked; exits and stops still work.</span>
          <Button size="sm" variant="outline" loading={busy === "rearm"} onClick={() => act(`/portfolio/${m}/rearm`, "rearm")}>Re-arm</Button><Tip k="rearm" label="re-arming" />
        </div>
      )}
      <Ratios>
        <Ratio label={<>Equity<Tip k="equity" /></>}>{money(data.equity, m, false)}</Ratio>
        <Ratio label={<>Cash<Tip k="cash" /></>}>{money(data.cash, m, false)}</Ratio>
        <Ratio label={<>Budget<Tip k="budget" /></>}>{money(cfg.budget, m, false)}</Ratio>
        <Ratio label={<>Gross exposure<Tip k="gross_exposure" label="gross exposure" /></>}>{data.equity ? pct(data.gross / data.equity, 0) : "—"}</Ratio>
        <Ratio label={<>From peak<Tip k="from_peak" label="from peak" /></>} valueClass={tone(data.drawdown)}>{pct(data.drawdown, 1)}</Ratio>
        <Ratio label={<>Realised P&amp;L<Tip k="realised_pnl" label="realised P&L" /></>} valueClass={tone(data.realized)}>{money(data.realized, m, false)}</Ratio>
        <Ratio label={<>Kill switch<Tip k="kill_switch" label="the kill switch" /></>} valueClass={state.kill_switch ? "text-down" : "text-up"}>{state.kill_switch ? "TRIPPED" : "armed"}</Ratio>
        <Ratio label={<>Last processed<Tip k="last_processed" label="last processed" /></>}>{state.last_processed ?? "—"}</Ratio>
      </Ratios>
      <Card className="mt-4 p-4"><Equity code={m} /></Card>

      <H2>Open positions <small className="text-sm font-normal text-muted-foreground">{positions.length} / {cfg.max_open_positions}</small></H2>
      {positions.length ? (
        <TableWrap>
          <table className="w-full min-w-[900px] text-[13px]">
            <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Symbol</th><th>Side<Tip k="long_short" label="side" /></th><th>Entered</th><th className="text-right">Qty</th><th className="text-right">Entry</th><th className="text-right">Last</th><th className="text-right">Stop<Tip k="stop" label="the stop-loss" /></th><th className="text-right">Target<Tip k="target" /></th><th className="text-right">P&amp;L<Tip k="unrealised_pnl" label="unrealised P&L" /></th><th className="text-right">Return<Tip k="return" /></th></tr></thead>
            <tbody>{positions.map((p: any) => (
              <tr key={p.id} className={td}>
                <td><Link to={`/m/${m}/${encodeURIComponent(p.symbol)}`} className="font-semibold text-accent">{p.symbol}</Link><div className="text-xs text-muted-foreground">{p.sector}{p.strategy && <> · <Link to={`/setups/${m}`} className="text-accent">{setupLabel(p.strategy)}</Link></>}</div></td>
                <td>{p.direction}</td><td>{p.entry_date}</td>
                <td className="text-right tabular-nums">{p.quantity}</td><td className="text-right tabular-nums">{num(p.entry_price, 2)}</td><td className="text-right tabular-nums">{num(p.last_price, 2)}</td>
                <td className="text-right tabular-nums text-down">{num(p.stop_price, 2)}</td><td className="text-right tabular-nums">{num(p.target_price, 2)}</td>
                <td className={`text-right tabular-nums ${tone(p.unrealized)}`}>{money(p.unrealized, m, false)}</td><td className={`text-right tabular-nums ${tone(p.unrealized_pct)}`}>{pct(p.unrealized_pct, 1, true)}</td>
              </tr>
            ))}</tbody>
          </table>
        </TableWrap>
      ) : <p className="text-[13px] text-muted-foreground">No open positions.</p>}

      {outside && (
        <>
          <H2>Your other stocks at Zerodha<Tip k="outside_holdings" label="your other stocks" /> <small className="text-sm font-normal text-muted-foreground">{outside.rows.length} · read-only</small></H2>
          <p className="mt-0 flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
            Bought outside TradeMare. Shown with TradeMare's view of each stock; it never places orders or stops on these. Snapshot {localTime(outside.fetched_at, m)}.
            <Button size="sm" variant="secondary" loading={busy === "outside"} onClick={() => act(`/portfolio/${m}/outside/refresh`, "outside")}>↻ Refresh from Zerodha</Button>
          </p>
          <Ratios>
            <Ratio label="Invested">{money(outside.invested, m, false)}</Ratio>
            <Ratio label="Value now">{money(outside.value, m, false)}</Ratio>
            <Ratio label="Profit / loss" valueClass={tone(outside.pnl)}>{money(outside.pnl, m, false)}</Ratio>
            <Ratio label="Return" valueClass={tone(outside.return)}>{pct(outside.return, 1, true)}</Ratio>
          </Ratios>
          <div className="mt-3">
            <SortTable
              rows={outside.rows as any[]} rowKey={(r) => r.symbol} minWidth={960}
              cols={[
                { key: "s", head: "Stock", sort: (r) => r.symbol, cell: (r) => <Link to={`/m/${m}/${encodeURIComponent(r.symbol)}`} className="font-semibold text-accent">{r.symbol}</Link> },
                { key: "q", head: "Qty", num: true, cell: (r) => r.quantity },
                { key: "a", head: "Avg cost", num: true, sort: (r) => r.average_price, cell: (r) => money(r.average_price, m, false) },
                { key: "p", head: "Price", num: true, sort: (r) => r.price, cell: (r) => <>{money(r.price, m, false)}<div className="text-xs text-muted-foreground">{new Date(r.price_day).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</div></> },
                { key: "v", head: "Value", num: true, sort: (r) => r.value, cell: (r) => money(r.value, m, false) },
                { key: "pl", head: "P&L", num: true, sort: (r) => r.pnl, cell: (r) => <span className={tone(r.pnl)}>{money(r.pnl, m, false)}</span> },
                { key: "r", head: "Return", num: true, sort: (r) => r.return, cell: (r) => <span className={tone(r.return)}>{pct(r.return, 1, true)}</span> },
                { key: "rk", head: <>TradeMare rank<Tip k="outside_rank" label="TradeMare rank" /></>, num: true, sort: (r) => r.rank ?? 9999, cell: (r) => r.rank ? <>#{r.rank} <span className="text-xs text-muted-foreground">of {r.ranked_of}</span></> : <span className="text-xs text-muted-foreground">not ranked</span> },
                { key: "t", head: <>Trend<Tip k="trend_200d" label="the trend" /></>, cell: (r) => r.above_200d == null ? <span className="text-muted-foreground">—</span> : r.above_200d ? <span className="text-up">▲ uptrend</span> : <span className="text-down">▼ below 200-day</span> },
                { key: "n", head: "Next results", sort: (r) => r.next_results ?? "9999", cell: (r) => r.next_results ? <span className={r.results_soon ? "font-semibold text-warning" : ""}>{new Date(r.next_results).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })}</span> : <span className="text-muted-foreground">—</span> },
              ]}
            />
          </div>
        </>
      )}

      <H2>Open orders<Tip k="limit_order" label="order types" /></H2>
      {orders.length ? (
        <TableWrap>
          <table className="w-full min-w-[860px] text-[13px]">
            <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Symbol</th><th>Purpose<Tip k="order_purpose" label="order purpose" /></th><th>Side</th><th>Type</th><th className="text-right">Qty</th><th className="text-right">Limit<Tip k="limit_price" label="the limit price" /></th><th className="text-right">Trigger<Tip k="trigger" label="the trigger" /></th><th>Valid until<Tip k="valid_until" label="valid until" /></th><th>Broker id</th></tr></thead>
            <tbody>{orders.map((o: any) => (
              <tr key={o.id} className={td}><td>{o.symbol}</td><td>{o.purpose}</td><td>{o.side}</td><td>{o.order_type}</td><td className="text-right tabular-nums">{o.quantity}</td>
                <td className="text-right tabular-nums">{num(o.limit_price, 2)}</td><td className="text-right tabular-nums">{num(o.trigger_price, 2)}</td><td>{o.valid_until ?? "until cancelled"}</td><td className="text-xs text-muted-foreground">{o.broker_order_id}</td></tr>
            ))}</tbody>
          </table>
        </TableWrap>
      ) : <p className="text-[13px] text-muted-foreground">No open orders.</p>}
      {ended?.length > 0 && (
        <>
          <H2>Orders ended in the last 14 days</H2>
          <ul className="m-0 list-none space-y-2 p-0 text-[13px]">{ended.map((w: any, i: number) => (
            <li key={i} className="flex gap-2"><Badge size="sm" variant={STAGE[w.status] ?? "neutral"} className="h-fit">{w.status}</Badge><span>{w.text}</span></li>
          ))}</ul>
        </>
      )}

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="m-0 mb-3 text-base font-semibold">Closed trades<Tip k="realised_pnl" label="closed-trade P&L" /></h2>
          {closed.length ? (
            <table className="w-full text-[13px]"><thead className="text-left text-xs text-muted-foreground"><tr className="[&_th]:py-2"><th>Symbol</th><th>Exit</th><th>Reason</th><th className="text-right">P&amp;L</th></tr></thead>
              <tbody>{closed.map((p: any) => <tr key={p.id} className="border-t border-line [&_td]:py-2"><td>{p.symbol}</td><td>{p.exit_date}</td><td className="text-xs text-muted-foreground">{p.exit_reason}</td><td className={`text-right tabular-nums ${tone(p.realized_pnl)}`}>{money(p.realized_pnl, m, false)}</td></tr>)}</tbody></table>
          ) : <p className="m-0 text-[13px] text-muted-foreground">None yet.</p>}
        </Card>
        <Card className="p-5">
          <h2 className="m-0 mb-3 text-base font-semibold">Audit log<Tip k="audit_log" label="the audit log" /></h2>
          <ul className="m-0 max-h-96 list-none space-y-1.5 overflow-y-auto p-0 text-[13px]">
            {events.length ? events.map((e: any) => <li key={e.id}><span className="text-xs text-muted-foreground">{String(e.ts).slice(5, 16).replace("T", " ")}</span> {e.message}</li>) : <li className="text-muted-foreground">Empty</li>}
          </ul>
        </Card>
      </div>
    </>
  );
}
