/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { Empty, Flash, Tip } from "@/components/bits";
import { SortTable } from "@/components/SortTable";
import { Badge } from "@/components/wensity/badge";
import { Button } from "@/components/wensity/button";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { useToastManager } from "@/components/wensity/toast";
import { postForm } from "@/lib/actions";
import { useChrome } from "@/lib/chrome";
import { money, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic Setups page (templates/setups.html): pick setups and see today's matches, the
// track record per period, and the paper-trading switch. Same data via /setups/<m>?format=json.

const PassPill = ({ r, short = false }: { r: any; short?: boolean }) => r ? <Badge size="sm" variant={r.passes ? "success" : "danger"} title={r.verdict}>{r.passes ? (short ? "pass" : "passes") : (short ? "fail" : "fails")}</Badge> : null;
const stat = (p: any, k: string, digits = 2) => (p?.[k] == null ? "—" : pct(p[k], digits, true));

export function Setups() {
  const { code = "IN" } = useParams();
  const [params, setParams] = useSearchParams();
  const { data, error, reload } = usePage<Record<string, any>>(`/setups/${code}${params.toString() ? `?${params}` : ""}`);
  const { chrome } = useChrome();
  const toasts = useToastManager();
  const [picked, setPicked] = useState<string[] | null>(null);
  const [trading, setTrading] = useState<any>(null);
  const [busy, setBusy] = useState("");
  if (error) return <Empty>Could not load setups: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const m = code, records = data.records ?? {}, setups = data.setups ?? {}, groups = data.groups as [string, any[]][];
  const chosen: string[] = picked ?? data.chosen;
  const tr = trading ?? { ...data.trading, selected: [...data.trading.selected] };
  const toggle = (arr: string[], id: string) => (arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]);
  const run = async (path: string, fields: Record<string, any>, label: string) => {
    if (!chrome) return;
    setBusy(label);
    const r = await postForm(path, chrome.csrf_token, fields);
    setBusy("");
    toasts.add({ title: r.message, data: { variant: r.ok ? "success" : "error" } });
    setTrading(null);
    reload();
  };
  const indexName = m === "IN" ? "Nifty 500" : "S&P 500";
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Chart setups<Tip k="book_setup" label="a chart setup" /></h1>
      <p className="mt-1 text-[13px] text-muted-foreground">
        Setups from <i>51 Trading Strategies</i> (Aseem Singhal, 2023), written as exact daily rules and tested on {indexName} history since 2010.
        Pick one or more to see today's matches. The book shows hand-picked charts and no tested results, so the track record below is TradeMare's own.{" "}
        <a href="https://github.com/Mayank9mare/TradeMare/blob/main/docs/plans/2026-11-01-book-setups-plan.md" className="text-accent">Exact rules</a>
      </p>
      {!data.record ? <Card className="mt-4 p-5"><p className="m-0"><b>Working out each setup's track record…</b> This takes about a minute the first time. Refresh the page shortly.</p></Card>
        : !data.current && (
          <Card className="mt-4 p-4 text-[13px] text-muted-foreground">
            {data.computing ? `Updating the record for newer prices (about a minute); showing the one from ${data.record.data_through}. Refresh shortly.`
              : <span className="flex flex-wrap items-center gap-2">This record is from {data.record.data_through}. <Button size="sm" variant="secondary" loading={busy === "refresh"} onClick={() => run(`/setups/${m}/refresh`, {}, "refresh")}>Update now</Button></span>}
          </Card>
        )}

      <Card className="mt-4 p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="m-0 text-base font-semibold">Choose setups</h2>
          <div className="flex flex-wrap items-center gap-3 text-[13px]">
            <Button size="sm" onClick={() => { const p = new URLSearchParams(); chosen.forEach((s) => p.append("s", s)); setParams(p); setPicked(null); }}>Show matches</Button>
            <button type="button" className="text-accent" onClick={() => setPicked(Object.keys(setups).filter((id) => records[id]?.passes))}>Only passing</button>
            <button type="button" className="text-accent" onClick={() => setPicked(Object.keys(setups))}>All</button>
            <button type="button" className="text-accent" onClick={() => setPicked([])}>None</button>
          </div>
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-4">
          {groups.map(([family, items]) => (
            <fieldset key={family} className="m-0 rounded-tile border border-line p-3">
              <legend className="px-1 text-xs font-semibold text-muted-foreground uppercase">{family}</legend>
              {items.map((st) => (
                <label key={st.id} className="flex cursor-pointer items-start gap-2 py-1 text-[13px]" title={st.summary}>
                  <input type="checkbox" className="mt-1" checked={chosen.includes(st.id)} onChange={() => setPicked(toggle(chosen, st.id))} />
                  <span><b>{st.name}</b> <span className="text-xs text-muted-foreground">{st.book}</span> <PassPill r={records[st.id]} />{data.trading.enabled && data.trading.selected.includes(st.id) && <> <Badge size="sm" variant="info">trading</Badge></>}</span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      </Card>

      <Card className="mt-4 p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 className="m-0 text-base font-semibold">Today's matches{data.record && ` · ${data.record.data_through}`}</h2><span className="text-xs text-muted-foreground">{data.matches.length} stock{data.matches.length === 1 ? "" : "s"}</span></div>
        {!data.chosen.length ? <p className="m-0 text-[13px] text-muted-foreground">Choose at least one setup above.</p>
          : !data.matches.length ? <p className="m-0 text-[13px] text-muted-foreground">No liquid stock matched {data.chosen.length === 1 ? "this setup" : "these setups"} on the latest session.</p>
          : (<>
            <p className="mt-0 text-xs text-muted-foreground">The entry is a buy-stop<Tip k="buy_stop" label="a buy-stop" />: the trade starts only if the price rises above the signal day's high in the next session. Stocks matching several setups come first.</p>
            <SortTable<any>
              rows={data.matches} rowKey={(x) => x.symbol} minWidth={900}
              cols={[
                { key: "s", head: "Stock", sort: (x) => x.symbol, cell: (x) => <><Link to={`/m/${m}/${encodeURIComponent(x.symbol)}`} className="font-semibold text-accent">{x.symbol}</Link><div className="text-xs text-muted-foreground">{x.name?.length > 28 ? `${x.name.slice(0, 27)}…` : x.name}</div></> },
                { key: "setup", head: "Setup", sort: (x) => x.setups.length, cell: (x) => <span className="flex flex-wrap gap-1">{x.setups.map((sid: string) => <Badge key={sid} size="sm" variant="neutral" title={setups[sid]?.summary}>{setups[sid]?.name ?? sid}</Badge>)}</span> },
                { key: "close", head: "Close", num: true, sort: (x) => x.close, cell: (x) => money(x.close, m, false) },
                { key: "entry", head: "Entry above", num: true, sort: (x) => x.entry, cell: (x) => money(x.entry, m, false) },
                { key: "stop", head: <>Stop-loss<Tip k="stop" label="the stop-loss" /></>, num: true, sort: (x) => x.stop, cell: (x) => money(x.stop, m, false) },
                { key: "risk", head: "Risk", num: true, sort: (x) => x.risk_pct, cell: (x) => pct(x.risk_pct, 1) },
                { key: "target", head: <>Target<Tip k="target" /></>, num: true, sort: (x) => x.target ?? 0, cell: (x) => x.target ? money(x.target, m, false) : <span className="text-xs text-muted-foreground">trailing</span> },
                { key: "sector", head: "Sector", sort: (x) => x.sector ?? "", cell: (x) => <span className="text-xs text-muted-foreground">{x.sector}</span> },
              ]}
            />
          </>)}
      </Card>

      {data.record && (
        <Card className="mt-4 p-5">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 className="m-0 text-base font-semibold">Track record on {m === "IN" ? "Indian" : "US"} stocks</h2><span className="text-xs text-muted-foreground">data through {data.record.data_through}</span></div>
          <p className="mt-0 text-xs text-muted-foreground">
            Every past signal as one trade with the book's entry, stop and exit, after costs, one trade per stock at a time.
            <b> Nifty, same days</b> is what simply holding the index earned over each trade's days, and <b>Edge</b> is the setup's average minus that: a negative edge means an index fund did better.
            Results use today's index list (some survivorship bias<Tip k="survivorship" />) and 2020 onwards was a strong bull market, so treat both columns as optimistic.
            The evidence bar<Tip k="evidence_bar" label="the evidence bar" /> was fixed before any results.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-[13px]">
              <thead className="text-xs text-muted-foreground">
                <tr className="[&_th]:px-2.5 [&_th]:py-1.5"><th rowSpan={2} className="text-left">Setup</th>{data.periods.map((p: string) => <th key={p} colSpan={6} className="border-b border-line">{p}</th>)}<th rowSpan={2}>Bar</th></tr>
                <tr className="[&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-right">{data.periods.map((p: string) => (
                  <th key={p} colSpan={6} className="!p-0"><div className="grid grid-cols-6 [&>span]:px-2.5 [&>span]:py-1.5"><span>Trades</span><span>Won<Tip k="win_rate" /></span><span>Avg / trade</span><span>Nifty, same days</span><span>Edge<Tip k="setup_edge" label="edge" /></span><span>PF<Tip k="profit_factor" /></span></div></th>
                ))}</tr>
              </thead>
              <tbody>{Object.entries(setups).map(([sid, st]: [string, any]) => {
                const r = records[sid];
                return (
                  <tr key={sid} className={`border-t border-line align-top [&_td]:px-2.5 [&_td]:py-2 ${data.chosen.includes(sid) ? "bg-accent-soft" : ""}`}>
                    <td className="max-w-80"><b>{st.name}</b> <span className="text-xs text-muted-foreground">{st.book}</span><div className="text-xs text-muted-foreground">{st.summary} {st.exit_text}</div></td>
                    {data.periods.map((p: string) => {
                      const x = r?.periods?.[p];
                      if (!x) return <td key={p} colSpan={6} className="text-xs text-muted-foreground">—</td>;
                      const edge = x.avg_ret != null && x.avg_bench != null ? x.avg_ret - x.avg_bench : null;
                      return (
                        <td key={p} colSpan={6} className="!p-0"><div className="grid grid-cols-6 text-right tabular-nums [&>span]:px-2.5 [&>span]:py-2">
                          <span>{x.trades}</span><span>{x.win_rate != null ? pct(x.win_rate, 0) : "—"}</span><span className={tone(x.avg_ret)}>{stat(x, "avg_ret")}</span>
                          <span className="text-muted-foreground">{stat(x, "avg_bench")}</span><span className={tone(edge)}>{edge == null ? "—" : pct(edge, 2, true)}</span><span>{x.profit_factor ? x.profit_factor.toFixed(2) : "—"}</span>
                        </div></td>
                      );
                    })}
                    <td><PassPill r={r} short /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        </Card>
      )}

      <Card className="mt-4 p-5" id="trading">
        <h2 className="m-0 mb-2 text-base font-semibold">Paper trading with setups</h2>
        <Flash msg={data.msg} err={data.err} />
        <p className="mt-0 text-xs text-muted-foreground">
          When on, each daily run turns the ticked setups' matches into signals for your approval, like the main strategy's.
          They use their own share of the budget and position limit, so the tested strategy's trades are unaffected, and every usual check still applies
          (risk per trade, sector and cash limits, results-date and red-flag checks, kill switch). Entries are buy-stops at the signal day's high.
          {m === "IN" && " Real orders stay blocked while this market is not validated."}
        </p>
        <label className="my-3 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={tr.enabled} onChange={(e) => setTrading({ ...tr, enabled: e.target.checked })} /><b>Let setups propose paper trades on this market</b></label>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-4">
          {groups.map(([family, items]) => (
            <fieldset key={family} className="m-0 rounded-tile border border-line p-3">
              <legend className="px-1 text-xs font-semibold text-muted-foreground uppercase">{family}</legend>
              {items.map((st) => (
                <label key={st.id} className="flex items-center gap-2 py-1 text-[13px]"><input type="checkbox" checked={tr.selected.includes(st.id)} onChange={() => setTrading({ ...tr, selected: toggle(tr.selected, st.id) })} /><span>{st.name} <PassPill r={records[st.id]} /></span></label>
              ))}
            </fieldset>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-[13px]">
          <label className="flex items-center gap-2 text-muted-foreground">Share of budget % <input type="number" min={1} max={100} step={1} value={Math.round(tr.budget_pct)} onChange={(e) => setTrading({ ...tr, budget_pct: Number(e.target.value) })} className="w-20 rounded-tile border border-line bg-surface px-2 py-1" /></label>
          <label className="flex items-center gap-2 text-muted-foreground">Max open positions <input type="number" min={1} step={1} value={tr.max_positions} onChange={(e) => setTrading({ ...tr, max_positions: Number(e.target.value) })} className="w-20 rounded-tile border border-line bg-surface px-2 py-1" /></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={tr.require_record} onChange={(e) => setTrading({ ...tr, require_record: e.target.checked })} />Only setups that pass the evidence bar<Tip k="evidence_bar" label="the evidence bar" /></label>
        </div>
        <p className="text-xs text-muted-foreground">Unticking the evidence bar lets failing setups trade too: that is trading a strategy with no tested edge on this market.</p>
        <Button loading={busy === "save"} onClick={() => run(`/setups/${m}/trading`, { enabled: tr.enabled, selected: tr.selected, budget_pct: tr.budget_pct, max_positions: tr.max_positions, require_record: tr.require_record }, "save")}>Save</Button>
      </Card>
    </>
  );
}
