# TradeMare — Build Plan

An engine that analyses stocks using fundamentals, technicals and sentiment. It produces swing-trade buy/sell signals inside a fixed budget and sends them to a web dashboard and by email. A trade is placed only after you approve it.

**Scope decisions (2026-10-13)**
- Markets: India (NSE/BSE) and the US (stocks/ETFs). Each market has its own budget, kept in its own currency.
- Autonomy: the engine sends a signal and you approve it before any order is placed. Fully automatic trading may come later.
- Style: swing trading, holding positions for days to weeks. Daily bars, with analysis run after the market closes.
- Alerts: a web dashboard plus email.
- **Everything is set in config:** budgets, stop-loss rules, risk limits, weights and long/short permissions live in `config/settings.yaml`. They can be changed without touching code, and later from the dashboard.
- **Brokers:** India uses Zerodha and Dhan behind one `BrokerAdapter` interface (strategy pattern), so either can be selected in config. The US uses Alpaca.
- **Runtime:** it runs on your Mac first, then moves to a VPS.
- **LLM:** optional and not part of the first build. It sits behind a provider-agnostic `LLMClient` interface, so Claude or ChatGPT can be plugged in later.
- **Direction:** the engine supports both long and short positions. Whether shorts are allowed is a config switch for each market, and it is off by default.

---

## 1. Architecture

```
            ┌──────────────── Scheduler (per-market calendar) ────────────────┐
            ▼                                                                  │
 Data layer ──► Analysts ──────────────► Signal aggregator ──► Risk & budget ──► Signal store
 (prices,      • Fundamental (weekly)     (weighted score,     (sizing, caps,      │
  fundamentals,• Technical (daily)         confidence,          kill switch)       ▼
  news)        • Sentiment / LLM (daily)   rationale)                       Notifier (email)
                                                                            Dashboard (approve/reject)
                                                                                   │ approved
                                                                                   ▼
                                                        Executor ──► BrokerAdapter (Alpaca | Zerodha | PaperBroker)
                                                                                   │
                                                              Journal / P&L / audit log ◄──┘
 Backtester ── reuses the same analysts, aggregator and risk modules on historical data
```

**Core principle:** the backtester, paper trading and live trading all run the same strategy, aggregator and risk code. Only the data feed and the broker adapter are swapped. This prevents the common situation where a strategy does well in a backtest but behaves differently when live.

## 2. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language/env | Python 3.12 managed with `uv` | Some quant libraries don't support 3.14 yet |
| Data frames | pandas, plus polars if speed becomes a problem | |
| Indicators | `pandas-ta` or `ta`, plus our own small helpers | |
| Storage | SQLite + SQLAlchemy at first, Postgres later | One user, simple to run |
| Scheduler | APScheduler + `exchange_calendars` | Uses the NSE and NYSE holiday calendars |
| API + dashboard | FastAPI + Jinja/HTMX | One process with no separate SPA build; approve/reject buttons are simple |
| Email | SMTP (Gmail app password) or Resend | |
| LLM (sentiment) | Claude API, results cached for each ticker and day | Optional and runs behind a cost cap |
| Tests | pytest, with recorded data fixtures | |
| Deploy | Small VPS with a **static IP** | Required for Indian broker APIs under SEBI rules |

## 3. Markets, brokers & data

| | US | India |
|---|---|---|
| Broker | Alpaca (`alpaca-py`). Free paper account, no commission on stocks | **Zerodha** (`kiteconnect`) and **Dhan** (`dhanhq`), both implementing `BrokerAdapter`; config picks the active one |
| Paper trading | Alpaca's own paper account | No broker sandbox, so we build an internal `PaperBroker` simulator |
| Prices (EOD) | Alpaca bars, falling back to yfinance | Kite historical data or NSE bhavcopy, falling back to yfinance (`.NS`) |
| Fundamentals | **SEC EDGAR `companyfacts`**: free, point-in-time via filing dates, full history. yfinance is the fallback. | **yfinance** for now: about 4 years annual and about 5 quarters, with availability estimated from SEBI deadlines (Q +45 days, annual +60 days). Can move to a paid API later if it is too thin. |
| News | Alpaca news API, RSS | RSS from Moneycontrol, ET and exchange announcements |
| Universe | S&P 500 or a liquid subset | Nifty 200 or another liquid subset |

