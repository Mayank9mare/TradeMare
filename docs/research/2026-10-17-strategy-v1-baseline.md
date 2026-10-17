# Strategy v1 baseline: backtest and signal study (2026-10-17)

**Verdict: the backtester works; strategy v1 has no statistically reliable edge. Do not trade it with real money.**

## Setup
- Period: 2021-10-18 → 2026-10-16 (5 years). Out-of-sample = last 40% (from 2024-10-16). Nothing was tuned on it.
- Universes: current Nifty 200 / S&P 500 constituents. This is **survivorship-biased** and flatters results, especially in India.
- Test capital: ₹10 lakh and $100k. The configured ₹1 lakh / $1k budgets force whole-share rounding, which distorts sizing.
- Settings: as in `config/settings.yaml` at commit time (ATR 2x stop, 2R target, ATR trailing stop, 15-session time stop, 1% risk per trade, long only).
- Costs: statutory Indian delivery charges (STT, stamp duty, exchange, SEBI, GST, DP) and US SEC/TAF fees, plus 5 bps slippage.

## Backtests (`trademare backtest`)

| | US strategy | S&P 500 | India strategy | Nifty 50 |
|---|---|---|---|---|
| CAGR | +7.2% | +11.3% | +14.1% | +4.6% |
| Sharpe | 0.51 | 0.72 | 1.06 | 0.40 |
| Max drawdown | −23.1% | −25.4% | −20.6% | −17.2% |
| Out-of-sample CAGR | +8.1% | +14.5% | −2.5% | −3.3% |
| Trades / win rate / profit factor | 936 / 42% / 1.13 | | 609 / 45% / 1.30 | |
| Costs paid | $360 | | ₹2.83 lakh (≈5.7% of capital per year) | |

Index benchmarks are price indices (no dividends), which slightly flatters both strategies.

## Signal study (`trademare research`)
This measures the rank correlation (IC) between each score and forward returns across the universe, independent of the trading rules.

| | 20-session IC (t-stat) | 60-session IC (t-stat) |
|---|---|---|
| US composite | +0.001 (0.1) | −0.008 (−0.6) |
| US technical | +0.003 (0.2) | −0.003 (−0.2) |
| US fundamental | −0.002 (−0.5) | −0.007 (−1.3) |
| India composite | +0.008 (0.9) | +0.011 (0.8) |
| India technical | +0.010 (1.1) | +0.015 (1.2) |
| India fundamental | −0.002 (−0.3) | −0.002 (−0.2) |

A t-stat below about 2 means the signal is not distinguishable from noise. No score clears that bar. India technical was positive in 2022–24 and negative in 2025–26, which matches the backtest's in-sample vs out-of-sample swing. In the US, the *lowest* fundamental quintile outperformed at 60 sessions (+1.34% excess per quarter), reflecting the 2023–26 low-quality/speculative rally.

## Takeaways
1. The India backtest gain is mostly the 2023–24 small/mid-cap rally plus survivorship bias, not a robust edge.
2. Swing holding periods of about 10 sessions generate high turnover. In India, STT makes that expensive (about 5–6% of capital per year).
3. Fundamentals add nothing at a 1–3 month horizon in this form.
4. Changing thresholds and re-running until the numbers look good would only overfit. Any change must be motivated beforehand, judged on the in-sample period, and then confirmed once out of sample.

## Candidate next experiments (not yet run)
- Use documented factors instead of hand-made composites: 12-1 month momentum, gross profitability, low volatility. Test them in `trademare research` first.
- Use a longer holding period / monthly rebalance to cut turnover and costs.
- Use point-in-time index membership to remove survivorship bias, which needs historical constituent lists.
- Use regime-only exposure control: trade only in risk-on regimes, since the regime filter showed sensible behaviour.
