/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";

import { Empty, Flash, H2, TableWrap, Tip } from "@/components/bits";
import { Badge } from "@/components/wensity/badge";
import { Button } from "@/components/wensity/button";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { useToastManager } from "@/components/wensity/toast";
import { postForm } from "@/lib/actions";
import { useChrome } from "@/lib/chrome";
import { money, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic IPOs page (templates/ipos.html), same data via /ipos?format=json.

const times = (x: number | null | undefined) => (x == null ? "—" : `${x.toFixed(1)}×`);
const timesWidth = (x: number | null | undefined) => (!x || x <= 0 ? 0 : Math.round(Math.min(100, (Math.log10(x + 1) / Math.log10(301)) * 100) * 10) / 10);
const day = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "2-digit", month: "short" }) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", opts) : "—");
const STATUS: Record<string, string> = { open: "bidding open", upcoming: "opens soon", closed: "closed · awaiting listing", listed: "listed" };
const KIND: Record<string, string> = { fresh: "Fresh issue", ofs: "Offer for sale", "fresh+ofs": "Fresh + OFS" };

function LabelPill({ label }: { label: string | null | undefined }) {
  if (!label) return null;
  return <><Badge size="sm" variant={label === "Favourable" ? "success" : label === "Unfavourable" ? "danger" : "warning"}>{label}</Badge><Tip k="ipo_label" label="this label" /></>;
}

function SubBar({ name, x, k }: { name: string; x: number | null | undefined; k: string }) {
  return (
    <div className="grid grid-cols-[minmax(0,11rem)_1fr_4rem] items-center gap-2 text-[13px]">
      <span className="text-muted-foreground">{name}<Tip k={k} /></span>
      <span className="h-1.5 rounded-full bg-surface-2"><i className="block h-full rounded-full bg-accent" style={{ width: `${timesWidth(x)}%` }} /></span>
      <b className="text-right tabular-nums">{times(x)}</b>
    </div>
  );
}

