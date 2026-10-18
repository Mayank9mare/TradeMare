# Strategy v3: pre-registered research plan (written before any test was run)

v2 (India: `mom_12_1` + `earnings_yield`, monthly, top 10% in / top 30% out, half the slots in a risk-off
regime) **failed** its out-of-sample bar on drawdown: −24.1% vs the Nifty 50's −15.6%
(`2026-10-17-strategy-v2-plan.md`). v3 keeps the v2 rules and only adds risk controls that are
documented in the literature. The aim is to lower drawdown, not to raise return. This file fixes the
variants, the selection rule, the test periods and the pass bar **before** any v3 test is run. Results
are appended at the end without editing the plan.

## The problem with re-using the v2 out-of-sample window
2024-10-16 → 2026-10-16 has already been looked at: v2 failed there, and that failure motivated v3. A
v3 "pass" on that window would be weak evidence. So:
- That window is reported **for information only** and cannot validate anything.
- The decisive test moves to **history no one in this project has examined**: 2010-01-01 → 2019-10-14,
  the years *before* the v2 in-sample period. That needs downloading older prices (`data.history_years`
  raised from 8 to 18). Nothing from that period has been loaded, plotted or summarised before this
  plan was committed.

## Variants (exactly four, India only)
The US had no qualifying factor in v2, so it stays signals-only and is not part of v3.

| Id | Change vs v2 | Rationale |
|---|---|---|
| V0 | none (v2 as configured) | reference |
| V1 | **market volatility targeting**: slots × min(1, typical / current index volatility) | Barroso & Santa-Clara (2015), "Momentum has its moments"; Moreira & Muir (2017) |
| V2 | **high-volatility exclusion**: no new entries in the most volatile 20% of the liquid universe | Ang et al. (2006); Frazzini & Pedersen (2014) |
| V3 | V1 + V2 | |

Definitions (fixed now, not tuned):
- **Current index volatility:** annualised standard deviation of the Nifty 50's daily returns over the
  last 63 sessions.
- **Typical volatility:** the median of that series over the trailing 756 sessions (3 years), with at
  least 252 observations required. It uses past data only, so no number is fitted.
- **Slots:** `floor(max_open_positions × vol_scale × regime_factor)`, where `regime_factor` is v2's 0.5
  in a risk-off regime. The weakest holdings beyond the slots are sold at the next open, exactly as v2
  already does in risk-off. Volatility is measured at each monthly rebalance only.
- **Stock volatility for V2:** annualised standard deviation of daily returns over 252 sessions (the
  v2 `low_vol` factor's input). The 20% cut is fixed. Exclusion applies to entries only; holdings are
  still exited by rank.

Note: in v2's in-sample factor study, India's `low_vol` IC had the *wrong* sign (high-volatility stocks
did better in 2019–24). V2 is included because the question here is drawdown, not return. If the
in-sample data rejects it, the selection rule below will drop it.

Everything else is unchanged from v2: 3× ATR protective stop, the cost model, 1% risk per trade,
`max_open_positions: 10`, the Nifty 500 universe filtered to the 200 most-traded names on each date,
monthly rebalance and the kill switch.

## Step 1: selection (in-sample only, 2019-10-15 → 2024-10-15)
Each variant is backtested once. **Choose the variant with the best in-sample Sharpe among those whose
in-sample max drawdown is no worse than the Nifty 50's by more than 5 points.** If none qualifies, v3
fails at this step.

## Step 2: decisive holdout (2010-01-01 → 2019-10-14), one run of the chosen variant
**Pass bar (same as v2):** Sharpe ≥ the Nifty 50's, **and** max drawdown no worse than the Nifty 50's by
more than 5 points.

Earnings data does not exist for this period (India statements start around 2022). Factor mode averages
the factors that are present, so in the holdout v3 is effectively **momentum plus the risk controls**.
That tests the part of the strategy that carries most of the weight and caused the v2 drawdown.

## Step 3: walk-forward check, 2010-01 → 2026-10
This is a test of the *selection procedure*, not of any single variant. All four variants are run
once over the full span. Then, at each calendar-year start, the variant with the best Sharpe over the
previous 3 years (minimum 1 year of history) is held for the next year. The yearly segments are
chained into one return series. That series must also meet the pass bar against the Nifty 50 over
the same span. Per-year excess returns and drawdowns are reported.

## Decision
- **India passes** only if Step 1 selects a variant and that variant passes both Step 2 and Step 3. A
  pass is reported and the new settings are applied, but `validated: true` is **not** switched on
  automatically: that is the owner's decision. Forward paper trading continues either way.
- **A fail is reported as a fail.** No re-tuning on any examined period.

## Known limitations (stated in advance)
- **Survivorship bias:** the universe is today's Nifty 500, and in 2010–2019 it is worse than in v2
  (companies that later failed or were delisted are absent). The point-in-time top-200 liquidity
  filter reduces this but does not remove it. It flatters momentum, so a pass overstates the edge.
- The holdout tests momentum without earnings yield (no data).
- Older Yahoo NSE data can have gaps. Symbols with missing history simply are not tradeable on those
  dates.
- Price indices exclude dividends, which slightly flatters the strategy relative to the index.

---

# Results (appended after the runs; the plan above was committed first, in 4288095)

Before any test, prices were extended to 2008-10 (`history_years: 18`). Only start dates were checked.

**V0 differs from the v2 write-up.** V0's in-sample result here (CAGR +34.1%, Sharpe 1.52, max DD
−27.5%) is not v2's documented +28.1% / 1.37 / −33.0%. Since then the statement-currency fix, recurring
earnings and today's stop-gap execution rule (a gap beyond the stop's 1% limit leg exits at the next
open) have changed the engine and data. All four variants below ran on the same engine and data.

