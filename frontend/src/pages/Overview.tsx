import { useEffect, useState } from "react";

import { Sparkline } from "@/components/Sparkline";
import { getJSON, type MarketSummary, type Overview as Data, type Tile } from "@/lib/api";
import { money, pct, shortDay, tone } from "@/lib/format";

const TILE_EDGE: Record<string, string> = {
  warn: "border-l-warning",
  up: "border-l-good",
  down: "border-l-critical",
  "": "border-l-line-strong",
};
const MOOD: Record<string, [string, string]> = {
  risk_on: ["risk-on", "text-up"],
  neutral: ["neutral", "text-muted"],
  risk_off: ["risk-off", "text-down"],
};

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-card border border-card-line bg-surface p-5 shadow-card ${className}`}>{children}</section>
  );
}

function TodayTile({ t }: { t: Tile }) {
  return (
    <a
      href={t.href}
      className={`flex min-w-0 flex-col gap-1 rounded-tile border border-card-line border-l-4 bg-surface px-4 py-3.5 text-text no-underline shadow-card transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${TILE_EDGE[t.tone] ?? TILE_EDGE[""]}`}
    >
      <span className="text-[11px] font-semibold tracking-wide text-muted uppercase">{t.label}</span>
      <span className="font-num text-2xl leading-tight font-bold tabular-nums">{t.value}</span>
      {t.sub && <span className="text-[13px] break-words text-muted">{t.sub}</span>}
    </a>
  );
}

function MoneyTile(props: {
  label: string; badge?: string; value: string; delta: string; deltaTone: string; sub: string; href: string; real?: boolean;
}) {
  return (
    <a
      href={props.href}
      className={`flex min-w-0 flex-col gap-1 rounded-tile border bg-surface px-[18px] py-4 text-text no-underline shadow-card transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${props.real ? "border-accent/50" : "border-card-line"}`}
    >
      <span className="text-[11px] font-semibold tracking-wide text-muted uppercase">
        {props.label}
        {props.badge && <span className="ml-1.5 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium tracking-normal normal-case">{props.badge}</span>}
      </span>
      <span className="font-num text-[32px] leading-tight font-bold tracking-tight tabular-nums">{props.value}</span>
      <span className={`text-[13px] font-semibold ${props.deltaTone}`}>{props.delta}</span>
      <span className="text-[13px] text-muted">{props.sub}</span>
    </a>
  );
}

function MarketCard({ m }: { m: MarketSummary }) {
  const [mood, moodTone] = MOOD[m.mood ?? ""] ?? ["—", "text-muted"];
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-base font-semibold">{m.name}</h3>
        <span className={`text-xs ${moodTone}`}>Market mood: {mood}</span>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Sparkline points={m.equity_line} label="Paper account" />
        <Sparkline points={m.index_line} label={`${m.index_name} · 6 months`} />
      </div>
      <h4 className="mt-5 mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
        Top ideas {m.scan_date && <span className="font-normal">· scan {m.scan_date}</span>}
      </h4>
      {m.ideas.length ? (
        <table className="w-full border-collapse text-[13px]">
          <tbody>
            {m.ideas.map((i) => (
              <tr key={i.symbol} className="border-t border-line first:border-t-0 hover:bg-row-hover">
                <td className="py-2 pr-2">
                  <a href={`/m/${m.code}/${encodeURIComponent(i.symbol)}`} className="font-semibold text-accent">{i.symbol}</a>
                  {i.sector && <div className="text-xs text-muted">{i.sector}</div>}
                </td>
                <td className="py-2 text-right tabular-nums">
                  {money(i.entry, m.currency, true)}
                  <div className="text-xs text-muted">stop {pct(i.stop / i.entry - 1)}</div>
                </td>
                <td className="w-24 py-2 pl-3 text-right">
                  <span className="font-num font-semibold text-up tabular-nums">{Math.round(Math.max(0, Math.min(1, i.score)) * 100)}</span>
                  <div className="text-xs text-muted">score</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="rounded-tile bg-surface-2 px-4 py-3 text-center text-sm text-muted">No ideas yet: they appear after the next daily run.</p>
      )}
      <a className="mt-2 inline-block text-[13px] text-accent" href={`/m/${m.code}/ideas`}>All ideas →</a>
    </Card>
  );
}

export function Overview() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    getJSON<Data>("/api/overview", ctl.signal).then(setData, (e: unknown) => {
      if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    });
    return () => ctl.abort();
  }, []);

  if (error) {
    return (
      <Card className="border-critical/40">
        <h2 className="m-0 text-lg font-semibold">Could not load the overview</h2>
        <p className="text-muted">{error}. Is <code>trademare serve</code> running? <a className="text-accent" href="/">Open the classic dashboard</a>.</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading">
        {[120, 140, 320].map((h) => <div key={h} className="animate-pulse rounded-card bg-surface-2" style={{ height: h }} />)}
      </div>
    );
  }

  const todayLabel = new Date(`${data.today}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  return (
    <div className="space-y-8">
      <section aria-labelledby="today-h" className="rounded-card border border-accent/35 bg-[linear-gradient(var(--tm-accent-soft),var(--tm-accent-soft)),var(--tm-surface)] px-5 py-5">
        <div className="mb-3 flex items-baseline gap-3">
          <h2 id="today-h" className="m-0 text-xl font-semibold">Today</h2>
          <span className="text-[13px] text-muted">{todayLabel}</span>
        </div>
        {data.tiles.length ? (
          <>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,210px),1fr))] gap-3">
              {data.tiles.map((t) => <TodayTile key={t.label} t={t} />)}
            </div>
            {data.pending_total > 0 && (
              <div className="mt-4 flex justify-end">
                <a href="/signals" className="rounded-tile bg-accent px-4 py-2 font-semibold text-white no-underline hover:opacity-90 dark:text-bg">Review signals →</a>
              </div>
            )}
          </>
        ) : (
          <p className="m-0 text-muted"><b className="text-text">You're all caught up.</b> New signals arrive after each market's close.</p>
        )}
      </section>

      <section aria-labelledby="money-h">
        <h2 id="money-h" className="mb-3 text-base font-semibold">Your money</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3">
          {data.zerodha && (
            <MoneyTile
              real label="Zerodha portfolio" badge="real money" href="/portfolio/IN#outside"
              value={money(data.zerodha.value, "INR")}
              delta={`${money(data.zerodha.pnl, "INR")} · ${pct(data.zerodha.ret)}`} deltaTone={tone(data.zerodha.ret)}
              sub={`${data.zerodha.count} stocks · invested ${money(data.zerodha.cost, "INR")} · as of ${shortDay(data.zerodha.as_of.slice(0, 10))}`}
            />
          )}
          {data.markets.map((m) => {
            const day = m.previous_equity ? m.equity / m.previous_equity - 1 : null;
            const fromPeak = m.peak ? m.equity / m.peak - 1 : 0;
            return (
              <MoneyTile
                key={m.code} label={`${m.code === "IN" ? "India" : "US"} paper account`} href={`/portfolio/${m.code}`}
                value={money(m.equity, m.currency)}
                delta={`${day ? `${pct(day, 2)} today` : "no change today"} · ${pct(fromPeak, 1, false)} from peak`}
                deltaTone={tone(day)}
                sub={`${m.positions} of ${m.max_positions} positions · cash ${money(m.cash, m.currency)}`}
              />
            );
          })}
        </div>
      </section>

      <section aria-labelledby="markets-h">
        <h2 id="markets-h" className="mb-3 text-base font-semibold">Markets</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] gap-4">
          {data.markets.map((m) => <MarketCard key={m.code} m={m} />)}
        </div>
      </section>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] gap-4">
        <Card>
          <h2 className="m-0 mb-3 text-base font-semibold">Coming up <span className="text-[13px] font-normal text-muted">· next 14 days</span></h2>
          {data.upcoming.length ? (
            <ul className="m-0 list-none p-0">
              {data.upcoming.map((u, i) => (
                <li key={i} className="grid grid-cols-[6.5rem_1fr] gap-3 border-t border-line py-2.5 text-[13px] first:border-t-0">
                  <span className={`tabular-nums ${u.kind === "results" ? "font-semibold text-warning" : u.kind === "deadline" ? "font-semibold text-accent" : "text-muted"}`}>
                    {u.day === data.today ? "Today" : shortDay(u.day)}
                  </span>
                  {u.href ? <a href={u.href} className="text-accent">{u.text}</a> : <span>{u.text}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-tile bg-surface-2 px-4 py-3 text-center text-sm text-muted">Nothing scheduled in the next two weeks.</p>
          )}
        </Card>
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="m-0 text-base font-semibold">Recent orders <span className="text-[13px] font-normal text-muted">· last 7 days</span></h2>
            <a href="/journal" className="text-[13px] text-accent">Trade journal →</a>
          </div>
          {data.orders.length ? (
            <ul className="m-0 list-none p-0">
              {data.orders.map((o, i) => (
                <li key={i} className="flex gap-2 border-t border-line py-2.5 text-[13px] first:border-t-0">
                  <span className={`h-fit shrink-0 rounded-full px-2 py-0.5 text-[11px] ${o.status === "filled" ? "bg-good/15 text-up" : o.status === "waiting" ? "bg-accent-soft text-accent" : "bg-warning/15 text-warning"}`}>{o.status}</span>
                  <span>{o.text}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-tile bg-surface-2 px-4 py-3 text-center text-sm text-muted">No orders in the last 7 days.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
