/* eslint-disable @typescript-eslint/no-explicit-any */
import { Link } from "react-router";

import { Empty, H2, Pill, TableWrap, Tip } from "@/components/bits";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { daysBetween, money, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic Trade journal (templates/journal.html), same data via /journal?format=json.

type E = Record<string, any>;
interface Data { entries: E[]; summary: any }

const th = "[&_th]:px-3.5 [&_th]:py-2.5 [&_th]:font-medium";
const td = "border-t border-line [&_td]:px-3.5 [&_td]:py-2.5 [&_td]:align-top hover:bg-row-hover";
const sub = "text-xs text-muted-foreground";

function Stock({ s }: { s: any }) {
  return <><Link to={`/m/${s.market}/${encodeURIComponent(s.symbol)}`} className="font-semibold text-accent">{s.symbol}</Link> <Pill>{s.market}</Pill></>;
}

function Ratio({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return <div className="rounded-tile bg-surface px-4 py-3 shadow-card"><div className="text-xs text-muted-foreground">{label}</div><div className="font-num text-lg font-semibold tabular-nums">{children}</div></div>;
}

const rText = (r: number | null | undefined) => (r == null ? "—" : `${r >= 0 ? "+" : ""}${r.toFixed(1)}`);

export function Journal() {
  const { data, error } = usePage<Data>("/journal");
  if (error) return <Empty>Could not load the journal: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const by = (stage: string) => data.entries.filter((e) => e.stage === stage);
  const open = by("open"), waiting = by("awaiting fill"), unfilled = by("not filled"), closed = by("closed");
  const passed = data.entries.filter((e) => e.hypothetical);
  const sm = data.summary ?? {};
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Trade journal</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">Your trades from signal to outcome. Prices are the latest cached close; profit and loss is after brokerage, taxes and fees paid so far.</p>

      <H2 count={open.length}>Open trades</H2>
      {sm.open_totals && Object.keys(sm.open_totals).length > 0 && (
        <div className="mb-3 grid grid-cols-[repeat(auto-fit,minmax(min(100%,190px),1fr))] gap-3">
          {Object.entries(sm.open_totals).map(([mk, t]: [string, any]) => (
            <div key={mk} className="contents">
              <Ratio label={<>Invested {mk}<Tip k="invested" /></>}>{money(t.invested, mk, false)}</Ratio>
              <Ratio label={<>Current value {mk}<Tip k="current_value" label="current value" /></>}>{money(t.value, mk, false)}</Ratio>
              <Ratio label={<>Unrealised P&amp;L {mk}<Tip k="unrealised_pnl" label="unrealised P&L" /></>}><span className={tone(t.pnl)}>{money(t.pnl, mk, false)} <small>({pct(t.return, 2, true)})</small></span></Ratio>
            </div>
          ))}
        </div>
      )}
      {open.length ? (
        <TableWrap>
          <table className="w-full min-w-[980px] text-[13px]">
            <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr>
              <th>Stock</th><th>Entered<Tip k="long_term" label="long-term holding" /></th><th className="text-right">Qty</th><th className="text-right">Bought at</th><th className="text-right">Invested<Tip k="invested" /></th>
              <th className="text-right">Price now</th><th className="text-right">Value now<Tip k="current_value" label="value now" /></th><th className="text-right">P&amp;L<Tip k="pnl" label="P&L" /></th><th className="text-right">Return<Tip k="return" /></th><th className="text-right">Stop<Tip k="stop_away" label="distance to stop" /></th>
            </tr></thead>
            <tbody>{open.map((e, i) => {
              const s = e.signal, m = s.market;
              const held = e.price_date ? daysBetween(e.entry_date, e.price_date) : null;
              const lt = e.long_term_on && e.price_date ? daysBetween(e.price_date, e.long_term_on) : null;
              return (
                <tr key={i} className={td}>
                  <td><Stock s={s} /><div className={sub}>{String(s.direction).toLowerCase()}</div></td>
                  <td>{e.entry_date}<div className={sub}>{held ?? "?"} days{lt !== null && (lt > 0 ? ` · long-term in ${lt}d` : " · long-term")}</div></td>
                  <td className="text-right tabular-nums">{e.quantity}</td>
                  <td className="text-right tabular-nums">{money(e.entry_price, m, false)}</td>
                  <td className="text-right tabular-nums">{money(e.invested, m, false)}</td>
                  <td className="text-right tabular-nums">{money(e.last_price, m, false)}<div className={sub}>{e.price_date ?? ""}</div></td>
                  <td className="text-right tabular-nums">{money(e.current_value, m, false)}</td>
                  <td className={`text-right font-semibold tabular-nums ${tone(e.pnl)}`}>{money(e.pnl, m, false)}</td>
                  <td className={`text-right tabular-nums ${tone(e.return_pct)}`}>{pct(e.return_pct, 2, true)}</td>
                  <td className="text-right tabular-nums">{e.stop ? money(e.stop, m, false) : "—"}{e.stop && e.last_price ? <div className={sub}>{pct(e.stop / e.last_price - 1, 1, true)} away</div> : null}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </TableWrap>
      ) : <Empty>No open trades. Approved orders appear here once they fill.</Empty>}

      {waiting.length > 0 && (
        <>
          <H2 count={waiting.length}>Approved, waiting to fill<Tip k="awaiting_fill" label="waiting to fill" /></H2>
          <TableWrap>
            <table className="w-full min-w-[860px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Stock</th><th>Approved</th><th className="text-right">Qty</th><th className="text-right">Limit<Tip k="entry_limit" label="the limit price" /></th><th className="text-right">Order value</th><th className="text-right">Price now</th><th>What happens next</th></tr></thead>
              <tbody>{waiting.map((e, i) => {
                const s = e.signal, m = s.market;
                return (
                  <tr key={i} className={td}>
                    <td><Stock s={s} /></td><td>{s.decided_at ? String(s.decided_at).slice(0, 10) : "—"}</td>
                    <td className="text-right tabular-nums">{e.quantity}</td><td className="text-right tabular-nums">{money(s.entry_price, m, false)}</td>
                    <td className="text-right tabular-nums">{e.quantity ? money(s.entry_price * e.quantity, m, false) : "—"}</td>
                    <td className="text-right tabular-nums">{e.last_price ? money(e.last_price, m, false) : "—"}{e.last_price ? <div className={sub}>{pct(e.last_price / s.entry_price - 1, 1, true)} vs limit · {e.price_date}</div> : null}</td>
                    <td>{e.outcome}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </TableWrap>
        </>
      )}

      {unfilled.length > 0 && (
        <>
          <H2 count={unfilled.length}>Approved, but did not fill</H2>
          <p className="mt-0 text-[13px] text-muted-foreground">The order ended without buying anything; no money was spent.</p>
          <ul className="m-0 list-none space-y-1.5 p-0 text-[13px]">{unfilled.map((e, i) => (
            <li key={i} className="rounded-tile border-l-4 border-l-line-strong bg-surface px-3 py-2"><Stock s={e.signal} /> {e.outcome}</li>
          ))}</ul>
        </>
      )}

      <H2 count={closed.length}>Closed trades</H2>
      {closed.length ? (
        <>
          <div className="mb-3 grid grid-cols-[repeat(auto-fit,minmax(min(100%,190px),1fr))] gap-3">
            <Ratio label={<>Win rate<Tip k="win_rate" label="win rate" /></>}>{sm.win_rate != null ? pct(sm.win_rate, 0) : "—"}</Ratio>
            {Object.entries(sm.realised ?? {}).map(([mk, v]: [string, any]) => <Ratio key={mk} label={<>Realised P&amp;L {mk}<Tip k="realised_pnl" label="realised P&L" /></>}><span className={tone(v)}>{money(v, mk, false)}</span></Ratio>)}
          </div>
          <TableWrap>
            <table className="w-full min-w-[900px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Stock</th><th>Held</th><th className="text-right">Qty</th><th className="text-right">Bought at</th><th className="text-right">Sold at</th><th className="text-right">P&amp;L<Tip k="pnl" label="P&L" /></th><th className="text-right">Return</th><th className="text-right">R<Tip k="r_multiple" label="R multiple" /></th><th>Why it closed</th></tr></thead>
              <tbody>{closed.map((e, i) => {
                const s = e.signal, m = s.market;
                return (
                  <tr key={i} className={td}>
                    <td><Stock s={s} /></td>
                    <td>{e.entry_date} → {e.exit_date}<div className={sub}>{daysBetween(e.entry_date, e.exit_date)} days</div></td>
                    <td className="text-right tabular-nums">{e.quantity}</td>
                    <td className="text-right tabular-nums">{money(e.entry_price, m, false)}</td>
                    <td className="text-right tabular-nums">{money(e.exit_price, m, false)}</td>
                    <td className={`text-right font-semibold tabular-nums ${tone(e.pnl)}`}>{money(e.pnl, m, false)}</td>
                    <td className={`text-right tabular-nums ${tone(e.return_pct)}`}>{pct(e.return_pct, 2, true)}</td>
                    <td className={`text-right tabular-nums ${e.r_multiple != null ? tone(Math.round(e.r_multiple * 10) / 10) : ""}`}>{rText(e.r_multiple)}</td>
                    <td>{e.exit_reason ?? ""}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </TableWrap>
        </>
      ) : <Empty>No closed trades yet.</Empty>}

      <H2 count={passed.length}>Signals you passed on<Tip k="passed_on" label="signals you passed on" /></H2>
      {passed.length ? (
        <>
          <p className="mt-0 text-[13px] text-muted-foreground">What each would have returned if taken at the signal price, up to the latest close (ignoring the stop).{sm.skipped_avg_return != null && <> Average: <b className={tone(sm.skipped_avg_return)}>{pct(sm.skipped_avg_return, 1, true)}</b>.</>}</p>
          <TableWrap>
            <table className="w-full min-w-[820px] text-[13px]">
              <thead className={`text-left text-xs text-muted-foreground ${th}`}><tr><th>Stock</th><th>Signal date</th><th>Decision</th><th className="text-right">Signal price</th><th className="text-right">Price now</th><th className="text-right">Would have returned</th><th className="text-right">R<Tip k="r_multiple" label="R multiple" /></th></tr></thead>
              <tbody>{passed.map((e, i) => {
                const s = e.signal, m = s.market;
                return (
                  <tr key={i} className={`${td} text-muted-foreground`}>
                    <td><Stock s={s} /></td><td>{s.scan_date}</td>
                    <td>{e.stage}{s.note && <div className={sub}>“{s.note}”</div>}</td>
                    <td className="text-right tabular-nums">{money(s.entry_price, m, false)}</td>
                    <td className="text-right tabular-nums">{e.last_price ? money(e.last_price, m, false) : "—"}</td>
                    <td className={`text-right tabular-nums ${tone(e.return_pct)}`}>{e.return_pct != null ? pct(e.return_pct, 1, true) : "—"}</td>
                    <td className={`text-right tabular-nums ${e.r_multiple != null ? tone(Math.round(e.r_multiple * 10) / 10) : ""}`}>{rText(e.r_multiple)}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </TableWrap>
        </>
      ) : <Empty>You haven't rejected or let any signal expire yet.</Empty>}
    </>
  );
}
