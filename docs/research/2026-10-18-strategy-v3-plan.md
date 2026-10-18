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
