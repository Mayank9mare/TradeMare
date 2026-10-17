# Strategy v2: pre-registered research plan (written before any test was run)

v1 showed no edge (see `2026-10-17-strategy-v1-baseline.md`). This file fixes the hypotheses, the
selection rule and the procedure **before** looking at any results, so the outcome cannot be tuned
into existence. Results are appended at the end without editing this plan.

## Candidate factors (documented in academic literature)
| Factor | Definition | Expected sign | Reference |
|---|---|---|---|
| `mom_12_1` | Return from t−252 to t−21 sessions (skips the last month) | + | Jegadeesh & Titman (1993) |
| `high_52w` | Close / 52-week high | + | George & Hwang (2004) |
| `low_vol` | −(annualised volatility of daily returns over 252 sessions) | + | Ang et al. (2006); Frazzini & Pedersen (2014) |
| `gross_profitability` | TTM gross profit / total assets | + | Novy-Marx (2013) |
| `earnings_yield` | TTM net income / market cap | + | Basu (1977) |
| `v1_technical` | v1 technical score (reference only) | + | — |

## Periods
- **In-sample (selection and tuning):** 2019-10-15 → 2024-10-15. This includes COVID and the 2022 bear market.
- **Out-of-sample (one confirmation run only):** 2024-10-16 → 2026-10-16. This is the same split as the v1 report.
- India fundamentals start around 2022, so `gross_profitability` and `earnings_yield` are only tested for India where data exists. Coverage will be reported.

## Step 1: factor selection (in-sample only)
`trademare research factors`, monthly sampling (every 21 sessions), horizons of 21 and 63 sessions, liquid stocks only.
A factor is **selected** for a market if, at the 63-session horizon:
1. mean IC > 0 (the expected sign), and
2. IC t-stat ≥ 2.0.

If no factor qualifies for a market, that market stays in signals-only mode with no trading recommendation.

## Step 2: strategy (in-sample backtests, at most 4 variants per market)
- **Factor mode:** each selected factor is ranked across the universe, and the percentile ranks are averaged equally.
- **Monthly rebalance:** buy names in the top 10% (up to `max_open_positions`) and sell names that drop out of the top 30%.
- **Protective stop:** 3× ATR, no profit target, no time stop. Same costs, slippage and sizing rules as v1.
- **Variants:**
  - (a) no regime filter vs (b) half exposure in risk-off.
  - × `max_open_positions` 10 vs 20.
- The variant with the best in-sample Sharpe is chosen.

## Step 3: one out-of-sample run
The chosen variant is run once on 2024-10-16 → 2026-10-16. **Pass bar:** out-of-sample Sharpe ≥ the index's, and max drawdown no worse than the index's by more than 5 points. The result is reported whether it passes or fails. A failed strategy is not re-tuned on out-of-sample data.

## Known limitations
- Universe = current constituents (survivorship bias), which particularly flatters momentum.
- Five years per period is short, so t-stats are noisy.
- Price indices used as benchmarks exclude dividends.
