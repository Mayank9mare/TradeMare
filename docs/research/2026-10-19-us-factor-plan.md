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

---

# Results (appended after the runs; the plan above was committed first, in 9ea04c3)

Before any test, US prices were extended from 2018-10 back to 2008-10. Only the date range was checked.

## Step 1: selection, in-sample 2019-10-15 → 2024-10-15

| Variant | CAGR | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| U0 momentum | +14.4% | 0.8131 | −23.5% | 84 |
| U1 + gross profitability | +0.8% | 0.1299 | −35.9% | 99 |
| **U2 + earnings yield** | +12.9% | **0.8192** | −22.3% | 59 |
| S&P 500 | +14.2% | 0.73 | −33.9% | |

All three meet the drawdown condition (U1 is 2 points worse than the index, inside the 5-point limit).
**U2 is selected** on the best Sharpe. Its margin over U0 is 0.006, effectively a tie.

## Step 2: decisive holdout, 2010-01-04 → 2019-10-14, U2, one run

| | U2 | S&P 500 |
|---|---|---|
| Total return | +240.2% | +161.8% |
| CAGR | +13.3% | +10.3% |
| Sharpe | **0.89** | 0.74 |
| Max drawdown | **−23.9%** | −19.8% |

**Pass:** Sharpe is above the index's, and the drawdown is 4.1 points worse, inside the 5-point limit.
The edge sits in the first half: 2010-01 → 2015-11 Sharpe 1.01 vs 0.70, but 2015-11 → 2019-10 only
0.68 vs 0.81. The kill switch tripped 16 times.

## Step 3: walk-forward, 2010 → 2026-10 (choices from 2012)

| Year | Pick | Return | S&P 500 |
|---|---|---|---|
| 2012 | U2 | +1.5% | +13.4% |
| 2013 | U2 | +43.0% | +29.6% |
| 2014 | U2 | +6.0% | +11.4% |
| 2015 | U1 | +1.8% | −0.7% |
| 2016 | U2 | −5.6% | +9.5% |
| 2017 | U1 | +20.6% | +19.4% |
| 2018 | U0 | −6.3% | −6.2% |
| 2019 | U0 | −1.9% | +28.9% |
| 2020 | U2 | −18.5% | +16.3% |
| 2021 | U2 | +29.3% | +26.9% |
| 2022 | U2 | −15.0% | −19.4% |
| 2023 | U0 | +22.9% | +24.2% |
| 2024 | U2 | +32.3% | +23.3% |
| 2025 | U2 | +2.5% | +16.4% |
| 2026 (to Oct) | U2 | +7.5% | +11.9% |

| | Walk-forward | S&P 500 (same sessions) |
|---|---|---|
| CAGR | +6.8% | +12.9% |
| Sharpe | **0.49** | 0.82 |
| Max drawdown | −38.6% | −33.9% |

**Fail.** The Sharpe is well below the index's, and the drawdown is 4.7 points worse (inside the limit,
but the Sharpe condition alone fails the step). The strategy lagged the index in 9 of 15 years.

Full-span reference (not part of the bar), each variant 2010-01 → 2026-10:
- U0: CAGR +10.8%, Sharpe 0.67, DD −26.3%
- U1: +7.0%, 0.51, −27.2%
- U2: +11.6%, 0.76, −29.7%
- S&P 500: Sharpe 0.75, DD −33.9%

## Verdict
**The US fails the pre-registered bar.** The holdout passed, but the walk-forward check failed: chosen
year by year from past results, the strategy made about half the index's return with a deeper
drawdown. Even the best single variant over the whole span (U2) only matched the S&P 500's Sharpe,
and that is before the survivorship bias, which flatters it.

- **Applied:** nothing. The US stays **signals-only** on its current settings.
- **Not done:** no re-tuning on any examined period, as the plan requires.

This matches the published evidence that momentum in US large caps has added little since about 2009.
A future US round should test a different idea (for example, a broader universe or a
trend-following allocation between stocks and bonds), with its own plan written first.
