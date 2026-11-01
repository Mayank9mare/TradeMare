# Book setups: selectable chart strategies (analysis and paper trading)

*Written 1 Nov 2026. Source: Aseem Singhal, "51 Trading Strategies" (Zebralearn, 2023). Status: plan.*

## Goal

Let the owner pick one or more chart setups from the book, see which stocks match each day, see
each setup's track record on TradeMare's own price history, and optionally let the chosen setups
create paper signals that go through the usual approval, risk and pre-trade checks.

The tested India strategy (v3: momentum + earnings yield, monthly rebalance) is unchanged. Book
setups run as a separate **sleeve** with its own share of the budget and its own position limit,
and every signal and position records which strategy opened it, so each strategy exits by its own
rules. Setups are off by default.

## Which setups, and why these

Only setups that work on daily bars, buy-only (Indian cash trading does not allow overnight
shorts), and can be written as exact rules. The book gives no tested rules or win rates, so every
threshold below is our reading of its text, fixed here before any results are seen.

Common terms: *ATR* = 14-day average true range; *vol50* = 50-day average volume (excluding
today); a *signal day* is the close on which all conditions hold; **entry** is a buy-stop at the
signal day's high, valid the next session only (the book's "buy above the high of the candle").

| id | Book | Family | Signal (all on the daily close) | Stop | Target / exit |
|---|---|---|---|---|---|
| `supertrend_rsi` | 4.2 | Trend | Close above Supertrend(10, 3) in an up trend and RSI(14) above 60, both holding today for the first time (in either order) | Supertrend line | No target; stop trails the Supertrend; exit on a close below it |
| `ichimoku` | 1.7 | Trend | Conversion line (9) above base line (26) and the close above the cloud top, both holding today for the first time | Leading span A (cloud edge) | 3R |
| `sector_rs` | 4.3 | Trend | The stock's sector (equal-weight of universe members) 63-day return turns from below to above the Nifty's; buy that sector's most-traded stock | 3 ATR | Exit when the sector's 63-day return falls back below the Nifty's |
| `rally_base_breakout` | 7.2 | Breakout | Rise of ≥ 15% in the 60 sessions before a 20-session base whose high-low range is ≤ 15%; base volume < 0.8 x vol50; today closes above the base high on ≥ 1.5 x vol50 with a body ≥ 60% of the day's range | Base low, but at least 5% below entry | 3R |
| `vcp` | 7.6 | Breakout | Uptrend (close > SMA50 > SMA200); three consecutive 15-session windows with shrinking high-low ranges, the last ≤ 10%, and falling average volume; today closes above the 45-session high on ≥ 1.5 x vol50 | Low of the last window | 3R |
| `bb_squeeze` | 1.6 | Breakout | Bollinger(20, 2) width in the lowest 20% of the last 126 sessions at some point in the previous 5; today closes above the upper band and the width is rising | Middle band | No target; stop trails the middle band; exit on a close below it |
| `triangle_breakout` | 1.4 | Breakout | Close > SMA50; over the previous 15 sessions highs slope down and lows slope up (linear fit) with falling volume; today closes above that window's high on ≥ 1.5 x vol50 | Window low | 2R |
| `breakout_retest` | 7.4 | Pullback | A close above the prior 40-session high happened 3-15 sessions ago (later higher closes do not reset the level while the setup is live); since then the low came within 2% of that breakout level without closing more than 2% below it; today closes above yesterday's high | Lowest low since the breakout | 3R |
| `williams_pullback` | 1.2 | Pullback | Close > SMA14; Williams %R(14) was above -20 in the last 10 sessions, dipped to -50 or lower, and crosses back above -50 today | 10-session low | 2R |
| `macd_fib` | 1.3 | Pullback | Close > SMA50; price pulled back into the 38.2-61.8% retracement of the move from the 60-session low to the 20-session high; MACD(12, 26, 9) crosses above its signal today on a green candle | Lowest low of the pullback | The 20-session high, or 2R if that is further |
| `rsi_w` | 4.4 | Reversal | RSI(14) "W": a dip below 30, a rise above 30, a second dip that stays above the first dip's RSI low, and a cross back above 30 today, all within 25 sessions | Lowest low of the pattern | Exit when RSI reaches 70 |
| `pin_bar_range` | 7.3 | Reversal | A 20-session range ≤ 10% after a ≥ 15% rise; today is a bullish pin bar (lower wick ≥ 2 x body, upper wick ≤ body, close in the top third) with its low within 1.5% of the range low; first or second such pin in the range only | Pin bar low | Range high |
| `repo_cut` | 7.5 | Macro | The RBI cuts the repo rate (decision dates from a checked reference file) | None (the book uses none); 10% emergency stop | Exit after 21 sessions. Traded through the Nifty ETF (NIFTYBEES); the book's "short on hikes" half is not possible in cash |

