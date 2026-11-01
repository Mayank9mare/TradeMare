# Check: real NSE publication dates instead of the 45-day deadline (India)

*Written 1 Nov 2026, before any run. Results are appended below without editing this plan.*

## Question

TradeMare's India fundamentals come from Yahoo, which has no filing dates, so every quarter is
treated as public 45 days after it ends (the SEBI deadline; 60 days for the fiscal year). Most
companies publish two to six weeks earlier. `data.nse_results` replaces those estimates with the
real broadcast time from NSE's results filings (from the next day when broadcast after 17:00) and
adds quarters Yahoo does not have yet from the filings' XBRL figures.

This is more accurate, but it changes the inputs of the tested strategy (v3: 12-1 momentum plus
earnings yield, monthly rebalance). Does the tested strategy still behave the same way?

## Setup

- Two backtests, India, **2022-01-03 → 2026-10-30** (the span where Yahoo has Indian statements;
  before 2022 there is no earnings data, so the dates cannot matter).
- Both at the tested settings: `strategy.factor_rebalance=monthly` (the owner's daily choice is a
  separate, untested setting and is not part of this check).
- **A:** `data.nse_results=false` (as tested). **B:** `data.nse_results=true`.
- NSE filings are fetched once for every universe stock beforehand (dates for all quarters,
  figures for the latest two).

## Pass bar (fixed now)

B passes if all hold:
1. CAGR at least A's minus 1.0 percentage point;
2. maximum drawdown no more than 2.0 points worse than A's;
3. number of trades within ±25% of A's.

## What happens next

- **Pass:** `data.nse_results: true` becomes the committed default (rankings use real dates and
  the newest quarters), and the v3 description notes the change.
- **Fail:** the switch stays off and the rankings keep the 45-day estimate. The post-results
  refresh and the brief's `latest_results` (information only) are unaffected either way.

## Caveats

- The check covers about five years and one strategy; it shows whether the change is harmless,
  not that it adds an edge.
- Figures from NSE exist only for the latest quarters, so the "new quarter" part of the switch is
  exercised only at the end of the span; the date part is exercised throughout.
