/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Empty, Flash, H2, Pill, ScoreBar, Spark, TableWrap, Tip } from "@/components/bits";
import { Badge } from "@/components/wensity/badge";
import { Button } from "@/components/wensity/button";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { useToastManager } from "@/components/wensity/toast";
import { postForm } from "@/lib/actions";
import { useChrome } from "@/lib/chrome";
import { localTime, metric, money, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic Signals page (templates/signals.html + _explain.html), same data via /signals?format=json.

type Sig = Record<string, any>;
interface Data {
  pending: Sig[]; recent: Sig[]; msg: string | null; err: string | null; config: any;
  charts: Record<string, any>; tech_metrics: Record<string, [string, string]>; fund_metrics: Record<string, [string, string]>;
  piotroski_labels: Record<string, string>; asks: Record<string, any[]>; llm_missing: string | null;
  kite_key: string | null; kite_basket_url: string;
}

const TECH_HELP: Record<string, string> = {
  trend: "price vs its 50- and 200-day averages, and the 200-day slope",
  momentum: "6-month return vs the index and 3-month return",
  setup: "breakout, pullback or overextension right now",
  volume: "buying vs selling volume over 20 days",
};
const FUND_HELP: Record<string, string> = {
  quality: "ROE, ROA and margins vs sector peers",
  health: "debt, liquidity and interest cover vs peers",
  growth: "revenue and earnings growth vs peers",
  value: "earnings, cash-flow and book yield vs peers",
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function actionWord(s: Sig) {
  if (s.action === "ENTRY") return s.direction === "LONG" ? "Buy" : "Short";
  return s.direction === "LONG" ? "Sell" : "Cover";
}

function setupLabel(strategy: string | null | undefined) {
  if (!strategy) return "";
  return strategy.startsWith("setup:") ? strategy.slice(6).replace(/_/g, " ").toUpperCase() : strategy;
}

function Dl({ children }: { children: React.ReactNode }) {
  return <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 text-[13px] [&_dd]:m-0 [&_dd]:text-right [&_dd]:tabular-nums [&_dt]:text-muted-foreground">{children}</dl>;
}

function FactorTable({ items, help }: { items: Record<string, number | null>; help: Record<string, string> }) {
  return (
    <table className="mb-2 w-full text-[13px]"><tbody>
      {Object.entries(items).map(([k, v]) => (
        <tr key={k} className="border-t border-line first:border-t-0">
          <td className="py-1.5"><b>{cap(k)}</b><Tip k={k} /><div className="text-xs text-muted-foreground">{help[k] ?? ""}</div></td>
          <td className="py-1.5 text-right">{v === null || v === undefined ? <span className="text-faint">no data</span> : <ScoreBar value={v} />}</td>
        </tr>
      ))}
    </tbody></table>
  );
}

function Explain({ s, data }: { s: Sig; data: Data }) {
  const m = s.market;
  const ev = s.evidence ?? {};
  const t = ev.technical, f = ev.fundamental, r = ev.risk, e = ev.events, fx = f?.forensic;
  const Section = ({ title, score, children }: { title: string; score?: number | null; children: React.ReactNode }) => (
    <section className="min-w-0 rounded-tile bg-surface-2/60 p-4">
      <div className="mb-2 flex items-center justify-between gap-2"><h4 className="m-0 text-sm font-semibold">{title}</h4>{score !== undefined && score !== null && <ScoreBar value={score} />}</div>
      {children}
    </section>
  );
  return (
    <details className="mt-3 rounded-tile border border-line">
      <summary className="cursor-pointer px-4 py-2.5 text-[13px] font-semibold">
        Explain this signal<Tip k="explain" label="this panel" />{" "}
        <span className="font-normal text-muted-foreground">technical · fundamental · filings · risk{ev.asof ? ` · as of ${ev.asof}` : ""}</span>
      </summary>
      {!s.evidence ? <div className="p-4"><Empty>Evidence was not recorded for this signal.</Empty></div> : (
        <div className="grid gap-3 p-3 md:grid-cols-2">
          <Section title="Technical" score={t?.score}>
            {t ? (<>
              {t.components && <FactorTable items={t.components} help={TECH_HELP} />}
              {t.reasons?.length > 0 && <ul className="mb-2 list-disc pl-5 text-[13px]">{t.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>}
              <Dl>{Object.entries(data.tech_metrics).filter(([k]) => t.metrics?.[k] !== null && t.metrics?.[k] !== undefined).map(([k, spec]) => (
                <div key={k} className="contents"><dt>{spec[0]}<Tip k={k} label={spec[0]} /></dt><dd className={spec[1] === "pct" && k !== "atr_pct" ? tone(t.metrics[k]) : ""}>{metric(t.metrics[k], spec[1], m)}</dd></div>
              ))}</Dl>
            </>) : <Empty>No technical data on the scan date.</Empty>}
          </Section>
          <Section title="Fundamental" score={f?.score}>
            {f ? (<>
              {f.factors && <FactorTable items={f.factors} help={FUND_HELP} />}
              {f.reasons?.length > 0 && <ul className="mb-2 list-disc pl-5 text-[13px]">{f.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>}
              <Dl>{Object.entries(data.fund_metrics).filter(([k]) => f.metrics?.[k] !== null && f.metrics?.[k] !== undefined).map(([k, spec]) => (
                <div key={k} className="contents"><dt>{spec[0]}<Tip k={k} label={spec[0]} /></dt><dd className={["earnings_growth", "revenue_growth"].includes(k) ? tone(f.metrics[k]) : ""}>{metric(f.metrics[k], spec[1], m)}</dd></div>
              ))}</Dl>
              <p className="mt-2 mb-0 text-xs text-muted-foreground">Source: {f.source} · scores rank the company against its sector peers (−1 worst, +1 best).</p>
              {fx && (
                <div className="mt-3">
                  <h4 className="m-0 mb-1.5 text-sm font-semibold">Forensic checks <small className="text-faint">FY {fx.fiscal_year ? String(fx.fiscal_year).slice(0, 4) : "?"}</small></h4>
                  <Dl>
                    {fx.piotroski != null && <div className="contents"><dt>Piotroski F-score<Tip k="piotroski" label="the Piotroski F-score" /></dt><dd className={fx.piotroski >= 0.75 * fx.piotroski_tests ? "text-up" : fx.piotroski <= 0.35 * fx.piotroski_tests ? "text-down" : ""}>{fx.piotroski} / {fx.piotroski_tests}</dd></div>}
                    {fx.altman_z != null && <div className="contents"><dt>Altman Z''<Tip k="altman" label="the Altman Z-score" /></dt><dd className={fx.altman_zone === "distress" ? "text-down" : fx.altman_zone === "safe" ? "text-up" : ""}>{fx.altman_z.toFixed(2)} <span className="text-faint">({fx.altman_zone})</span></dd></div>}
                    {fx.beneish_m != null && <div className="contents"><dt>Beneish M<Tip k="beneish" label="the Beneish M-score" /></dt><dd className={fx.beneish_flag ? "text-down" : "text-up"}>{fx.beneish_m.toFixed(2)} <span className="text-faint">({fx.beneish_flag ? "flag" : "no flag"})</span></dd></div>}
                    {fx.accruals != null && <div className="contents"><dt>Accruals<Tip k="accruals" /></dt><dd className={fx.accruals > 0.1 ? "text-down" : ""}>{pct(fx.accruals, 1, true)}</dd></div>}
                  </Dl>
                  {fx.piotroski_detail && <ul className="mt-2 mb-0 grid list-none grid-cols-1 gap-0.5 p-0 text-xs sm:grid-cols-2">{Object.entries(fx.piotroski_detail).map(([k, ok]) => <li key={k} className={ok ? "text-up" : "text-down"}>{ok ? "✓" : "✗"} {data.piotroski_labels[k] ?? k}</li>)}</ul>}
                  {(fx.notes ?? []).map((n: string) => <p key={n} className="mt-1 mb-0 text-xs text-muted-foreground">{n}</p>)}
                </div>
              )}
            </>) : <Empty>No fundamental data for this company yet.</Empty>}
          </Section>
          <Section title="Filings and results">
            {e ? (<>
              <Dl>
                <dt>Next results<Tip k="next_results" label="next results" /></dt>
                <dd className={e.results_soon ? "text-down" : ""}>{e.next_results ? <>{e.next_results}{e.sessions_to_results != null && <span className="text-faint"> ({e.sessions_to_results} session{e.sessions_to_results !== 1 ? "s" : ""} after scan · {e.results_source})</span>}</> : <span className="text-faint">not announced</span>}</dd>
                <dt>Red flags / cautions<Tip k="red_flags" label="red flags" /></dt><dd className={e.red ? "text-down" : ""}>{e.red} / {e.amber}</dd>
              </Dl>
              {e.flags?.length ? (
                <ul className="mt-2 list-none space-y-1.5 p-0 text-[13px]">{e.flags.map((fl: any, i: number) => (
                  <li key={i} className={`rounded-tile border-l-4 bg-surface px-3 py-2 ${fl.level === "red" ? "border-l-critical" : "border-l-warning"}`}>
                    <b>{fl.title}</b>{fl.count > 1 && ` ×${fl.count}`} · {fl.day} · {fl.source}{fl.url && <> · <a href={fl.url} target="_blank" rel="noopener" className="text-accent">filing</a></>}
                    <div className="text-xs text-muted-foreground">{fl.detail}</div>
                  </li>))}</ul>
              ) : <p className="mt-2 mb-0 text-xs text-muted-foreground">No red flags in official filings{!e.coverage && " (red: last 180 days, cautions: last 90)"}.</p>}
              {e.coverage && <p className="mt-2 mb-0 text-xs text-warning">{e.coverage}</p>}
              {e.recent?.length > 0 && (
                <details className="mt-2 text-[13px]"><summary className="cursor-pointer text-accent">{e.recent[0].category === "News headline" ? "Latest news headlines" : "Latest filings"}</summary>
                  <ul className="mt-1 list-disc pl-5">{e.recent.map((a: any, i: number) => <li key={i}>{a.day} · {a.category}{a.url && <> · <a href={a.url} target="_blank" rel="noopener" className="text-accent">open</a></>}<div className="text-xs text-muted-foreground">{a.text}</div></li>)}</ul>
                </details>
              )}
            </>) : <Empty>Filings were not checked for this signal.</Empty>}
          </Section>
          <Section title="Price, 6 months">
            {data.charts[s.id] ? <Spark line={data.charts[s.id]} label={s.symbol} /> : <Empty>No price history cached.</Empty>}
            {r && (
              <div className="mt-4">
                <h4 className="m-0 mb-1.5 text-sm font-semibold">Risk and sizing</h4>
                <Dl>
                  <dt>Position value<Tip k="position_value" label="position value" /></dt><dd>{money(r.position_value, m, false)}{r.position_pct != null && <span className="text-faint"> ({pct(r.position_pct, 1)} of capital)</span>}</dd>
                  {r.risk_per_share !== undefined && <>
                    <dt>Stop distance<Tip k="stop_distance" label="stop distance" /></dt><dd>{money(r.risk_per_share, m, false)} <span className="text-faint">({pct(r.stop_distance, 1)}, {r.stop_method} stop)</span></dd>
                    <dt>Loss if stopped<Tip k="loss_if_stopped" label="loss if stopped" /></dt><dd className="text-down">{money(r.risk_amount, m, false)} <span className="text-faint">({pct(r.risk_pct, 2)} of capital; limit {r.risk_per_trade_pct}%)</span></dd>
                  </>}
                  {r.reward_risk !== undefined && <><dt>Reward : risk<Tip k="reward_risk" label="reward to risk" /></dt><dd>{Number(r.reward_risk).toFixed(1)} : 1</dd></>}
                </Dl>
              </div>
            )}
          </Section>
        </div>
      )}
    </details>
  );
}

function Ask({ s, data, onDone }: { s: Sig; data: Data; onDone: () => void }) {
  const { chrome } = useChrome();
  const toasts = useToastManager();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const qa = data.asks[s.id] ?? [];
  return (
    <details className="mt-3 rounded-tile border border-line" open={qa.length > 0}>
      <summary className="cursor-pointer px-4 py-2.5 text-[13px] font-semibold">Ask Claude about this pick<Tip k="ask_claude" label="Ask Claude" />{qa.length > 0 && <span className="font-normal text-faint"> ({qa.length})</span>}</summary>
      <div className="space-y-3 px-4 pb-4">
        {qa.map((a, i) => (
          <div key={i} className="rounded-tile bg-surface-2/60 p-3 text-[13px]">
            <p className="m-0 font-semibold">{a.question}</p>
            <p className="mt-1 mb-0 whitespace-pre-line">{a.answer}</p>
            {a.risks?.length > 0 && <ul className="mt-1 mb-0 list-disc pl-5">{a.risks.map((x: string) => <li key={x}>{x}</li>)}</ul>}
            <p className="mt-1 mb-0 text-xs text-muted-foreground">{String(a.ts).slice(0, 16).replace("T", " ")} UTC · {a.source === "claude-code" ? "review from your Claude Code session (evidence above plus news it searched)" : `$${(a.cost_usd ?? 0).toFixed(3)} · from the evidence above only`}</p>
          </div>
        ))}
        {data.llm_missing ? <p className="m-0 text-xs text-muted-foreground">To use this, {data.llm_missing}.</p> : (
          <form className="flex flex-wrap items-center gap-2" onSubmit={async (ev) => {
            ev.preventDefault();
            if (!chrome) return;
            setBusy(true);
            const r = await postForm(`/signals/${s.id}/ask`, chrome.csrf_token, { question: q });
            setBusy(false);
            toasts.add({ title: r.ok ? "Answer saved on this card" : "Could not ask", description: r.ok ? undefined : r.message, data: { variant: r.ok ? "success" : "error" } });
            if (r.ok) { setQ(""); onDone(); }
          }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="Why this pick, and what could go wrong?" aria-label="Your question" className="min-w-0 flex-1 rounded-tile border border-line bg-surface px-3 py-2 text-[13px]" />
            <Button type="submit" size="sm" loading={busy}>Ask</Button>
            <span className="w-full text-xs text-muted-foreground">Takes ~10–30 s. Uses only the evidence on this card; counts toward llm.max_daily_cost_usd.</span>
          </form>
        )}
      </div>
    </details>
  );
}

function Decide({ s, onDone }: { s: Sig; onDone: () => void }) {
  const { chrome, refresh } = useChrome();
  const toasts = useToastManager();
  const [qty, setQty] = useState(String(s.quantity));
  const [limit, setLimit] = useState(Number(s.entry_price).toFixed(2));
  const [stop, setStop] = useState(s.stop_price != null ? Number(s.stop_price).toFixed(2) : "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"" | "approve" | "reject">("");
  const entry = s.action === "ENTRY";
  const act = async (kind: "approve" | "reject") => {
    if (!chrome) return;
    setBusy(kind);
    const fields = kind === "approve" ? { quantity: qty, ...(entry ? { limit, stop } : {}) } : { quantity: qty, note };
    const r = await postForm(`/signals/${s.id}/${kind}`, chrome.csrf_token, fields);
    setBusy("");
    toasts.add({ title: r.message, data: { variant: r.ok ? "success" : "error" } });
    refresh();
    onDone();
  };
  const field = "w-32 rounded-tile border border-line bg-surface px-2.5 py-1.5 text-[13px] tabular-nums";
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2.5 border-t border-line pt-4 text-[13px]">
      <label className="flex items-center gap-1.5 text-muted-foreground">Qty<Tip k="quantity" /> <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} className={field} /></label>
      {entry ? (<>
        <label className="flex items-center gap-1.5 text-muted-foreground">{s.strategy ? <>Buy above<Tip k="buy_stop" label="a buy-stop" /></> : <>Limit<Tip k="entry_limit" label="the limit price" /></>} <input type="number" step="any" value={limit} onChange={(e) => setLimit(e.target.value)} className={field} /></label>
        <label className="flex items-center gap-1.5 text-muted-foreground">Stop<Tip k="stop" label="the stop-loss" /> <input type="number" step="any" value={stop} onChange={(e) => setStop(e.target.value)} className={field} /></label>
      </>) : <span className="text-xs text-muted-foreground">sells at the next open, at market</span>}
      <Button size="sm" loading={busy === "approve"} disabled={!!busy} onClick={() => act("approve")}>Approve</Button>
      <Button size="sm" variant="outline" loading={busy === "reject"} disabled={!!busy} onClick={() => act("reject")}>Reject</Button>
      <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} placeholder="If rejecting: why? (optional, shown in your journal)" aria-label="Reason for rejecting" className="min-w-0 flex-1 basis-60 rounded-tile border border-line bg-surface px-2.5 py-1.5 text-[13px]" />
    </div>
  );
}

function Kite({ s, data }: { s: Sig; data: Data }) {
  if (!s.kite_basket || !data.kite_key) return null;
  const ko = s.kite_basket[0];
  const m = s.market;
  return (
    <form method="post" action={data.kite_basket_url} target="_blank" rel="noopener" className="mt-3 flex flex-wrap items-center gap-3">
      <input type="hidden" name="api_key" value={data.kite_key} />
      <input type="hidden" name="data" value={JSON.stringify(s.kite_basket)} />
      <button type="submit" className="rounded-tile bg-[#387ed1] px-3.5 py-2 text-[13px] font-semibold text-white hover:opacity-90">Buy in Kite (real money)</button>
      <span className="flex-1 basis-80 text-xs text-muted-foreground">
        Opens Kite with {ko.quantity} {s.symbol} {ko.order_type === "SL" ? `to buy if it trades above ${money(ko.trigger_price, m, false)} (limit ${money(ko.price, m, false)})` : `at up to ${money(ko.price, m, false)}`}, delivery{ko.variety === "amo" && ", as an after-market order for the next session"}.
        Nothing is placed until you confirm in Kite. Once it fills, set a GTT stop-loss at <b className="text-down">{money(s.stop_price, m, false)}</b> in Kite (Holdings → the stock → Create GTT). Approve above as well if you want the paper account to track it.
      </span>
    </form>
  );
}

function SignalCard({ s, data, shortsOn, onDone }: { s: Sig; data: Data; shortsOn: boolean; onDone: () => void }) {
  const m = s.market;
  const buyish = (s.action === "ENTRY") === (s.direction === "LONG");
  const rs = s.split ?? { pro: [], con: [], neutral: [], summary: "" };
  const warns: string[] = [...(s.reasons?.events ?? []), ...(s.reasons?.tax ?? [])];
  const risk = s.action === "ENTRY" && s.stop_price != null ? s.quantity * Math.abs(s.entry_price - s.stop_price) : null;
  return (
    <Card id={`sig-${s.id}`} className={`p-5 ${s.is_expired ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2 text-lg">
            <Pill>{m}</Pill>
            <span className={`font-bold ${buyish ? "text-up" : "text-down"}`}>{actionWord(s)}</span>
            <Link to={`/m/${m}/${encodeURIComponent(s.symbol)}`} className="font-bold text-accent">{s.symbol}</Link>
            {(shortsOn || s.direction !== "LONG") && <Badge size="sm" variant="outline">{s.direction}</Badge>}
            <Tip k="long_short" label="long and short" />{s.action === "EXIT" && <Tip k="exit_signal" label="an exit signal" />}
            {s.strategy && <Link to={`/setups/${m}`} title="A chart setup from 51 Trading Strategies, traded in its own share of the budget"><Badge size="sm" variant="info">{setupLabel(s.strategy)}</Badge></Link>}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {s.is_expired ? <b className="text-down">expired</b> : <>decide by <b className="text-text">{localTime(s.expires_at, m)}</b></>}<Tip k="expires" label="the deadline" /> · scanned {s.scan_date}
          </div>
        </div>
        <div className="text-right text-xs text-muted-foreground">{s.strategy ? <>book setup<Tip k="book_setup" label="a book setup" /></> : <><ScoreBar value={s.score} /><Tip k="score" /></>}</div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {s.action === "ENTRY" ? (<>
          <Level label="Shares" value={String(s.quantity)} sub={s.strategy ? <>only if it trades above {money(s.entry_price, m, false)}<Tip k="buy_stop" label="a buy-stop" /></> : <>at most {money(s.entry_price, m, false)} each<Tip k="entry_limit" label="the limit price" /></>} />
          <Level label="Cost" value={money(s.quantity * s.entry_price, m, false)} sub={s.evidence?.risk?.position_pct != null ? `${pct(s.evidence.risk.position_pct, 1)} of capital` : ""} />
          <Level label={<>Stop-loss<Tip k="stop" label="the stop-loss" /></>} value={money(s.stop_price, m, false)} valueClass="text-down" sub={s.stop_price != null ? `${pct(s.stop_price / s.entry_price - 1, 1, true)} from entry` : ""} />
          <Level label={<>Most you can lose<Tip k="risk" /></>} value={money(risk, m, false)} valueClass="text-down" sub="if the stop is hit" />
          {s.target_price != null && <Level label="Target" value={money(s.target_price, m, false)} valueClass="text-up" sub={`${pct(s.target_price / s.entry_price - 1, 1, true)} from entry`} />}
        </>) : (<>
          <Level label="Shares" value={String(s.quantity)} sub="sold at the next open" />
          <Level label="Last price" value={money(s.entry_price, m, false)} sub={`value ${money(s.quantity * s.entry_price, m, false)}`} />
          <Level label="Why sell" value={s.exit_reason ?? "signal"} />
        </>)}
      </div>

      {warns.length > 0 && (
        <>
          <p className="mt-3 mb-1 text-xs text-muted-foreground">Warnings<Tip k="event_alerts" label="these warnings" /></p>
          <ul className="m-0 list-none space-y-1 p-0 text-[13px]">{warns.map((w) => (
            <li key={w} className={`rounded-tile border-l-4 bg-surface-2/60 px-3 py-1.5 ${w.startsWith("RED FLAG") ? "border-l-critical" : /^(Note|Tax)/.test(w) ? "border-l-line-strong" : "border-l-warning"}`}>{w}</li>
          ))}</ul>
        </>
      )}
      {s.reasons?.setup?.length > 0 && <div className="mt-3 text-[13px]"><b>Book setup:</b><ul className="mt-1 mb-0 list-disc pl-5">{s.reasons.setup.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
      {rs.summary && <p className="mt-3 mb-0 text-[13px]"><b>Why:</b> {rs.summary}</p>}
      {(rs.pro.length > 0 || rs.con.length > 0 || rs.neutral.length > 0) && (
        <div className="mt-3 grid gap-3 text-[13px] sm:grid-cols-3">
          {rs.pro.length > 0 && <ReasonList title="For" items={rs.pro} tone="text-up" />}
          {rs.con.length > 0 && <ReasonList title="Against" items={rs.con} tone="text-down" />}
          {rs.neutral.length > 0 && <ReasonList title="Also noted" items={rs.neutral} tone="text-muted-foreground" />}
        </div>
      )}
      <Explain s={s} data={data} />
      <Ask s={s} data={data} onDone={onDone} />
      {!s.is_expired && <><Decide s={s} onDone={onDone} /><Kite s={s} data={data} /></>}
    </Card>
  );
}

function Level({ label, value, sub, valueClass = "" }: { label: React.ReactNode; value: string; sub?: React.ReactNode; valueClass?: string }) {
  return (
    <div className="min-w-0 rounded-tile bg-surface-2/60 px-3 py-2.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`font-num text-lg font-semibold tabular-nums ${valueClass}`}>{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function ReasonList({ title, items, tone: t }: { title: string; items: string[]; tone: string }) {
  return <div><div className={`mb-1 text-xs font-semibold uppercase tracking-wide ${t}`}>{title}</div><ul className="m-0 list-disc space-y-0.5 pl-5">{items.map((x) => <li key={x}>{x}</li>)}</ul></div>;
}

export function Signals() {
  const [params] = useSearchParams();
  const { data, error, reload } = usePage<Data>(`/signals${params.toString() ? `?${params}` : ""}`);
  const { chrome } = useChrome();
  if (error) return <Empty>Could not load signals: {error}</Empty>;
  if (!data) return <div className="space-y-4">{[0, 1].map((k) => <SkeletonCard key={k} />)}</div>;
  const markets = Object.entries(data.config.markets ?? {}) as [string, any][];
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Signals<Tip k="signal" label="a signal" /></h1>
      <p className="mt-1 mb-5 text-[13px] text-muted-foreground">
        Approving places an order for the next session: entries as a limit order at the signal price (a protective stop is placed once filled), exits at the next open.{" "}
        {markets.filter(([, c]) => c.enabled !== false).map(([code, c], i) => <span key={code}><b className="text-text">{code}</b>: {c.validated ? "validated" : "paper only (not validated)"}{i < markets.length - 1 ? " · " : ""}</span>)}
      </p>
      <Flash msg={data.msg} err={data.err} />
      {data.pending.length === 0 ? (
        <Empty>No pending signals. The daily job creates them after each market closes.</Empty>
      ) : (
        <div className="space-y-4">{data.pending.map((s) => <SignalCard key={s.id} s={s} data={data} shortsOn={!!chrome?.shorts_on} onDone={reload} />)}</div>
      )}
      {data.recent.length > 0 && (
        <>
          <H2>Recent decisions</H2>
          <TableWrap>
            <table className="w-full min-w-[720px] text-[13px]">
              <thead className="text-left text-xs text-muted-foreground"><tr className="[&_th]:px-3.5 [&_th]:py-2.5"><th>Created</th><th>Market</th><th>Action</th><th>Symbol</th><th className="text-right">Qty</th><th className="text-right">Price</th><th>Status</th><th>Decided</th></tr></thead>
              <tbody>{data.recent.map((s) => (
                <tr key={s.id} className="border-t border-line [&_td]:px-3.5 [&_td]:py-2.5 hover:bg-row-hover">
                  <td>{String(s.created_at).slice(0, 16).replace("T", " ")}</td><td>{s.market}</td><td>{actionWord(s)}</td>
                  <td><Link to={`/m/${s.market}/${encodeURIComponent(s.symbol)}`} className="text-accent">{s.symbol}</Link></td>
                  <td className="text-right tabular-nums">{s.quantity}</td><td className="text-right tabular-nums">{Number(s.entry_price).toFixed(2)}</td>
                  <td><Badge size="sm" variant={s.outcome?.[0] === "filled" ? "success" : s.outcome?.[0] === "waiting" ? "info" : s.outcome?.[0] === "rejected" ? "neutral" : "warning"}>{s.outcome?.[1] ?? s.status}</Badge></td>
                  <td className="text-xs text-muted-foreground">{s.decided_at ? String(s.decided_at).slice(0, 16).replace("T", " ") : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        </>
      )}
    </>
  );
}