Not included: intraday (chapter 2), scalping (5) and option (6) strategies, which need intraday
data or derivatives; Dow theory, Smart Money, Elliott waves, fractals, Renko and Gann (chapter 3),
which are too subjective to code faithfully; the Martingale system (5.6), which doubles risk after
losses.

All setups also require liquidity (20-day traded value ≥ the market's `min_avg_traded_value`) and
use TradeMare's universe; positions also exit after 60 sessions if nothing else has closed them.

## Track record (per setup, on our own data)

An event study over the cached Nifty 500 history: each signal becomes one simulated trade with the
book's entry, stop, target and exit, filled from daily bars (gaps through a stop fill at the open),
with India delivery costs (about 0.25% per round trip plus slippage). Reported per setup and per
period (2010-2019, and 2020 onwards as the later check):

trades, win rate, average and median gain per trade, average R, profit factor, median days held,
worst trade, and yearly counts. Overlapping trades in one stock are skipped (one at a time).

**Evidence bar** for a setup to create paper signals when `require_record: true` (the default):
at least 100 trades in each period, positive average gain after costs in both, and profit factor
≥ 1.1 in the later period. The bar is fixed here; it is not tuned after looking.

## Trading the setups (paper)

- `markets.<M>.setups`: `enabled` (default false), `selected` (setup ids), `budget_pct` (share of
  the market budget for setup trades, default 30), `max_positions` (default 3), `require_record`.
- Daily run, after the core strategy: each selected setup that meets the bar proposes entries for
  that day's signals (not held, not pending, no results within the event window, not vetoed by
  red flags). Sizing uses the market's risk per trade, inside the sleeve's budget and the shared
  sector and cash limits. Ranked by the setup's own strength measure, then traded value.
- Each signal and position stores `strategy` (`null` = core strategy, else `setup:<id>`). The core
  strategy's rebalance never sells setup positions and does not count them against its slots; setup
  positions exit by their setup's rule (exit signals, approved like any other) and by their stops,
  which trail where the setup says so.
- Signal cards show the setup, its book reference and its track record. Pre-trade checks, the kill
  switch and live-order blocking (`validated`) apply unchanged.

## Phases

1. Indicators (Supertrend, Bollinger, Ichimoku, Williams %R) and the setup library, with tests.
2. Track-record study, `trademare setups` command, and a Setups page (pick setups, today's
   matches, records).
3. Trading: config, strategy tag on signals and positions, sleeve sizing and exits, Settings and
   signal-card changes.
4. Brain skill and docs.

## Amendments before any results

