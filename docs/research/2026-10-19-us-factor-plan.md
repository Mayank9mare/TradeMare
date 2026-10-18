# US factor strategy: pre-registered research plan (written before any test was run)

The US has no tested strategy: in v2's factor study (2019-10 → 2024-10) no factor had a usable
information coefficient (`2026-10-17-strategy-v2-plan.md`), so the US has been signals-only. This round
asks one question fixed in advance: **does the India v3 recipe, momentum with volatility controls,
also work on US large caps?** Variants, selection rule, periods and pass bar are fixed here **before**
any US price before 2018-10 is downloaded and before any test. Results are appended at the end without
editing the plan.

## What is already known (and why it matters)
- The 2019–24 US factor study has been looked at. Momentum's IC there was slightly negative (−0.018,
  t −0.7). In-sample selection on that window is therefore biased *against* momentum, not for it.
- Nothing from US history before 2018-10-15 has been downloaded or examined. The decisive holdout uses
  **2010-01-04 → 2019-10-14**, as India did.

## Variants (exactly three, chosen from the literature, not from data)
All use India v3's rules unchanged: factor mode, monthly rebalance, buy the top 10% and sell below the
top 30%, half the slots in a risk-off regime, `vol_target: true`, `exclude_high_vol_pct: 0.2`, a 3× ATR
non-trailing protective stop, no take-profit and no time stop.

| Id | Factors | Rationale |
|---|---|---|
| U0 | `mom_12_1` | Jegadeesh & Titman (1993); the core of India v3 |
| U1 | `mom_12_1` + `gross_profitability` | quality and momentum combine well in US data (Novy-Marx 2013; Asness, Frazzini & Pedersen 2019) |
| U2 | `mom_12_1` + `earnings_yield` | exactly India v3's factors |

Fixed settings for every run (`--set` overrides; nothing else changes):
- `markets.US.strategy_mode=factor`, `markets.US.factors=[...]` per variant
- `markets.US.stop_loss.atr_multiple=3.0`, `markets.US.stop_loss.trailing=false`
- `markets.US.take_profit.method=none`, `markets.US.time_stop_days=null`
- `markets.US.max_open_positions=10` (as India)
- `markets.US.budget=100000`: the research measures the strategy, not integer-share rounding. A $1,000
  account cannot hold ten S&P 500 names in whole shares; if the US passes, the live budget needs
  raising or the strategy holding fewer names, which is a separate decision.

Unchanged from the current US settings: the S&P 500 universe (today's constituents), the $20M/day
liquidity filter, 5 bps slippage, the cost model, 1% risk per trade, the kill switch, and EDGAR
fundamentals dated by their SEC filing date.

## Step 1: selection (in-sample, 2019-10-15 → 2024-10-15)
Each variant is backtested once. **Choose the variant with the best in-sample Sharpe among those whose
in-sample max drawdown is no worse than the S&P 500's by more than 5 points.** If none qualifies, the
US fails at this step.

## Step 2: decisive holdout (2010-01-04 → 2019-10-14), one run of the chosen variant
**Pass bar:** Sharpe ≥ the S&P 500's (price index, `^GSPC`), **and** max drawdown no worse than the
S&P 500's by more than 5 points.

EDGAR annual data starts around 2009–2010, so fundamentals are thin early in the holdout. Factor mode
averages the factors present, so early on U1 and U2 behave like U0.

## Step 3: walk-forward check, 2010-01 → 2026-10
All three variants run once over the full span. At each calendar-year start, the variant with the best
Sharpe over the previous 3 years (at least 1 year of history) is held for the next year. The chained
series must also meet the pass bar against the S&P 500 over the same sessions.

## Decision
- **The US passes** only if Step 1 selects a variant and it passes Steps 2 and 3. Then the chosen
  settings are applied to `markets.US` and the US moves from signals-only to paper trading the factor
  strategy. `validated` stays **false**; real orders are the owner's decision.
- **A fail is reported as a fail.** No re-tuning on any examined period; the US stays signals-only.

## Known limitations (stated in advance)
- **Survivorship bias is severe:** today's S&P 500 members, back to 2010. Firms that dropped out or
  failed are absent. This flatters momentum, so a pass overstates the edge.
- Large-cap US momentum has been weak since about 2009 in published data, so a fail would not be
  surprising.
- Stock prices are dividend-adjusted (Yahoo `auto_adjust`), but `^GSPC` is a price index without
  dividends (about 2% a year). This flatters the strategy against the benchmark, by roughly the
  index's dividend yield in return and a little in Sharpe.
- Yahoo prices for older years can have gaps; symbols without history simply are not tradeable then.
