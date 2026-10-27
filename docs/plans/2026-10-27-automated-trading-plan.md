# Automated trading plan (India, Zerodha)

*Written 27 Oct 2026. Owner: the account holder. Status: planning; nothing here is live.*

The goal is to move TradeMare from paper trading to real orders, and then from "approve every
trade" to "trades automatically unless vetoed", without skipping the checks that make the results
trustworthy. Each phase has a gate: the next phase starts only when the gate is met.

## 1. Expectations

- The India strategy (v3: momentum plus earnings yield, monthly rebalance, volatility controls)
  passed a pre-registered history test: about **+20% a year (CAGR) with a worst drawdown of
  −24%** on 2010–2026 data, versus the Nifty's ~+9%. The typical month was +1.4%; the worst month
  was −15.9%; a +16% month happened once in 201 months.
- Live results are usually worse than backtests (costs, slippage, survivorship bias in the
  history, regime change). Plan for half the backtest return and the full drawdown.
- Automation is about discipline (every trade sized, every position with a stop, no skipped
  rules), not about making money faster.

## 2. Phases and gates

| Phase | What happens | Gate to move on |
|---|---|---|
| **0. Prepare** (now → ~2 weeks) | Owner decisions (section 9); build the must-have items (section 7); India back on the tested **monthly** rebalance | Must-have items merged and tested |
| **1. Paper validation** (≥ 8 weeks) | Paper trading exactly as it would trade live; owner approves signals on the dashboard as rehearsal | ≥ 2 monthly rebalances and ≥ 10 filled trades; Tracking page shows paper ≈ backtest replay (no unexplained trades); every stop-loss behaved as designed; no data-freshness incident left unhandled; owner decided ≥ 90% of signals before their deadline |
| **2. Infrastructure** (~1 week, can overlap phase 1) | Always-on server with a static IP; Zerodha app IP-whitelisted; alerts on the phone; backups off the machine | A dry run on the server: daily run, login, `broker check`, Telegram alert, restore from backup |
| **3. Supervised live** (4–8 weeks) | `broker: zerodha`, `validated: true`, budget = **half** the planned capital; every order still approved by the owner | Live fills within 0.3% of signal prices on average; no unexplained order; reconciliation clean every day; drawdown inside kill-switch limits |
| **4. Automated** | Auto-approve with a veto window (section 5); full planned capital | Ongoing: monthly review (section 8) |

Rollback at any phase: kill switch trips, an unexplained order, or a reconciliation mismatch →
stop new entries, return to the previous phase, find the cause before resuming.

## 3. Capital and sizing

| Item | Spec |
|---|---|
| Capital | Owner decides (section 9). **Minimum ₹50,000** so 10 positions are meaningful; below that, fees and whole-share rounding distort the strategy |
| Separation | TradeMare's `budget` is the only money it may use. Personal holdings in the same demat are never traded (verified: the desk only manages positions and orders it created) |
| Risk per trade | 1% of capital lost if the stop is hit (`risk_per_trade_pct: 1`) |
| Position cap | 15% of capital per stock; 30% per sector |
| Positions | Up to 10; half in a risk-off market; fewer when the index is unusually volatile |
| Cash buffer | 10% kept uninvested |
| Funds check | Before every order: free cash at Zerodha ≥ order value (currently ₹141 free, so money must be added or freed first) |

Costs per round trip on Zerodha delivery: no brokerage; STT 0.1% on buy and sell; stamp duty
0.015% on buy; exchange and SEBI fees; GST; and a depository charge of about ₹15.34 + GST per
stock sold per day. Roughly 0.25% plus ~₹18 per sale, i.e. about 0.6% on a ₹5,000 position.

## 4. Strategy and execution spec

| Area | Spec |
|---|---|
| Universe | Nifty 500, the 200 most-traded names on each date |
| Ranking | 12-1 month momentum + earnings yield, equal weight |
| Rebalance | **Monthly** (first trading day), the tested setting. Daily is untested; use it only after its own study passes |
| Buy rule | Top 10% of the ranking, until slots are full; skip the 20% most volatile stocks |
| Sell rule | Falls out of the top 30% at a rebalance; or its stop-loss is hit; or a red-flag filing (owner alerted) |
| Stop-loss | 3 × ATR (average daily range) below entry, fixed (not trailing); no profit target |
| Entry order | LIMIT at the signal price, CNC (delivery), DAY validity; sent as an after-market order (AMO) when approved outside market hours |
| Stop order | Zerodha GTT placed once the entry fills, child order LIMIT with a buffer below the trigger; if the price gaps past it, exit at the next open |
| Exit order | Market order **with market protection** (required for API market orders since April 2026; must be non-zero) or a marketable LIMIT |
| Data | Daily prices after the close (Yahoo); a session is never processed before its prices arrive; intraday stop checks every 15 minutes |
| Corporate actions | Splits and bonuses change share counts and prices; stops must be adjusted (verify before phase 3) |

## 5. Automation spec (phase 4)

