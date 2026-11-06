import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "react-router";

import { Alert } from "@/components/wensity/alert";
import { Badge, type BadgeVariant } from "@/components/wensity/badge";
import { Card as WCard } from "@/components/wensity/card";
import { NumberTicker } from "@/components/wensity/count-up-animation";
import { EmptyState } from "@/components/wensity/empty-state";
import { Marker, type MarkerTone } from "@/components/wensity/marker";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/wensity/table";
import { Button } from "@/components/wensity/button";
import { useToastManager } from "@/components/wensity/toast";
import { postForm } from "@/lib/actions";
import { getJSON, type MarketSummary, type Overview as Data, type Tile } from "@/lib/api";
import { useChrome } from "@/lib/chrome";
import { money, pct, shortDay, tone } from "@/lib/format";

const TILE_EDGE: Record<string, string> = {
  warn: "border-l-warning",
  up: "border-l-good",
  down: "border-l-critical",
  "": "border-l-line-strong",
};
const MOOD: Record<string, [string, string]> = {
  risk_on: ["risk-on", "text-up"],
  neutral: ["neutral", "text-muted-foreground"],
  risk_off: ["risk-off", "text-down"],
};

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <WCard className={`p-5 ${className}`}>{children}</WCard>;
}

// Recharts is the heaviest dependency: load it after the page has painted.
const MiniArea = lazy(() => import("@/components/MiniArea").then((m) => ({ default: m.MiniArea })));
const chartFallback = <div className="h-[84px] rounded-tile bg-surface-2" aria-hidden />;

const ORDER_BADGE: Record<string, BadgeVariant> = { filled: "success", waiting: "info" };
const UPCOMING_TONE: Record<string, MarkerTone> = { results: "warning", deadline: "info", ipo: "neutral" };

function TodayTile({ t }: { t: Tile }) {
  return (
    <Link
      to={t.href}
      className={`flex min-w-0 flex-col gap-1 rounded-tile border border-card-line border-l-4 bg-surface px-4 py-3.5 text-text no-underline shadow-card transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${TILE_EDGE[t.tone] ?? TILE_EDGE[""]}`}
    >
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{t.label}</span>
      <span className="font-num text-2xl leading-tight font-bold tabular-nums">{t.value}</span>
      {t.sub && <span className="text-[13px] break-words text-muted-foreground">{t.sub}</span>}
    </Link>
  );
}

function MoneyTile(props: {
  label: string; badge?: string; amount: number; currency: "INR" | "USD"; delta: string; deltaTone: string; sub: string;
  href: string; real?: boolean;
}) {
  return (
    <Link
      to={props.href}
      className={`flex min-w-0 flex-col gap-1 rounded-tile border bg-surface px-[18px] py-4 text-text no-underline shadow-card transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${props.real ? "border-accent/50" : "border-card-line"}`}
    >
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        {props.label}
        {props.badge && <span className="ml-1.5 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium tracking-normal normal-case">{props.badge}</span>}
      </span>
      <NumberTicker
        className="font-num text-[32px] leading-tight font-bold tracking-tight tabular-nums"
        value={props.amount}
        trigger="mount"
        locales={props.currency === "INR" ? "en-IN" : "en-US"}
        format={{ style: "currency", currency: props.currency, maximumFractionDigits: props.currency === "INR" ? 0 : 2 }}
        aria-label={money(props.amount, props.currency)}
      />
      <span className={`text-[13px] font-semibold ${props.deltaTone}`}>{props.delta}</span>
      <span className="text-[13px] text-muted-foreground">{props.sub}</span>
    </Link>
  );
}

function RunButton({ m, onDone }: { m: MarketSummary; onDone: () => void }) {
  const { chrome, refresh } = useChrome();
  const toasts = useToastManager();
  const [busy, setBusy] = useState(false);
  const time = (iso: string) => new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  if (m.run.running || busy) return <Button size="sm" variant="secondary" loading disabled>Updating…</Button>;
  if (m.run.block) {
    return <Button size="sm" variant="ghost" disabled title={m.run.block}>✓ Up to date{m.run.next ? ` · next ${time(m.run.next)}` : ""}</Button>;
  }
  return (
    <Button
      size="sm" variant="secondary" title="Fetch the latest prices, check fills and stops, and look for new signals"
      onClick={async () => {
        if (!chrome) return;
        setBusy(true);
        const r = await postForm(`/run/${m.code}`, chrome.csrf_token, { back: "/" });
        toasts.add({ title: r.ok ? r.message : "Could not start the update", description: r.ok ? undefined : r.message, data: { variant: r.ok ? "success" : "error" } });
        setBusy(false);
        refresh();
        onDone();
      }}
    >↻ Update now</Button>
  );
}

