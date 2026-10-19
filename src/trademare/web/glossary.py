# ruff: noqa: RUF001  (proper minus, times and dash signs in user-facing text)
"""Plain-English explanations behind the dashboard's ⓘ icons, written for someone new to trading.

Templates call ``{{ tip("sharpe") }}``. Keys for evidence metrics match ``TECH_METRICS`` and
``FUND_METRICS`` in strategy/evidence.py, so those loops can call ``tip(key)`` directly.
"""

from __future__ import annotations

from markupsafe import Markup, escape

GLOSSARY: dict[str, str] = {
    # ---------------------------------------------------------------- account and safety
    "equity": "What the account is worth right now: cash plus the current value of every open position.",
    "cash": "Money not invested in any position. Some is always kept back (the cash buffer in Settings).",
    "budget": "The most this market may ever have invested at once, set by you in Settings. "
    "Position sizes are worked out from it.",
    "positions_count": "Stocks held now, out of the most the strategy is allowed to hold at once.",
    "from_peak": "How far equity is below its highest point so far (the drawdown). 0% means the "
    "account is at a new high.",
    "kill_switch": "A safety brake. If the account loses too much in one day or falls too far from its "
    "peak, new buys stop until you re-arm it. Stops and exits keep working.",
    "rearm": "Turns the kill switch back on so new buys are allowed again. Do it only once you "
    "understand why it tripped.",
    "paper": "Paper trading: orders are simulated with real prices, but no real money moves. A market "
    "trades real money only when it is marked validated in the settings file.",
    "validated": "A market is validated only after its strategy passed a test on historical data it was "
    "never tuned on. Until then the system refuses real orders for it.",
    "regime": "The overall market mood, read from the index's trend and volatility. In a risk-off "
    "regime the strategy holds fewer stocks and needs stronger signals.",
    "breadth": "How many stocks in the market rose versus fell on the last session. Many more risers "
    "than fallers is a healthy sign.",
    "last_processed": "The latest trading day the daily run has fully handled: prices, fills, stops "
    "and new signals.",
    "gross_exposure": "Total value of all positions as a share of equity. 80% means 80% of the "
    "account is invested and 20% is in cash.",
    "realised_pnl": "Profit or loss locked in by trades that have been closed, after costs.",
    "unrealised_pnl": "Profit or loss on positions still open, at the latest price. It becomes "
    "realised only when you sell.",
    "audit_log": "A record of everything the system did: signals, approvals, orders, fills and "
    "warnings, newest first.",
    "next_run": "When the daily job runs next: after the market closes it downloads prices, fills "
    "paper orders, checks stops and creates new signals.",
    # ---------------------------------------------------------------- signals and orders
    "signal": "A suggested trade. Nothing happens until you approve it; unapproved signals expire.",
    "score": "The strategy's overall rating of a stock from −1 (worst) to +1 (best), combining its "
    "analysts. Higher means a stronger buy case.",
    "confidence": "How much data the score is based on. Low confidence means some inputs were missing.",
    "entry_limit": "The highest price you will pay. The order is a limit order: it fills at the next "
    "open only if the price is at or below this.",
    "stop": "The stop-loss: if the price falls to this level the position is sold automatically, "
    "capping the loss. It is placed as soon as the buy fills.",
    "long_short": "LONG means buying shares to profit if the price rises. SHORT means selling borrowed "
    "shares to profit if it falls (off by default).",
    "pnl": "P&L means profit and loss: what a trade has made (+) or lost (−), after brokerage, taxes "
    "and fees.",
    "limit_order": "A limit order fills only at your price or better. A market order fills straight "
    "away at whatever the price is.",
    "sector": "The industry the company is in. Stocks are compared with sector peers, and the "
    "strategy caps how much goes into any one sector.",
    "target": "An optional profit-taking price. When reached, the position is sold.",
    "quantity": "Number of shares, sized so that being stopped out loses about your risk-per-trade "
    "setting (e.g. 1% of the budget).",
    "risk": "What you would lose if the stop is hit: quantity × (entry − stop), before costs.",
    "expires": "Signals are only valid for the next session. After this time it can no longer be "
    "approved, because the prices it was based on are stale.",
    "explain": "Everything the system looked at for this signal: price trend, company financials, "
    "official filings, and how the position was sized.",
    "event_alerts": "Warnings from official filings and the results calendar. Red flags (like an "
    "auditor resigning) deserve a careful read before approving.",
    "ask_claude": "Ask an AI to explain this signal in plain words. It sees only the evidence on this "
    "card, and it won't make the decision for you.",
    "exit_signal": "A suggestion to sell a holding, for example because it dropped out of the "
    "strategy's top-ranked stocks. Approving sells at the next open.",
    "order_purpose": "entry buys a new position; stop is the protective stop-loss; exit sells a "
    "position you approved selling.",
    "trigger": "For a stop order: the price that activates it.",
    "limit_price": "The worst price the order will accept. A stop's limit sits a little past its "
    "trigger so it can still fill in a fast fall.",
    "valid_until": "The last session the order can fill. Protective stops stay until cancelled.",
    "awaiting_fill": "Approved and sent, but not bought yet. It fills at the next open if the price "
    "is at or below your limit; otherwise it expires.",
    # ---------------------------------------------------------------- journal
    "invested": "What you paid: entry price × quantity.",
    "current_value": "What the shares are worth at the latest close: price now × quantity.",
    "return": "Profit or loss as a percentage of what you invested.",
    "r_multiple": "Profit or loss measured in units of the risk you took. +2R means you made twice "
    "what you stood to lose at the stop; −1R means the stop was hit.",
    "win_rate": "Share of closed trades that made money. Trend strategies often win less than half "
    "the time but make more on winners than they lose on losers.",
    "passed_on": "Signals you rejected or let expire, and what they would have returned. A way to "
    "check whether your own overrides help or hurt.",
    "long_term": "Indian and US tax treat gains on shares held more than a year as long-term, taxed "
    "at a lower rate. This shows how many days until that happens.",
    "stop_away": "How far the current price is above the stop. Small means the position is close "
    "to being sold.",
    # ---------------------------------------------------------------- technical analysis
    "trend": "Whether the price is above its 50- and 200-day averages and the long average is rising.",
    "momentum": "How strongly the price has risen recently, compared with the index.",
    "setup": "Whether right now is a good moment: a breakout or pullback scores well; a price that "
    "has run up too far too fast scores badly.",
    "volume": "Whether more shares change hands on up days than down days, a sign of buying interest.",
    "close": "The last price of the trading day.",
    "sma50": "The average closing price over the last 50 trading days (about 2½ months).",
    "sma200": "The average closing price over the last 200 trading days (about 10 months). A price "
    "above it is in a long-term uptrend.",
    "dist_sma50": "How far the price is above (+) or below (−) its 50-day average.",
    "rsi": "Relative Strength Index, 0–100: above 70 the stock has risen fast and may be stretched; "
    "below 30 it has fallen fast.",
    "adx": "Average Directional Index: how strong the trend is, up or down. Above 25 is a clear trend.",
    "atr_pct": "Average True Range: the stock's typical daily price swing, as a % of its price. "
    "Stops are set a few ATRs away so normal wiggles don't trigger them.",
    "ret_1m": "Price change over the last month.",
    "ret_3m": "Price change over the last 3 months.",
    "ret_6m": "Price change over the last 6 months.",
    "ret_12_1": "Momentum: the return over the past 12 months, skipping the latest month. Stocks that "
    "rose most over a year have tended to keep rising for a while.",
    "rel_6m": "The 6-month return minus the index's. Positive means it beat the market.",
    "from_52w_high": "How far the price is below its highest price of the last year.",
    "vol_ratio": "Today's trading volume compared with its 20-day average. 2.0x means twice the "
    "usual activity.",
    "traded_value_20": "Average value of shares traded per day over 20 days. Higher means easier to "
    "buy and sell without moving the price.",
    "sma_lines": "The chart's moving averages: the 50-day (short-term trend) and 200-day "
    "(long-term trend) average prices.",
    # ---------------------------------------------------------------- fundamentals
    "quality": "How profitable the company is (return on equity and assets, margins) compared with "
    "others in its sector.",
    "health": "How safe the balance sheet is: debt, ability to pay short-term bills and interest, "
    "compared with peers.",
    "growth": "How fast revenue and earnings are growing, compared with peers.",
    "value": "How cheap the stock is for what the company earns, its cash flow and its book value, "
    "compared with peers.",
    "market_cap": "Market capitalisation: share price × number of shares, the market's price tag for "
    "the whole company.",
    "pe": "Price / earnings: what you pay for each unit of yearly profit. Lower is cheaper; loss-makers "
    "have none.",
    "price_to_book": "Price / book: market value divided by the accounting value of the company's net "
    "assets.",
    "earnings_growth": "Change in profit over the last 12 months versus the 12 months before.",
    "revenue_growth": "Change in sales over the last 12 months versus the 12 months before.",
    "gross_margin": "Share of sales left after the direct cost of making the product.",
    "operating_margin": "Share of sales left after all running costs, before interest and tax.",
    "net_margin": "Share of sales left as profit after everything, excluding one-off gains or losses.",
    "roe": "Return on equity: yearly profit as a % of the shareholders' money in the business.",
    "roa": "Return on assets: yearly profit as a % of everything the company owns.",
    "debt_to_equity": "Debt divided by shareholders' equity. Above 1 means more borrowed money than "
    "owners' money.",
    "current_ratio": "Short-term assets divided by bills due within a year. Below 1 can mean cash pressure.",
    "interest_coverage": "Operating profit divided by interest paid. Below about 2 means interest "
    "eats a large part of profit.",
    "fcf_yield": "Free cash flow (cash from the business minus investment) as a % of market cap.",
    "gross_profitability": "Gross profit divided by total assets; a quality measure that has "
    "predicted returns in research.",
    "data_age_days": "Days since the end of the latest reported quarter. Old data means new results "
    "are due soon.",
    "ttm": "Trailing twelve months: the sum of the last four reported quarters.",
    "available_from": "The first date these numbers were public. Backtests only use numbers on or "
    "after this date, so they never peek at the future.",
    # ---------------------------------------------------------------- forensic checks
    "piotroski": "Nine yes/no checks of profitability, balance sheet and efficiency (Piotroski 2000). "
    "7–9 is strong, 0–3 weak.",
    "altman": "A bankruptcy-risk score (Altman Z''). Below 1.1 is the distress zone, above 2.6 safe.",
    "beneish": "An earnings-manipulation screen (Beneish M-score). Above −1.78 resembles companies "
    "that inflated their profits. A warning, not proof.",
    "accruals": "Profit minus operating cash flow, as a share of assets. High means reported profits "
    "are not turning into cash.",
    "next_results": "When the company next reports results. Prices often jump on results day, "
    "so a fresh buy just before carries extra risk.",
    "red_flags": "Red flags are serious events in official filings (default, fraud, auditor "
    "resigning). Cautions are worth reading (rating downgrade, regulator order).",
    # ---------------------------------------------------------------- sizing
    "position_value": "What this trade would cost: quantity × entry price.",
    "stop_distance": "How far the stop is below the entry price.",
    "loss_if_stopped": "What you lose if the price falls to the stop, and how that compares with "
    "your risk-per-trade limit.",
    "reward_risk": "Potential gain to the target divided by the potential loss to the stop.",
    "risk_per_share": "Entry price minus stop price: what each share can lose before it is sold.",
    "weights": "How much each analyst (technical, fundamental, news) counts towards the score.",
    # ---------------------------------------------------------------- returns over time
    "chg_1d": "Price change on the last trading day.",
    "chg_1m": "Price change over the last month.",
    "chg_6m": "Price change over the last 6 months.",
    "chg_1y": "Price change over the last year.",
    # ---------------------------------------------------------------- backtests
    "backtest": "A replay of the strategy over past prices, as if it had been running then, "
    "including costs and slippage.",
    "total_return": "Gain over the whole period.",
    "cagr": "Compound annual growth rate: the steady yearly return that would give the same total.",
    "volatility": "How much returns swing, as a yearly standard deviation. Higher means a bumpier ride.",
    "sharpe": "Return per unit of volatility. Higher is better; above 1 is good. Compare it with the "
    "index's Sharpe.",
    "sortino": "Like Sharpe, but counts only downside swings as risk.",
    "max_drawdown": "The worst fall from a peak to a low during the period. It shows the pain you "
    "would have had to sit through.",
    "calmar": "Yearly return divided by the worst drawdown. Higher is better.",
    "longest_drawdown": "The longest stretch, in calendar days, before the account got back to a "
    "previous high.",
    "profit_factor": "Total won on winning trades divided by total lost on losing ones. Above 1 is "
    "profitable.",
    "avg_r": "Average result per trade in units of risk taken (see R).",
    "exposure": "How much of the account was invested on average, and on how many days it held anything.",
    "index_benchmark": "The market index bought and held over the same period, for comparison.",
    "oos": "In-sample is the part of history used to choose settings; out-of-sample is the later part "
    "never used for that. Only out-of-sample results are honest evidence.",
    "overrides": "Settings changed for this run only, e.g. a different stop distance.",
    "survivorship": "The backtest uses today's index members, which leaves out companies that failed. "
    "That flatters past results.",
    # ---------------------------------------------------------------- paper vs backtest
    "tracking": "Replays the backtest over the exact days paper trading has run. If the backtest is "
    "realistic, both should make the same trades.",
    "gap": "Paper return minus backtest return over the same days.",
    "tracking_error": "How much the daily returns of paper and backtest differ, as a yearly figure. "
    "Small (a few %) means the backtest models reality well.",
    "unexplained": "Trades made on only one side with no decision of yours to explain them. These "
    "point at a problem worth investigating before using real money.",
    "prices_as_of": "The last trading day whose closing prices TradeMare has. Prices are fetched at each "
    "daily run; if a finished session is still missing (the data provider was late), the chip turns "
    "amber and that day's fills and stops wait until the prices arrive.",
    # ---------------------------------------------------------------- IPOs
    "ipo": "Initial public offering: a company selling shares to the public for the first time. You bid "
    "during a few days; shares are allotted (often by lottery) and start trading on the listing day.",
    "price_band": "The range you may bid within. Most retail investors tick 'cut-off', which means "
    "paying whatever final price is set, normally the top of the band.",
    "lot": "Bids are made in lots, a fixed number of shares. The minimum investment is one lot at the "
    "top of the price band.",
    "issue_size": "The total value of shares on offer, at the top of the price band.",
    "issue_type": "Fresh issue: new shares, money goes to the company. Offer for sale (OFS): existing "
    "owners sell their shares and keep the money; the company raises nothing.",
    "subscription": "How many times over each part of the issue has been bid for. 10× means bids for ten "
    "times the shares on offer. It is updated hourly while bidding is open.",
    "qib": "Qualified Institutional Buyers: mutual funds, insurers, banks and foreign funds. They do "
    "their own research, so their demand is the most watched number. They bid mostly on the last day.",
    "nii": "Non-institutional investors: wealthy individuals and companies bidding over ₹2 lakh.",
    "retail_portion": "Individuals bidding up to ₹2 lakh. When this part is oversubscribed, lots are "
    "allotted by lottery, so applying does not mean you get shares.",
    "allotment_odds": "A rough chance of getting one lot as a retail applicant: about 1 in the retail "
    "subscription figure if most people applied for one lot. Real odds are often a little better.",
    "qib_bucket": "Past NSE mainboard IPOs grouped by how many times over institutions subscribed. Shows "
    "what happened to IPOs with similar demand, from TradeMare's own study of 2016–2026 listings.",
    "listing_gain": "Listing-day closing price compared with the issue price. Positive means shares bought "
    "in the IPO were worth more by the end of the first trading day.",
    "excess_6m": "Return from the issue price to six months after listing, minus the Nifty 50 over the "
    "same days. Shows whether holding on after listing has beaten simply owning the index.",
    "ipo_label": "A fixed rule written before the study ran: Favourable when at least 75% of past IPOs "
    "with similar QIB demand listed above their issue price and the median gain was 10% or more; "
    "Unfavourable when under 60% did or the median was negative; Mixed otherwise. The study found QIB "
    "demand strongly predicted listings in 2016–21 and again in 2022–26. It is a base rate, not a "
    "forecast for this IPO.",
}


def tip(key: str, label: str | None = None) -> Markup:
    """The ⓘ icon for ``key``, or nothing when the key has no entry (so loops can call it freely)."""
    text = GLOSSARY.get(key)
    if text is None:
        return Markup("")
    name = escape(label or key.replace("_", " "))
    return Markup(
        f'<button type="button" class="tip" data-tip="{escape(text)}" aria-label="What is {name}?">i</button>'
    )