- 1 Nov 2026, while writing unit tests and before any study run: `supertrend_rsi` and `ichimoku`
  fire on the first day both conditions hold rather than requiring both to change on the same day
  (the book's charts show either order); `breakout_retest` keeps the first breakout level while the
  setup is live, so the higher closes right after a breakout do not move it.

- 1 Nov 2026, after the first study run (execution only, no rule changed): live entries are
  stop-limit orders with a limit 1% above the trigger (Zerodha has no plain stop-market entry for
  this use), so the study now skips trades whose next open gaps more than 1% above the trigger
  instead of filling them at the open. Results before and after are in the results section.

## What would change this plan

A setup that fails the evidence bar stays analysis-only unless the owner turns `require_record`
off for that market, which the Settings page labels as trading an untested strategy.

## Results (appended 1 Nov 2026; India, Nifty 500 list, data through 30 Oct 2026)

Final run (with the stop-limit fill amendment). "First run" is the verdict before that amendment.
Average gain per trade is after costs; "Nifty same days" is the index's return over each trade's
days (before costs), the honest comparison for a setup's edge.

| Setup | Book | 2010-19 trades | won | avg/trade | Nifty same days | 2020+ trades | won | avg/trade | Nifty same days | PF 2020+ | Bar | First run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Supertrend + RSI | 4.2 | 4729 | 35% | +0.17% | +1.15% | 4591 | 39% | +1.79% | +1.38% | 1.45 | pass | pass |
| Ichimoku cloud | 1.7 | 3989 | 31% | -0.01% | +0.81% | 3694 | 36% | +1.36% | +1.23% | 1.38 | fail | pass |
| Sector relative strength | 4.3 | 458 | 42% | +0.10% | +0.69% | 316 | 38% | +0.20% | +0.98% | 1.08 | fail | pass |
| Rally, quiet base, breakout | 7.2 | 306 | 45% | +0.52% | +1.72% | 382 | 47% | +3.40% | +1.73% | 1.59 | pass | pass |
| Volatility contraction (VCP) | 7.6 | 167 | 46% | +1.83% | +1.05% | 168 | 45% | +2.51% | +1.34% | 1.49 | pass | pass |
| Bollinger squeeze breakout | 1.6 | 3567 | 31% | -0.33% | +0.53% | 3534 | 33% | +0.55% | +0.71% | 1.20 | fail | fail |
| Triangle breakout | 1.4 | 134 | 51% | +2.99% | +2.16% | 117 | 47% | +1.61% | +2.00% | 1.27 | pass | pass |
| Breakout and retest | 7.4 | 2813 | 32% | -0.04% | +0.73% | 2692 | 38% | +1.78% | +1.30% | 1.45 | fail | pass |
| Williams %R pullback | 1.2 | 3691 | 38% | +0.01% | +0.81% | 3459 | 42% | +0.99% | +1.08% | 1.25 | pass | fail |
| MACD + Fibonacci pullback | 1.3 | 892 | 39% | +0.10% | +0.82% | 852 | 42% | +1.11% | +1.16% | 1.24 | pass | pass |
| RSI 'W' bounce | 4.4 | 619 | 27% | -0.04% | +1.65% | 494 | 27% | +1.00% | +1.70% | 1.24 | fail | fail |
| Pin bar at range bottom | 7.3 | 102 | 32% | -0.43% | +0.41% | 172 | 31% | -0.42% | +0.26% | 0.76 | fail | fail |

What this says:

- **Against the evidence bar**: 6 of 12 pass. The verdicts of four setups changed with a small
  execution detail (how gaps above the trigger fill), so for setups near zero before 2020 the
  pass/fail line is close to noise.
- **Against simply holding the Nifty over the same days** (not part of the bar, shown on the
  page): only the volatility contraction pattern (VCP) did better in both periods. The triangle
  breakout did better in 2010-2019 only; the rally-base breakout, Supertrend + RSI and Ichimoku in
  2020 onwards only. Every other setup earned less than the index in both periods.
- 2020 onwards was a strong market for Indian mid-caps and the universe is today's index list, so
  the later column flatters every setup.

The bar is unchanged. Setups that pass may trade on paper if the owner turns them on; the page
shows the Nifty comparison next to each so the choice is informed.