**Short selling:**
- **US:** Alpaca needs a margin account (at least $2,000 equity), and the stock must be easy to borrow. The adapter checks `shortable` before placing an order.
- **India:** in the cash segment, short positions must be closed the same day, so a swing short can't be held there. Holding a short overnight means using **stock futures (F&O)**, which brings lot sizes, margin and contract rollover. The other route is SLB, which is rarely used.
- **Decision:** the engine is built to handle both directions from the start. India swing shorts are routed to futures through a later `instrument: FUT` option, and until then shorting in India is disabled by config.

**Compliance (India):** the SEBI retail algo framework requires API orders to come from a broker-whitelisted static IP. Strategies that send orders above an exchange-set rate must be registered, and selling signals or algos to others requires a SEBI RA license. TradeMare is for **personal use only**. Check the current enforcement status and the broker's API pricing before phase 6.

## 4. Analysis modules

All analysts implement one interface: `analyze(ticker, asof) -> AnalystResult(score ∈ [-1, 1], confidence ∈ [0, 1], reasons: list[str])`. The `asof` argument guards against look-ahead bias: an analyst can only use data that was available on that date.

**Fundamental analyst** (recomputed weekly or after earnings). It acts as a quality and value gate.
- Quality: ROE, ROCE, operating margin trend, Piotroski F-score
- Health: debt/equity, interest coverage, current ratio
- Growth: revenue and EPS CAGR over 3 years, latest quarter vs the same quarter last year
- Value: P/E and EV/EBITDA compared with sector peers and the stock's own history
- Output: a percentile within the sector, turned into a score. Stocks below a quality threshold are never bought.

**Technical analyst** (daily). It decides entry and exit timing.
- Trend: price vs the 50 and 200 SMA, ADX
- Momentum: RSI, MACD, and 3/6-month relative strength vs the index
- Setups: pullbacks within an uptrend, breakouts on high volume, and 52-week highs
- Volatility: ATR, used for stops and position sizing
- Market regime filter: index above its 200-day moving average (risk-on) or below it (risk-off). In risk-off, new longs get less weight.

**Sentiment analyst** (daily, optional, added later). It adjusts a signal or vetoes it.
- Recent headlines and announcements, summarised by an LLM into a score and a short reason
- Hard veto on red flags: fraud, auditor resignation, an earnings date inside the holding window, and so on

**Aggregator**
- `composite = w_f·F + w_t·T + w_s·S`, keeping only candidates that pass the fundamental gate. Starting weights are F 0.35, T 0.5, S 0.15. They live in config and are tuned in backtests.
- Output: a `Signal` with side, ticker, entry (a limit price), stop, target, size, expiry, score, confidence, and a readable rationale from each analyst.

## 5. Risk & budget manager

All values below are **defaults in `settings.yaml`** that can be changed for each market. Example:

```yaml
markets:
  IN:
    enabled: true
    broker: zerodha            # zerodha | dhan | paper
    budget: 200000             # INR, hard cap on deployed capital
    cash_buffer_pct: 10
    allow_long: true
    allow_short: false         # needs F&O routing for swing shorts
    risk_per_trade_pct: 1.0
    max_position_pct: 15
    max_open_positions: 8
    max_sector_pct: 30
    stop_loss:
      method: atr              # atr | percent | swing_low | fixed_amount
      atr_multiple: 2.0
      percent: 5.0
      trailing: true
      trail_method: atr        # atr | percent
      trail_value: 2.5
    take_profit:
      method: r_multiple       # r_multiple | percent | none
      value: 2.0
    time_stop_days: 15
    kill_switch:
      daily_loss_pct: 3
      max_drawdown_pct: 10
  US:
    enabled: true
    broker: alpaca             # alpaca | paper
    budget: 2000               # USD
    ...
```

When approving a signal, you can override its stop, target and quantity on the dashboard.

Per market (each in its own currency):
- **Budget:** an overall capital cap the engine can never exceed, plus a cash buffer of e.g. 10%.
- **Per-trade risk:** 1% of the budget. Position size = risk ÷ (entry − ATR-based stop). The result is capped at 15% of the budget per position and at what the stock's liquidity can support.
- **Portfolio caps:** at most N open positions (e.g. 8), at most 30% in one sector, and no averaging down.
- **Exits:** stop-loss, target or trailing stop, a time stop after N days without progress, and an exit when the technical or fundamental score turns negative.
- **Kill switch:** new entries stop if daily losses exceed 3% of the budget, or if drawdown from the peak exceeds 10%. Manual re-arming is needed to resume.
- **Signal expiry:** a signal is created after the close and is valid until the next session ends. Signals that weren't approved expire.
- **Costs:** India's brokerage, STT, stamp duty and GST, and US fees, are included in backtests and journal P&L.

## 6. Signal → approval → execution flow

