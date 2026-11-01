# Intraday module: research and paper trading (independent of the daily system)

*Written 1 Nov 2026. Status: plan. Paper only: this module never places real orders.*

## Goal

Test the intraday strategies from "51 Trading Strategies" (chapter 2, plus a common opening-range
breakout as a baseline) on 5-minute bars of liquid Indian stocks, honestly and with intraday costs,
and paper-trade any of them live during market hours, without touching the existing daily system.

Context the owner should keep in mind: SEBI's study of FY2023 found about 7 in 10 individual
intraday traders in the equity cash segment lost money. The default expectation is that most of
these strategies will not survive costs.

## Independence

| Thing | Daily system | Intraday module |
|---|---|---|
| Code | `trademare.*` | `trademare.intraday.*`; nothing outside it imports it |
| Command | `trademare` | `trademare-intraday` |
| Settings | `config/settings.yaml` (+ local) | `config/intraday.yaml` (+ `config/intraday.local.yaml`) |
| Database | `data/trademare.db` | `data/intraday/intraday.db` |
| Price data | `data/cache/...` | `data/intraday/bars/` |
| Dashboard | port 8765 | port 8766, its own process and scheduler |

The module may *read* shared, side-effect-free code (indicator maths, the NSE calendar) and, for
prices, the Zerodha login token the owner creates each morning (`data/tokens/zerodha.json`) and
the Kite API key in `.env`. It never writes anything the daily system uses and never sends orders.

## Data

- **Kite Connect** (the owner upgrades the API app from the free Personal plan to Connect,
  about ₹500 a month), which provides historical candles and live data.
- Universe: the **Nifty 50** (the most liquid stocks; intraday needs tight spreads), list saved
  in the module's own folder.
- History: 5-minute candles from 2018 (or as far back as Kite serves), fetched in 100-day chunks
  at ≤ 3 requests a second and cached as one Parquet file per stock.
- Live: during market hours, the latest 5-minute candles every 5 minutes, after each candle closes.

## Common trading rules (all strategies)

- 5-minute candles; signals on a candle's close. Entries are **stop orders** at the price the
  book names (usually the signal candle's high for buys, low for sells), valid for the next
  3 candles; a gap through the trigger fills at that candle's open.
- Long and short (intraday shorting is allowed in the cash segment).
- New entries only between 09:20 and 14:45; every position closes at **15:15** (the book's time;
  Zerodha's own square-off starts at 15:20).
- One trade per stock per strategy per day (the book warns against overtrading one stock).
- Exits in this order on each candle: stop (the candle's open if it gapped past), target, then
  the strategy's own exit at the next candle's open, then 15:15.
- Costs (Zerodha intraday equity, 2026): brokerage the lower of ₹20 or 0.03% per order; STT
  0.025% on the sell side; exchange 0.00297%; SEBI fee; stamp duty 0.003% on buys; GST 18% on
  brokerage and fees; plus 2 basis points of slippage per stop or market fill. Measured on a
  ₹50,000 position.

Indicators on 5-minute candles unless stated; "previous day" levels come from the prior session's
high, low and close: P = (H+L+C)/3, R1 = 2P−L, S1 = 2P−H, R2 = P+(H−L), S2 = P−(H−L); CPR:
BC = (H+L)/2, TC = 2P−BC.

## Strategies (rules fixed before any results)

| id | Book | Buy rule (shorts mirror it) | Stop | Exit |
|---|---|---|---|---|
| `ma_fib` | 2.1 | Close above a rising 200-candle SMA (higher than 10 candles ago); the last 60 candles' low came before their high, a move of at least 1%; the price pulled back into the 38.2–61.8% retracement and closes as a green candle | The next Fibonacci level below the one touched (50% → 61.8%, 61.8% → 78.6%) | The swing high or 2R, whichever is further |
| `supertrend_pivot` | 2.2 | Close above the previous day's R1, Supertrend(7, 3) up, green candle; buy above its close | Supertrend line | Close below the Supertrend (next open) |
| `vwap_bands` | 2.3 | A red candle closed below the day's VWAP − 2σ band (volume-weighted σ since the open); then a green candle; buy above that green candle's high | Its low | VWAP (the book's first target) |
| `rsi_volume` | 2.4 | Uptrend (close above the 200-candle SMA); RSI(14) at or below 30 and the volume oscillator (EMA 5 vs EMA 10 of volume) at or below −30% within the last 3 candles; then a green candle; buy above its high | Lowest low of the last 10 candles | RSI(14) reaches 70 |
| `pivot_pullback` | 2.5 | Price closed above a previous-day pivot level (S1, P, R1, R2) 2–12 candles ago; since then the low came within 0.15% of the level without a close more than 0.15% below it; now a green candle; buy above its high | Its low | The next pivot level above that is at least 1R away |
| `double_rsi` | 2.6 | 5-minute RSI(14) at or below 30 within the last 3 candles while the hourly RSI(14) (completed hours only) is above 50; green candle; buy above its high | Its low | 5-minute RSI rises above 70 and then falls back below it |
| `cpr` | 2.7 | Today's CPR width vs the stock's last 60 days: **narrow** (bottom 30%): a close breaking above BC/TC, R1 or S1 from below, buy above that candle's high; **wide** (top 30%): a green candle whose low touches S1 or BC (within 0.15%), buy above its high; middle 40%: no trade | The signal candle's low | The next pivot level above |
| `orb` | — (baseline) | After 09:30, a close above the first 15 minutes' high; buy above that candle's high | The opening range's low | 2R |

Not included: chapter 5 scalping (1–3 minute charts and Heikin-Ashi, too fast for this setup),
the Martingale system, and the option strategies of chapter 6.

## Track record and evidence bar

Every signal simulated as one trade with the rules above, per stock, on the cached history,
split into **2018–2021** and **2022 onwards**. Reported per strategy, period and side (long,
short): trades, win rate, average and median net gain per trade, profit factor, average holding
time, and the share of trades closed at 15:15.

A strategy may be paper-traded live by default only if it has at least 300 trades in each
period, a positive average after costs in both, and a profit factor of at least 1.1 in the later
one. The bar is fixed here and not changed after looking.

## Live paper trading

A separate process (`trademare-intraday serve`) runs from 09:10 to 15:35 IST on NSE trading days:
every 5 minutes it fetches the closed candles, evaluates the selected strategies, places paper
stop orders, fills them on the following candles by the same rules as the study, and squares off
at 15:15. Separate paper capital (₹1,00,000 by default), 0.5% risk per trade, at most 5 open
positions, no leverage. The dashboard on port 8766 shows the day's trades, open positions, P&L,
each strategy's record and the selection. Alerts are not sent in this version.

## Phases

1. Package, settings, database, Kite candle loader and cache, Nifty 50 list, costs; tests.
2. Indicators (VWAP bands, volume oscillator, pivots/CPR, hourly RSI) and the eight strategies;
   tests.
3. Study, `trademare-intraday study`, results appended here.
4. Live paper engine, scheduler and dashboard.
