"""Latest intraday prices from Yahoo, for paper trading's intraday stop check (live brokers quote
their own prices). Yahoo quotes can lag the exchange by a few minutes; good enough for an alert."""

from __future__ import annotations

import pandas as pd

from trademare.core.models import Market
from trademare.data.providers.yfinance_provider import yf_symbol


def latest_prices(market: Market, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    import yfinance as yf

    tickers = {yf_symbol(market, s): s for s in symbols}
    raw = yf.download(
        list(tickers), period="1d", interval="1m", progress=False, auto_adjust=False, group_by="ticker"
    )
    out: dict[str, float] = {}
    for ticker, symbol in tickers.items():
        try:
            closes = raw[ticker]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        except KeyError:
            continue
        closes = closes.dropna()
        if len(closes):
            out[symbol] = float(closes.iloc[-1])
    return out