1. After the market closes, the pipeline runs and saves signals with status `PENDING`.
2. Email digest: a list of signals, each with its rationale, size, stop and target, and a link to the dashboard.
3. Dashboard: each signal has Approve, Reject or Edit (change quantity or limit price). Authentication is a single-user login.
4. On approval, the Executor places a limit entry order. After it fills, the stop is set: a bracket/OCO order on Alpaca, a GTT on Zerodha.
5. The order is reconciled against the broker, and all fills and P&L go into the journal. Exit signals use the same approve flow, though the stop orders already sit at the broker.
6. Every decision is logged in an audit log: inputs, scores, who approved it, and when.

## 7. Project layout

```
trademare/
  config/            settings.yaml (budgets, weights, risk limits, universe), .env (secrets)
  core/              models (Signal, Order, Position), interfaces, clock/calendars
  data/              providers/ (alpaca, yfinance, edgar, kite, nse), cache, storage
  analysis/          fundamental.py, technical.py, sentiment.py, regime.py
  strategy/          aggregator.py, screener.py
  risk/              sizing.py, limits.py, kill_switch.py
  execution/         brokers/ (base, alpaca, zerodha, paper), executor.py, reconciler.py
  backtest/          engine.py, metrics.py, reports
  notify/            email.py
  web/               FastAPI app, templates (dashboard, signals, portfolio, backtests)
  scheduler.py       per-market jobs
tests/
```

## 8. Phased roadmap

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0. Scaffold** ✅ | uv project, config, models, logging, tests, CI lint | `pytest` passes and settings load |
| **1. Data layer** ✅ | Price and fundamental providers for US and India, local cache, universe lists | 5 years of daily bars and fundamentals stored for both universes |
| **2. Analysts** | Technical and fundamental analysts, regime filter, aggregator | Signals for any `asof` date with readable reasons |
| **3. Backtester** | Daily event loop reusing the phase 2 code, costs, metrics (CAGR, Sharpe, max drawdown, win rate), walk-forward split | A report for each market, beating buy-and-hold of the index on a risk-adjusted basis, out of sample |
| **4. Risk & budget** | Sizing, caps, kill switch, expiry, plus their effect in backtests | Hard limits are never breached (property tests) |
| **5. Signals live (no orders)** 🔄 dashboard foundation built early (data views) | Scheduler, email digest, dashboard listing signals and positions | Two weeks of daily emails delivered correctly |
| **6. Paper execution** | Approve flow → Alpaca paper and the internal PaperBroker for India, reconciliation, journal | 4–6 weeks of paper trading tracking the backtest |
| **7. Live, small** | Zerodha and Alpaca live adapters, static IP VPS, a small budget | Clean reconciliation and no limit breaches for a month |
| **8. Sentiment/LLM** | News ingestion and LLM sentiment with veto, plus an A/B test of its effect in paper trading | Kept only if it improves results |
| Later | Optional automatic execution within limits, more strategies, ML ranking, tax/P&L export | |

## 9. Known risks / pitfalls
- **Look-ahead and survivorship bias:** free fundamental data is not point-in-time, and current index membership leaves out companies that were delisted. Lag fundamentals by their reporting date and be sceptical of backtest results.
- **Overfitting:** use walk-forward validation and few parameters, and test on data the strategy was never tuned on.
- **India fundamentals data is thin.** yfinance has only about 4 years of annual data and a few quarters, and some quarters are missing for banks. Fundamental backtests in India are limited to recent years until we add a richer source.
- **Broker API changes, token expiry:** Kite access tokens must be refreshed every day. Plan the login flow for this.
- **This is not financial advice.** Start with paper trading and small capital.

## 10. Decisions log & open items
- ✅ Budget, stop-loss and risk settings are all configurable (`settings.yaml`, later editable from the dashboard).
- ✅ India brokers are Zerodha and Dhan behind `BrokerAdapter`; the US broker is Alpaca. Each also has a `PaperBroker`.
- ✅ It runs on your Mac first and is deployed to a VPS later. Note that a home IP is usually dynamic, so **live Indian API orders will probably need the VPS** with a static IP. Paper trading and signals work fine on the Mac.
- ✅ LLM: the `LLMClient` interface supports Claude or OpenAI. It is disabled for now.
- ✅ Long and short are both supported, controlled by `allow_short` per market (off by default).
- ✅ Fundamentals: EDGAR for the US (point-in-time) and yfinance for India (availability estimated from SEBI deadlines). Both are swappable through config (`fundamentals_source`).
- ⏳ Final budget numbers will be filled into the config when the time comes.
