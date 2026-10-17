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

---

# Results (appended after the runs; the plan above was committed before any test)

## Step 1: factor selection, in-sample 2019-10-15 → 2024-10-15, 63-session horizon

| Market | Factor | Mean IC | t-stat | Selected |
|---|---|---|---|---|
| US | mom_12_1 | −0.018 | −0.7 | no |
| US | high_52w | −0.061 | −2.1 | no (wrong sign) |
| US | low_vol | −0.097 | −2.8 | no (wrong sign) |
| US | gross_profitability | +0.014 | +0.7 | no |
| US | earnings_yield | +0.008 | +0.5 | no |
| IN | **mom_12_1** | **+0.081** | **+3.2** | **yes** |
| IN | high_52w | −0.009 | −0.3 | no |
| IN | low_vol | −0.093 | −2.9 | no (wrong sign) |
| IN | gross_profitability | −0.193 | −3.6 | no (wrong sign; 21% coverage) |
| IN | **earnings_yield** | **+0.074** | **+2.2** | **yes** (26% coverage, 2023–24 only) |

**US: no factor qualifies, so no trading strategy (signals only).** In the US, high-volatility, beaten-down stocks outperformed in 2019–24. Flipping signs after seeing that would be post-hoc and is not done.

## Step 2: India in-sample variants (current Nifty 200)

| risk_off_exposure | max positions | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| 1.0 | 10 | +46.0% | 1.72 | −41.8% |
| 1.0 | 20 | +55.7% | 1.83 | −39.2% |
| **0.5** | **10** | **+58.3%** | **2.18** | **−22.5%** ← chosen (best Sharpe) |
| 0.5 | 20 | +43.4% | 1.72 | −30.3% |
| Nifty 50 | | +17.0% | 0.94 | −38.4% |

## Robustness check (NOT pre-registered; added because the numbers were implausible)
The top 5 trades produced 82% of realised P&L, and several were small or mid caps that joined the Nifty 200 *after* their rallies (CG Power, Apar, Kalyan Jewellers, Dixon). That is survivorship bias.

The stricter universe: current Nifty 500 names that were among the **200 most-traded on each date** (`universe_top_n: 200`). This can only make the test harder; it adds no new choices.
- Factor study: mom_12_1 IC +0.059 (t +2.3), earnings_yield +0.092 (t +2.6). **Both still qualify.**
- Chosen variant, in-sample: **CAGR +28.1% vs +17.0%, Sharpe 1.37 vs 0.94, max DD −33.0% vs −38.4%.** Roughly half of the apparent outperformance was survivorship bias.

## Step 3: the single out-of-sample run, 2024-10-16 → 2026-10-16

| | Strategy (point-in-time universe, decisive) | Strategy (Nifty 200, reference) | Nifty 50 |
|---|---|---|---|
| Total return | −6.5% | +13.7% | −7.3% |
| Sharpe | −0.17 | +0.46 | −0.23 |
| Max drawdown | −24.1% | −22.1% | −15.6% |

Pass bar: Sharpe ≥ index (−0.17 ≥ −0.23, **passes**) and max DD within 5 points of the index (−24.1% vs −15.6% is 8.5 points worse, **fails**).

## Verdict
**Strategy v2 fails the pre-registered bar** and must not be traded with real money. It is not re-tuned on the out-of-sample period.

India momentum is the only effect with in-sample support that survives the survivorship correction. It becomes the **paper-trading candidate**: forward paper trading is the next genuine out-of-sample test and costs nothing. Both markets are marked `validated: false`, which blocks live orders.


---

# 2026-10-17 amendment: earnings yield uses recurring earnings

**Why:** the first live paper scan ranked Vodafone Idea (IDEA) #2 of 183 on earnings yield (27.4%). Its March 2026 quarter included a one-off gain of about ₹57,000 Cr ("Total Unusual Items"), while every other quarter was a loss. Tata Motors PV (TMPV) topped the list (75%) because of demerger accounting. TTM net income including one-offs made both look cheap.

**Change:** `earnings_yield` (and P/E) now use yfinance's **Normalized Income** (net income excluding unusual items) and fall back to reported net income where it is missing. 100% of cached Indian periods with net income also have normalized income. EDGAR (US) has no clean equivalent tag, so US still uses reported income. Loss-makers stay negative and rank last.

**Re-selection, in-sample only** (2019-10-15 → 2024-10-15, 63-session horizon, monthly sampling, Nifty 500 top-200 point-in-time universe). No out-of-sample data was run or inspected for this decision.

| Factor | Before (reported income) | After (normalized income) | Selected |
|---|---|---|---|
| mom_12_1 | +0.059 (t +2.3) | +0.059 (t +2.3), unchanged | yes |
| earnings_yield | +0.092 (t +2.6) | **+0.077 (t +2.1)** | yes, barely clears t ≥ 2.0 |

Part of the earlier earnings-yield strength came from one-off gains. It still meets the pre-registered rule, so India keeps `factors: [mom_12_1, earnings_yield]`. Its evidence is thin: 24% coverage, 2023–24 only.

**Sanity check (2026-10-16):** IDEA's earnings yield is −16.2%, ranked #182 of 184 (was #2). The top 5 are now HINDPETRO, PFC, IOC, RECLTD and CANBK.