function MarketCard({ m, onChanged }: { m: MarketSummary; onChanged: () => void }) {
  const [mood, moodTone] = MOOD[m.mood ?? ""] ?? ["—", "text-muted-foreground"];
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-base font-semibold">
          {m.name} {m.validated && <Badge size="sm" variant="success">Live</Badge>}
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-xs ${moodTone}`}>Market mood: {mood}</span>
          <RunButton m={m} onDone={onChanged} />
        </div>
      </div>
      {m.kill_switch && (
        <Alert className="mb-3" variant="error" title="Kill switch tripped" description={<>{m.kill_reason}. New entries are blocked. <Link className="text-accent" to={`/portfolio/${m.code}`}>Review and re-arm</Link></>} />
      )}
      {m.run.outcome && <p className="mt-0 mb-3 text-xs text-muted-foreground">Last manual update: {m.run.outcome}</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Suspense fallback={chartFallback}>
          <MiniArea points={m.equity_line} label="Paper account" format={(v) => money(v, m.currency)} />
        </Suspense>
        <Suspense fallback={chartFallback}>
          <MiniArea points={m.index_line} label={`${m.index_name} · 6 months`} format={(v) => v.toLocaleString("en-IN", { maximumFractionDigits: 0 })} />
        </Suspense>
      </div>
      <h4 className="mt-5 mb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        Top ideas {m.scan_date && <span className="font-normal">· scan {m.scan_date}</span>}
      </h4>
      {m.vs_signals && m.pending === 0 && <p className="mt-0 mb-2 text-xs text-muted-foreground">{m.vs_signals}</p>}
      {m.ideas.length ? (
        <Table density="compact" className="text-[13px]">
          <TableHeader>
            <TableRow>
              <TableHead>Stock</TableHead>
              <TableHead className="text-right">Price · stop</TableHead>
              <TableHead className="text-right">Score</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {m.ideas.map((i) => (
              <TableRow key={i.symbol}>
                <TableCell>
                  <Link to={`/m/${m.code}/${encodeURIComponent(i.symbol)}`} className="font-semibold text-accent">{i.symbol}</Link>
                  {i.sector && <div className="text-xs text-muted-foreground">{i.sector}</div>}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {money(i.entry, m.currency, true)}
                  <div className="text-xs text-muted-foreground">stop {pct(i.stop / i.entry - 1)}</div>
                </TableCell>
                <TableCell className="text-right font-num font-semibold text-up tabular-nums">
                  {Math.round(Math.max(0, Math.min(1, i.score)) * 100)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="rounded-tile bg-surface-2 px-4 py-3 text-center text-sm text-muted-foreground">No ideas yet: they appear after the next daily run.</p>
      )}
      <Link className="mt-2 inline-block text-[13px] text-accent" to={`/m/${m.code}/ideas`}>All ideas →</Link>
      {m.holdings.length > 0 && (
        <>
          <h4 className="mt-5 mb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Your holdings today</h4>
          <Table density="compact" className="text-[13px]">
            <TableHeader>
              <TableRow><TableHead>Stock</TableHead><TableHead className="text-right">Today</TableHead><TableHead className="text-right">Since you bought</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {m.holdings.map((h) => (
                <TableRow key={h.symbol}>
                  <TableCell><Link to={`/m/${m.code}/${encodeURIComponent(h.symbol)}`} className="font-semibold text-accent">{h.symbol}</Link></TableCell>
                  <TableCell className={`text-right tabular-nums ${tone(h.today)}`}>{pct(h.today)}</TableCell>
                  <TableCell className={`text-right tabular-nums ${tone(h.since_entry)}`}>{pct(h.since_entry)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
      <details className="mt-4 border-t border-line pt-3 text-xs text-muted-foreground">
        <summary className="cursor-pointer">Data · prices to {m.data.prices_to ?? "—"} · last processed {m.last_processed ?? "—"}</summary>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
          <span>Breadth <b className="text-up">{m.data.advancers} up</b> / <b className="text-down">{m.data.decliners} down</b></span>
          <span>Prices <b className="text-text">{m.data.bars}/{m.data.universe_size}</b> stocks</span>
          <span>Fundamentals <b className="text-text">{m.data.fundamentals}/{m.data.universe_size}</b> ({m.data.source})</span>
        </div>
      </details>
    </Card>
  );
}

export function Overview() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const ctl = new AbortController();
    getJSON<Data>("/api/overview", ctl.signal).then(setData, (e: unknown) => {
      if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    });
    return () => ctl.abort();
  }, [version]);

  if (error) {
    return (
      <Alert
        variant="error"
        title="Could not load the overview"
        description={<>{error}. Is <code>trademare serve</code> running? <a className="text-accent" href="/">Open the classic dashboard</a>.</>}
      />
    );
  }
  if (!data) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading">
        {[0, 1, 2].map((k) => <SkeletonCard key={k} />)}
      </div>
    );
  }

  const todayLabel = new Date(`${data.today}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  return (
    <div className="space-y-8">
      <section aria-labelledby="today-h" className="rounded-card border border-accent/35 bg-[linear-gradient(var(--tm-accent-soft),var(--tm-accent-soft)),var(--tm-surface)] px-5 py-5">
        <div className="mb-3 flex items-baseline gap-3">
          <h2 id="today-h" className="m-0 text-xl font-semibold">Today</h2>
          <span className="text-[13px] text-muted-foreground">{todayLabel}</span>
        </div>
        {data.tiles.length ? (
          <>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,210px),1fr))] gap-3">
              {data.tiles.map((t) => <TodayTile key={t.label} t={t} />)}
            </div>
            {data.pending_total > 0 && (
              <div className="mt-4 flex justify-end">
                <Link to="/signals" className="rounded-tile bg-accent px-4 py-2 font-semibold text-white no-underline hover:opacity-90 dark:text-bg">Review signals →</Link>
              </div>
            )}
          </>
        ) : (
          <p className="m-0 text-muted-foreground"><b className="text-text">You're all caught up.</b> New signals arrive after each market's close.</p>
        )}
      </section>

      <section aria-labelledby="money-h">
        <h2 id="money-h" className="mb-3 text-base font-semibold">Your money</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3">
          {data.zerodha && (
            <MoneyTile
              real label="Zerodha portfolio" badge="real money" href="/portfolio/IN#outside"
              amount={data.zerodha.value} currency="INR"
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
                amount={m.equity} currency={m.currency}
                delta={`${day ? `${pct(day, 2)} today` : "no change today"} · ${pct(fromPeak, 1, false)} from peak`}
                deltaTone={tone(day)}
                sub={`${m.positions} of ${m.max_positions} positions · cash ${money(m.cash, m.currency)} · budget ${money(m.budget, m.currency)}`}
              />
            );
          })}
        </div>
      </section>

      <section aria-labelledby="markets-h">
        <h2 id="markets-h" className="mb-3 text-base font-semibold">Markets</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] gap-4">
          {data.markets.map((m) => <MarketCard key={m.code} m={m} onChanged={() => setVersion((v) => v + 1)} />)}
        </div>
      </section>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] gap-4">
        <Card>
          <h2 className="m-0 mb-3 text-base font-semibold">Coming up <span className="text-[13px] font-normal text-muted-foreground">· next 14 days</span></h2>
          {data.upcoming.length ? (
            <ul className="m-0 list-none p-0">
              {data.upcoming.map((u, i) => (
                <li key={i} className="grid grid-cols-[1rem_6.5rem_1fr] items-center gap-3 border-t border-line py-2.5 text-[13px] first:border-t-0">
                  <Marker variant="dot" size="sm" tone={UPCOMING_TONE[u.kind] ?? "neutral"} aria-hidden />
                  <span className={`tabular-nums ${u.kind === "results" ? "font-semibold text-warning" : u.kind === "deadline" ? "font-semibold text-accent" : "text-muted-foreground"}`}>
                    {u.day === data.today ? "Today" : shortDay(u.day)}
                  </span>
                  {u.href ? <Link to={u.href} className="text-accent">{u.text}</Link> : <span>{u.text}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nothing scheduled" description="No results dates for your stocks, IPOs or signal deadlines in the next two weeks." />
          )}
        </Card>
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="m-0 text-base font-semibold">Recent orders <span className="text-[13px] font-normal text-muted-foreground">· last 7 days</span></h2>
            <Link to="/journal" className="text-[13px] text-accent">Trade journal →</Link>
          </div>
          {data.orders.length ? (
            <ul className="m-0 list-none p-0">
              {data.orders.map((o, i) => (
                <li key={i} className="flex gap-2 border-t border-line py-2.5 text-[13px] first:border-t-0">
                  <Badge size="sm" variant={ORDER_BADGE[o.status] ?? "warning"} className="h-fit">{o.status}</Badge>
                  <span>{o.text}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No recent orders" description="Approved orders from the last 7 days appear here." />
          )}
        </Card>
      </div>
    </div>
  );
}