## Step 1: selection, in-sample 2019-10-15 → 2024-10-15

| Variant | CAGR | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| V0 (v2 as is) | +34.1% | 1.5163 | −27.5% | 86 |
| V1 vol targeting | +32.5% | 1.5225 | −25.6% | 76 |
| V2 high-vol exclusion | +40.6% | 1.7951 | −23.4% | 59 |
| **V3 both** | +37.4% | **1.7952** | **−21.2%** | 55 |
| Nifty 50 | +17.0% | 0.94 | −38.4% | |

All four meet the drawdown condition. **V3 is selected** on the best Sharpe. The margin over V2 is
0.0001, effectively a tie. The rule is applied as written, and V3 is also the variant with the lowest
drawdown.

Contrary to the in-sample factor study, where high-volatility stocks did *better*, excluding them
improved the Sharpe here. Excluding them mostly avoided the worst stop-outs rather than missing winners.

## Step 2: decisive holdout, 2010-01-04 → 2019-10-14, V3, one run

| | V3 | Nifty 50 |
|---|---|---|
| Total return | +284.8% | +116.8% |
| CAGR | +14.8% | +8.2% |
| Sharpe | **1.04** | 0.60 |
| Max drawdown | **−21.5%** | −27.9% |

**Pass.** Sharpe ≥ the index and drawdown better than the index. Weak spots:
- 2018: −16.8% vs the index's +3.2%.
- The second half of the holdout (2015-11 → 2019-10) roughly matched the index: Sharpe 0.74 vs 0.81.

The edge was much stronger in 2010–2015 than afterwards.

## Step 3: walk-forward, 2010 → 2026-10 (choices from 2012; each needs 3 prior years, at least 1)

| Year | Pick | Return | Nifty 50 |
|---|---|---|---|
| 2012 | V1 | +39.7% | +27.7% |
| 2013 | V0 | +2.0% | +6.8% |
| 2014 | V3 | +70.9% | +31.4% |
| 2015 | V3 | +13.9% | −4.1% |
| 2016 | V3 | +12.5% | +3.0% |
| 2017 | V3 | +34.8% | +28.6% |
| 2018 | V3 | −16.8% | +3.2% |
| 2019 | V2 | +24.1% | +12.0% |
| 2020 | V0 | −3.3% | +14.9% |
| 2021 | V3 | +43.6% | +24.1% |
| 2022 | V3 | +14.3% | +4.3% |
| 2023 | V3 | +77.0% | +20.0% |
| 2024 | V2 | +32.7% | +8.8% |
| 2025 | V3 | +25.8% | +10.5% |
| 2026 (to Oct) | V3 | −5.0% | −11.4% |

| | Walk-forward | Nifty 50 (same sessions) |
|---|---|---|
| CAGR | +22.1% | +11.3% |
| Sharpe | **1.21** | 0.76 |
| Max drawdown | **−41.1%** | −38.4% |

**Pass, narrowly.** Drawdown is 2.7 points worse than the index, within the 5-point limit. The −41.1%
came in the 2020 COVID crash, a year for which the procedure had picked **V0, the variant without
volatility control**. V0's trailing 2017–19 Sharpe was best.

Full-span reference (not part of the bar), each variant 2010-01 → 2026-10:
- V0: Sharpe 0.91, DD −41.1%
- V1: 0.99, −29.3%
- V2: 1.15, −31.4%
- V3: **1.25, −24.2%**
- Nifty 50: 0.64, −38.4%

## For information only: the already-used window, 2024-10-16 → 2026-10-16
This comes from the full-span runs, so positions carried in from before the window, unlike v2's
standalone run.

| Variant | Return | Sharpe | Max DD |
|---|---|---|---|
| V0 | +6.4% | 0.29 | −16.8% |
| V3 | +14.0% | 0.50 | −16.0% |
| Nifty 50 | −7.3% | −0.23 | −15.6% |

Because this window motivated v3, it cannot count as evidence.

## Verdict
**India v3 (V3) passes the pre-registered bar:** selected in-sample, passed the untouched 2010–2019
holdout, and the walk-forward procedure passed.

- **Applied:** `strategy.vol_target: true` and `strategy.exclude_high_vol_pct: 0.2`.
- **Not applied:** `validated` is **left false**. Switching on real orders is the owner's decision.

Reasons for caution when making that decision:
1. Survivorship bias is larger before 2019 (today's constituents), and the holdout used momentum only.
2. The edge weakened after 2015.
3. The walk-forward drawdown passed by under 3 points.
4. The selection between V2 and V3 was a tie.

Recommended path: keep paper trading with v3 for at least 3 months and compare the paper results with
the backtest for the same days. If they agree, start live with a small budget.
