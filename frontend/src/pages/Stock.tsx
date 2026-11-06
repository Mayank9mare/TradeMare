/* eslint-disable @typescript-eslint/no-explicit-any */
import { useParams } from "react-router";

import { Empty, H2, ScoreBar, Tip } from "@/components/bits";
import { RegimeBadge } from "@/components/RegimeBadge";
import { Ratio, Ratios } from "@/components/Ratio";
import { StockChart } from "@/components/StockChart";
import { Badge } from "@/components/wensity/badge";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/wensity/tabs";
import { useChrome } from "@/lib/chrome";
import { money, num, pct, tone } from "@/lib/fmt";
import { usePage } from "@/lib/page";

// The classic stock page (templates/symbol.html): chart with the strategy picker, analysis, key
// ratios and financial statements. Same data via /m/<code>/<symbol>?format=json.

export function Stock() {
  const { code = "IN", symbol = "" } = useParams();
  const { data, error } = usePage<Record<string, any>>(`/m/${code}/${encodeURIComponent(symbol)}`);
  const { chrome } = useChrome();
  if (error) return <Empty>Could not load {symbol}: {error}</Empty>;
  if (!data) return <SkeletonCard />;
  const m = code, x = data.ratios ?? {}, score = data.score, idea = data.idea, tx = data.texts ?? {};
  const ratioCells: [string, string, string][] = [
    ["Market cap", money(x.market_cap, m), "market_cap"], ["P/E", num(x.pe, 1), "pe"], ["P/B", num(x.price_to_book, 2), "price_to_book"],
    ["FCF yield", pct(x.fcf_yield, 1), "fcf_yield"], ["Revenue (TTM)", money(x.revenue_ttm, m), "ttm"], ["Revenue growth", pct(x.revenue_growth, 1, true), "revenue_growth"],
    ["Gross margin", pct(x.gross_margin, 1), "gross_margin"], ["Operating margin", pct(x.operating_margin, 1), "operating_margin"], ["Net margin", pct(x.net_margin, 1), "net_margin"],
    ["ROE", pct(x.roe, 1), "roe"], ["ROA", pct(x.roa, 1), "roa"], ["Debt / equity", num(x.debt_to_equity, 2), "debt_to_equity"],
    ["Current ratio", num(x.current_ratio, 2), "current_ratio"], ["Interest cover", num(x.interest_coverage, 1), "interest_coverage"],
  ];
  const labels = Object.keys(data.statements ?? {});
  return (
    <>
      <p className="m-0 text-xs text-muted-foreground">{m === "IN" ? "India" : "US"} / {data.symbol}</p>
      <div className="mt-1 mb-4 flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="m-0 text-[26px] font-bold tracking-tight">{data.symbol} <small className="text-base font-normal text-muted-foreground">{data.name}</small></h1>{data.sector && <Badge size="sm" variant="neutral">{data.sector}</Badge>}</div>
        <div className="text-right"><b className="font-num text-2xl tabular-nums">{money(data.close, m, false)}</b> <span className={tone(data.change)}>{pct(data.change, 2, true)}</span><div className="text-xs text-muted-foreground">close {data.last}</div></div>
      </div>
      <Card className="p-4"><StockChart market={m} symbol={data.symbol} choices={data.strategy_choices} /></Card>

      {score && (
        <>
          <H2>Analysis <small className="inline-flex items-center gap-1.5 text-sm font-normal text-muted-foreground">scan {data.scan.asof} · market <RegimeBadge regime={data.scan.regime} /></small></H2>
          <Card className="p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-[13px]">
              <div className="flex flex-wrap items-center gap-2"><span className="text-muted-foreground">Composite<Tip k="score" /></span> <ScoreBar value={score.composite} /> <span className="text-xs text-muted-foreground">confidence {Math.round(score.confidence * 100)}%<Tip k="confidence" /></span>{!score.liquid && <Badge size="sm" variant="warning">below liquidity threshold</Badge>}</div>
              {idea && <div>{(chrome?.shorts_on || idea.direction !== "LONG") && <Badge size="sm" variant="outline">{idea.direction}</Badge>} Trade idea: entry {money(idea.levels.entry, m, false)} · stop {money(idea.levels.stop, m, false)} · target {money(idea.levels.target, m, false)}</div>}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {Object.entries(score.analysts as Record<string, any>).map(([name, a]) => {
                const t = tx[name] ?? { label: name, details: [], reasons: [] };
                return (
                  <div key={name}>
                    <h3 className="m-0 mb-2 flex items-center gap-2 text-sm font-semibold">{t.label} <ScoreBar value={a.score} /></h3>
                    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">{t.details.map(([k, v]: [string, number]) => <span key={k} className="inline-flex items-center gap-1.5">{k} <ScoreBar value={v} /></span>)}</div>
                    <ul className="m-0 list-disc pl-5 text-[13px]">{t.reasons.length ? t.reasons.map((r: string) => <li key={r}>{r}</li>) : <li className="text-muted-foreground">No notable factors</li>}</ul>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      <H2>Key ratios <small className="text-sm font-normal text-muted-foreground">TTM<Tip k="ttm" label="TTM" />, as of {data.last}</small></H2>
      <Ratios>{ratioCells.map(([label, value, key]) => <Ratio key={label} label={<>{label}<Tip k={key} label={label} /></>}>{value}</Ratio>)}</Ratios>

      <H2>Financials <small className="text-sm font-normal text-muted-foreground">source: {data.source}</small></H2>
      {labels.length > 0 && (
        <Tabs defaultValue={labels[0]}>
          <TabsList>{labels.map((l) => <TabsTrigger key={l} value={l}>{l}</TabsTrigger>)}</TabsList>
          {labels.map((l) => {
            const t = data.statements[l];
            return (
              <TabsContent key={l} value={l} className="mt-3">
                {t.periods?.length ? (
                  <div className="overflow-x-auto rounded-card border border-line bg-surface">
                    <table className="w-full min-w-[720px] text-[13px]">
                      <thead className="text-xs text-muted-foreground">
                        <tr className="[&_th]:px-3.5 [&_th]:py-2"><th className="text-left">Period end</th>{t.periods.map((p: string) => <th key={p} className="text-right">{p}</th>)}</tr>
                        <tr className="[&_th]:px-3.5 [&_th]:pb-2 [&_th]:font-normal"><th className="text-left">Available from<Tip k="available_from" label="available from" /></th>{t.available.map((a: string, i: number) => <th key={i} className="text-right">{a}</th>)}</tr>
                      </thead>
                      <tbody>{(data.rows as string[]).map((f) => (
                        <tr key={f} className="border-t border-line hover:bg-row-hover [&_td]:px-3.5 [&_td]:py-2">
                          <td>{(f.charAt(0).toUpperCase() + f.slice(1)).replace(/_/g, " ")}</td>
                          {(t.values[f] ?? []).map((v: number | null, i: number) => <td key={i} className="text-right tabular-nums">{f === "shares_outstanding" && v != null ? num(v, 0) : money(v, m)}</td>)}
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                ) : <p className="text-[13px] text-muted-foreground">No {l.toLowerCase()} data cached.</p>}
              </TabsContent>
            );
          })}
        </Tabs>
      )}
    </>
  );
}