function IpoCard({ c, data, onDone }: { c: any; data: any; onDone: () => void }) {
  const { chrome } = useChrome();
  const toasts = useToastManager();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const i = c.ipo, f = i.facts, sub = i.subscription, st = c.stats;
  const notes: string[] = [...(c.notes ?? []), ...(c.retail_odds && c.status !== "listed" ? [`Retail allotment is a lottery: roughly a 1 in ${Math.round(1 / c.retail_odds)} chance of a lot.`] : [])];
  const qa = data.asks?.[i.symbol] ?? [];
  return (
    <Card id={`ipo-${i.symbol}`} className="p-5">
      <div className="flex flex-wrap items-center gap-2"><b className="text-base">{i.company}</b> <Badge size="sm" variant="neutral">{i.symbol}</Badge> <Badge size="sm" variant={c.status === "open" ? "info" : "outline"}>{STATUS[c.status]}</Badge> <LabelPill label={c.label} /></div>
      <div className="mt-1 text-xs text-muted-foreground">
        {c.status === "upcoming" ? `bidding ${day(i.start)} – ${day(i.end)}`
          : c.status === "open" ? (i.end === data.today ? <b className="text-down">closes today, 5 pm IST</b> : <>closes <b className="text-text">{day(i.end)}, 5 pm IST</b></>)
          : <>bidding closed {day(i.end)}{i.listing_date && ` · lists ${day(i.listing_date)}`}</>}
        {f?.lead_managers && ` · lead managers: ${f.lead_managers}`}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Lv label={<>Price band<Tip k="price_band" label="the price band" /></>} value={`₹${Math.round(i.price_low).toLocaleString("en-IN")}${i.price_high !== i.price_low ? ` – ${Math.round(i.price_high).toLocaleString("en-IN")}` : ""}`} sub={i.issue_price ? `final ₹${i.issue_price.toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : undefined} />
        <Lv label={<>Lot<Tip k="lot" label="a lot" /></>} value={`${f?.lot ?? "—"} shares`} sub={i.min_investment ? `${money(i.min_investment, "IN", false)} per lot` : undefined} />
        {i.issue_value && <Lv label={<>Issue size<Tip k="issue_size" label="issue size" /></>} value={money(i.issue_value, "IN")} />}
        <Lv label={<>Type<Tip k="issue_type" label="issue type" /></>} value={f ? KIND[f.kind] ?? "—" : "—"} />
      </div>
      {sub && (
        <div className="mt-3 space-y-1.5">
          <p className="m-0 mb-1 text-xs text-muted-foreground">Subscription<Tip k="subscription" /> · {c.status !== "open" ? "final" : "so far"}{sub.as_of && ` · as of ${sub.as_of}`}</p>
          <SubBar name="Institutions (QIB)" x={sub.qib} k="qib" /><SubBar name="Wealthy individuals (NII)" x={sub.nii} k="nii" /><SubBar name="Retail" x={sub.retail} k="retail_portion" /><SubBar name="Total" x={sub.total} k="subscription" />
        </div>
      )}
      {st?.count > 0 && <p className="mb-0 text-[13px]">Past IPOs with QIB {c.bucket}<Tip k="qib_bucket" label="this group" />: <b>{st.count}</b>, median listing-day gain <b className={tone(st.median_gain)}>{pct(st.median_gain, 0, true)}</b>, <b>{pct(st.share_above, 0)}</b> listed above issue{st.count_6m ? <>, after 6 months a median <b className={tone(st.median_excess_6m)}>{pct(st.median_excess_6m, 1, true)}</b> vs the Nifty</> : null}.</p>}
      {c.listing && <p className="mb-0 text-[13px]">Listed: day-one close <b className={tone(c.listing_gain)}>{pct(c.listing_gain, 1, true)}</b> vs issue price · now <b className={tone(c.gain_since)}>{pct(c.gain_since, 1, true)}</b> <span className="text-xs text-muted-foreground">({c.listing.last_date})</span></p>}
      {notes.length > 0 && <ul className="mt-3 mb-0 list-none space-y-1 p-0 text-[13px]">{notes.map((n) => <li key={n} className="rounded-tile border-l-4 border-l-line-strong bg-surface-2/60 px-3 py-1.5">{n}</li>)}</ul>}
      {f?.prospectus_url && <p className="mb-0 text-xs text-muted-foreground">Prospectus (RHP): <a href={f.prospectus_url} className="text-accent">{f.prospectus_url.split("/").pop()}</a> · read the risk factors and financials before applying.</p>}
      {["open", "upcoming", "closed"].includes(c.status) && (
        <details className="mt-3 rounded-tile border border-line" open={qa.length > 0}>
          <summary className="cursor-pointer px-4 py-2.5 text-[13px] font-semibold">Ask Claude about this IPO<Tip k="ask_claude" label="Ask Claude" />{qa.length > 0 && <span className="font-normal text-faint"> ({qa.length})</span>}</summary>
          <div className="space-y-3 px-4 pb-4">
            {qa.map((a: any, n: number) => (
              <div key={n} className="rounded-tile bg-surface-2/60 p-3 text-[13px]"><p className="m-0 font-semibold">{a.question}</p><p className="mt-1 mb-0 whitespace-pre-line">{a.answer}</p>
                {a.risks?.length > 0 && <ul className="mt-1 mb-0 list-disc pl-5">{a.risks.map((r: string) => <li key={r}>{r}</li>)}</ul>}
                <p className="mt-1 mb-0 text-xs text-muted-foreground">{String(a.ts).slice(0, 16).replace("T", " ")} UTC · ${(a.cost_usd ?? 0).toFixed(3)} · from the facts on this card only</p></div>
            ))}
            {data.llm_missing ? <p className="m-0 text-xs text-muted-foreground">To use this, {data.llm_missing}.</p> : (
              <form className="flex flex-wrap items-center gap-2" onSubmit={async (e) => {
                e.preventDefault(); if (!chrome) return;
                setBusy(true);
                const r = await postForm(`/ipos/${encodeURIComponent(i.symbol)}/ask`, chrome.csrf_token, { question: q });
                setBusy(false);
                toasts.add({ title: r.ok ? "Answer saved on this card" : "Could not ask", description: r.ok ? undefined : r.message, data: { variant: r.ok ? "success" : "error" } });
                if (r.ok) { setQ(""); onDone(); }
              }}>
                <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="What stands out in this IPO, for and against?" aria-label="Your question" className="min-w-0 flex-1 rounded-tile border border-line bg-surface px-3 py-2 text-[13px]" />
                <Button type="submit" size="sm" loading={busy}>Ask</Button>
                <span className="w-full text-xs text-muted-foreground">Uses only the facts on this card (Claude may not know this company); counts toward llm.max_daily_cost_usd.</span>
              </form>
            )}
          </div>
        </details>
      )}
    </Card>
  );
}

function Lv({ label, value, sub }: { label: React.ReactNode; value: string; sub?: string }) {
  return <div className="rounded-tile bg-surface-2/60 px-3 py-2.5"><div className="text-xs text-muted-foreground">{label}</div><div className="font-num text-base font-semibold tabular-nums">{value}</div>{sub && <div className="text-xs text-muted-foreground">{sub}</div>}</div>;
}

export function Ipos() {
  const { data, error, reload } = usePage<Record<string, any>>("/ipos");
  if (error) return <Empty>Could not load IPOs: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const by = (s: string) => (data.cards as any[]).filter((c) => c.status === s);
  const open = by("open"), soon = by("upcoming"), closed = by("closed"), listed = by("listed");
  const study = data.study;
  const grid = (cs: any[]) => <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,460px),1fr))] gap-4">{cs.map((c) => <IpoCard key={c.ipo.symbol} c={c} data={data} onDone={reload} />)}</div>;
  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">IPOs<Tip k="ipo" label="an IPO" /></h1>
      <div className="mt-3"><Flash msg={data.msg} err={data.err} /></div>
      <p className="mt-1 text-[13px] text-muted-foreground">NSE mainboard issues: open now, opening soon, and the last 30 days. Figures come from NSE (subscription updates hourly while bidding is open). TradeMare never applies for you: apply through your bank's or broker's IPO screen with a UPI mandate before 5 pm on the last day.</p>
      <Card className="mt-4 p-5">
        <h2 className="m-0 text-base font-semibold">What QIB demand has meant for past IPOs<Tip k="qib_bucket" label="these groups" /></h2>
        {study ? (<>
          <p className="mt-1 text-xs text-muted-foreground">{study.n} NSE mainboard IPOs listed 2016 – Oct 2026, grouped by final institutional (QIB) subscription.{" "}
            {study.h1_holds ? "Higher QIB demand went with better listings in both halves of the sample, so each group carries a fixed label." : "The link between QIB demand and listings was not reliable in both halves of the sample, so no labels are shown."}{" "}
            <a href="https://github.com/Mayank9mare/TradeMare/blob/main/docs/research/2026-10-19-ipo-demand-plan.md" className="text-accent">Method and results</a></p>
          <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-[13px]">
            <thead className="text-xs text-muted-foreground"><tr className="[&_th]:px-2.5 [&_th]:py-2"><th className="text-left">QIB subscribed</th><th className="text-right">IPOs</th><th className="text-right">Median listing-day gain<Tip k="listing_gain" label="listing-day gain" /></th><th className="text-right">Listed above issue</th><th className="text-right">6 months vs Nifty<Tip k="excess_6m" label="6 months vs Nifty" /></th>{study.h1_holds && <th className="text-left">Label</th>}</tr></thead>
            <tbody>{(data.buckets as string[]).map((name) => { const b = study.buckets?.[name]; return (
              <tr key={name} className="border-t border-line [&_td]:px-2.5 [&_td]:py-2"><td><b>{name}</b></td><td className="text-right tabular-nums">{b?.count ?? 0}</td>
                <td className={`text-right tabular-nums ${tone(b?.median_gain)}`}>{b?.count ? pct(b.median_gain, 1, true) : "—"}</td><td className="text-right tabular-nums">{b?.count ? pct(b.share_above, 0) : "—"}</td>
                <td className={`text-right tabular-nums ${tone(b?.median_excess_6m)}`}>{b?.count_6m ? <>{pct(b.median_excess_6m, 1, true)}<div className="text-xs text-muted-foreground">median of {b.count_6m}</div></> : "—"}</td>
                {study.h1_holds && <td><LabelPill label={data.bucket_labels?.[name]} /></td>}</tr>
            ); })}</tbody></table></div>
        </>) : <p className="mt-1 mb-0 text-xs text-muted-foreground">The study has not been run on this machine yet. Run <code>trademare ipo study</code> (takes about 20 minutes the first time) to see how past IPOs with similar demand listed.</p>}
      </Card>
      <H2 count={open.length}>Bidding open</H2>{open.length ? grid(open) : <Empty>No mainboard IPO is taking bids right now.</Empty>}
      <H2 count={soon.length}>Opening soon</H2>{soon.length ? grid(soon) : <Empty>Nothing announced yet. Issues usually appear here a few days before they open.</Empty>}
      {closed.length > 0 && <><H2 count={closed.length}>Closed, awaiting listing</H2>{grid(closed)}</>}
      {listed.length > 0 && (<>
        <H2 count={listed.length}>Listed in the last 30 days</H2>
        <p className="mt-0 text-xs text-muted-foreground">How each did against the issue price, with the label the fixed rule gives its final QIB demand. Issues listed up to 17 Oct 2026 were part of the study; later ones are a fresh check of the rule.</p>
        <TableWrap><table className="w-full min-w-[720px] text-[13px]">
          <thead className="text-xs text-muted-foreground"><tr className="[&_th]:px-3.5 [&_th]:py-2.5"><th className="text-left">Company</th><th className="text-left">Listed</th><th className="text-right">Issue price</th><th className="text-right">QIB<Tip k="qib" /></th>{study?.h1_holds && <th className="text-left">Rule said</th>}<th className="text-right">Day one<Tip k="listing_gain" label="listing-day gain" /></th><th className="text-right">Since</th></tr></thead>
          <tbody>{listed.map((c) => { const i = c.ipo; return (
            <tr key={i.symbol} className="border-t border-line [&_td]:px-3.5 [&_td]:py-2.5"><td><b>{i.company}</b><div className="text-xs text-muted-foreground">{i.symbol}</div></td><td>{day(i.listing_date, { day: "2-digit", month: "short" })}</td>
              <td className="text-right tabular-nums">{money(i.price, "IN", false)}</td><td className="text-right tabular-nums">{times(c.qib)}</td>
              {study?.h1_holds && <td>{c.label ? <LabelPill label={c.label} /> : "—"}</td>}
              <td className={`text-right tabular-nums ${tone(c.listing_gain)}`}>{pct(c.listing_gain, 1, true)}</td>
              <td className={`text-right tabular-nums ${tone(c.gain_since)}`}>{pct(c.gain_since, 1, true)}{c.listing && <div className="text-xs text-muted-foreground">{c.listing.last_date}</div>}</td></tr>
          ); })}</tbody></table></TableWrap>
      </>)}
      {data.us?.length > 0 && (<>
        <H2>United States <small className="text-sm font-normal text-muted-foreground">{data.us.length} this month</small></H2>
        <p className="mt-0 text-xs text-muted-foreground">From Nasdaq's IPO calendar. US issues publish no demand figures and retail investors rarely get allocations, so these are listed for information only, without a track record or label.</p>
        <TableWrap><table className="w-full min-w-[720px] text-[13px]">
          <thead className="text-xs text-muted-foreground"><tr className="[&_th]:px-3.5 [&_th]:py-2.5"><th className="text-left">Company</th><th className="text-left">Exchange</th><th className="text-left">Date</th><th className="text-right">Price</th><th className="text-right">Shares</th><th className="text-right">Raise</th><th className="text-left">Status</th></tr></thead>
          <tbody>{data.us.map((i: any) => (
            <tr key={i.symbol} className="border-t border-line [&_td]:px-3.5 [&_td]:py-2.5"><td><b>{i.company}</b><div className="text-xs text-muted-foreground">{i.symbol}</div></td><td>{i.exchange}</td><td>{i.start ? day(i.start, { day: "2-digit", month: "short" }) : "—"}</td>
              <td className="text-right tabular-nums">{i.price_low && i.price_high && i.price_low !== i.price_high ? `$${i.price_low}–${i.price_high}` : money(i.price_high, "US", false)}</td>
              <td className="text-right tabular-nums">{i.shares_offered ? Math.round(i.shares_offered).toLocaleString("en-US") : "—"}</td><td className="text-right tabular-nums">{money(i.issue_value, "US")}</td><td>{i.extra?.deal}</td></tr>
          ))}</tbody></table></TableWrap>
      </>)}
    </>
  );
}