**Approve by default, with a veto window.** Signals are created after the 5 pm run. The owner gets
them on Telegram and the dashboard with Approve / Reject buttons. Any signal not rejected by
**08:45 IST** the next trading day is approved automatically if it passes every guardrail:

- Order value ≤ position cap, total open orders ≤ free cash minus buffer.
- Price within ±5% of the last close (fat-finger check); quantity > 0; symbol in the universe.
- Not in NSE's ASM/GSM surveillance lists or trade-for-trade segment.
- No red flag in its filings; no results within 3 trading days (earnings can gap past a stop).
- Prices are current (no missing session) and health checks are clean.
- Daily limits: at most 6 new orders a day and at most 30% of capital committed in one day.
- Order rate far below SEBI's 10 orders per second threshold (TradeMare sends at most ~1 per second).

Anything failing a guardrail stays pending for the owner. Stops and exits are always automatic once
a position exists (they protect capital and must not wait for a person).

## 6. Operations

| Area | Spec |
|---|---|
| Hosting | Always-on Linux server in Mumbai (cloud VPS, ~₹500–1,500/month) or a home machine with an ISP static IP; the Mac sleeping makes runs late |
| Static IP | Required by SEBI for all API order placement (from April 2026), regardless of speed. Kite allows a primary and a backup IP; register both in the developer console |
| Daily login | API sessions end every day. Owner logs in each trading morning before 09:00 (`trademare broker login zerodha`); a Telegram reminder at 08:30 if not done; no orders are sent without a valid session. Credentials and TOTP stay with the owner (no automated password entry) |
| Alerts | Telegram: signals, fills, stops hit, kill switch, missed run, stale data, reconciliation mismatch, login missing |
| Reconciliation | Every evening: Zerodha's positions vs TradeMare's for the stocks it manages; any mismatch halts new entries and alerts |
| Backups | Nightly database backup (exists), copied off the server weekly |
| Security | Dashboard behind a password and a private network (e.g. Tailscale), never open to the internet; `.env` readable by the service user only |
| Audit | Every signal, decision, order and fill is logged (exists: audit log, trade journal) |

**Incident runbook**: broker API down → orders queue, alert, retry next session; login missed →
no new orders, stops (GTT) still live at Zerodha; order stuck or rejected → alert with the
broker's reason, no automatic retry beyond one; data late → session waits (exists); unexplained
position → halt and reconcile by hand.

## 7. Engineering backlog

| # | Item | Phase | Size |
|---|---|---|---|
| 1 | Zerodha: market protection on API market orders (or marketable limit exits) | 0 | S |
| 2 | Pre-trade checks: price band, order value, funds, surveillance lists, results-soon | 0 | M |
| 3 | Evening reconciliation job with halt-and-alert on mismatch | 0 | S |
| 4 | Login-missing reminder and order block; session status on the dashboard | 0 | S |
| 5 | Red-flag rules: ignore debenture (NCD) maturity "suspensions" (false alarm seen on SHRIRAMFIN) | 0 | S |
| 6 | Corporate-action handling for live stops (splits, bonuses) | 0 | M |
| 7 | Telegram Approve / Reject buttons | 1 | M |
| 8 | Server deployment hardening (private network, service user, off-site backups) | 2 | M |
| 9 | Auto-approve with veto window and guardrails (section 5), off by default | 3 | M |
| 10 | Optional: Sell button for personal holdings (owner-triggered, confirm screen) | any | M |

## 8. Monitoring and review

Weekly (5 minutes): Overview and Trade journal; any stop hit, any failed order.
Monthly (after each rebalance): return vs Nifty, drawdown, win rate, average loss per losing trade
(should be ≈ 1% of capital), slippage vs signal prices, fees as % of capital, tracking vs backtest.

**Stop conditions** (pause new entries and review): kill switch trips (3% in a day or 10% from
peak); live return trails the Nifty by more than 10 points over 6 months; two months in a row of
fills worse than 0.5% slippage; any unexplained trade.

## 9. Owner decisions

1. Capital for TradeMare: ₹____ (minimum ₹50,000 recommended), and where it comes from (new money
   or selling holdings, decided on their own merits).
2. Rebalance: back to **monthly** (tested), or keep daily on paper only while a study tests it.
3. Hosting: cloud server or home static IP.
4. Telegram alerts: set up the bot (`trademare notify telegram-setup`).
5. Automation level for phase 4: auto-approve with a veto window, or keep approving every trade.
6. Tax: delivery trades are taxed as capital gains (short-term 20% under 12 months, long-term
   12.5% above ₹1.25 lakh a year); frequent trading can be treated as business income. Confirm with
   a chartered accountant before phase 3, and keep Zerodha's tradebook for filing.

## Sources

- [Zerodha: SEBI algo trading changes, April 2026](https://inthemoneybyzerodha.substack.com/p/sebi-algo-trading-changes-april-2026)
- [FYERS: SEBI framework for retail algo trading from April 2026](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026)
- [Zerodha: Kite Connect plans](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs)
- India strategy results: `docs/research/2026-10-18-strategy-v3-plan.md`
