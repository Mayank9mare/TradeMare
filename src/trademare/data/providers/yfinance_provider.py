"""Yahoo Finance prices (both markets) and fundamentals (India, US fallback)."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from trademare.core.interfaces import BAR_COLUMNS
from trademare.core.models import Market
from trademare.data.fields import FIELDS, empty_financials, normalize_financials
from trademare.data.quality import clean_bars, empty_bars

log = logging.getLogger(__name__)


def yf_symbol(market: Market, symbol: str) -> str:
    if symbol.startswith("^"):  # index, e.g. ^NSEI, ^GSPC
        return symbol
    if market is Market.IN:
        return f"{symbol}.NS"
    return symbol.replace(".", "-")  # BRK.B -> BRK-B


class YFinancePriceProvider:
    """Split- and dividend-adjusted daily bars."""

    def get_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_daily_bars_many(market, [symbol], start, end).get(symbol, empty_bars())

    def get_daily_bars_many(
        self, market: Market, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """Bulk download; symbols that return no data are omitted."""
        if not symbols:
            return {}
        tickers = {yf_symbol(market, s): s for s in symbols}
        raw = yf.download(
            list(tickers),
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        out: dict[str, pd.DataFrame] = {}
        if raw is None or raw.empty:
            return out
        for ticker, symbol in tickers.items():
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker].rename(columns=str.lower).dropna(how="all")
            if df.empty:
                continue
            out[symbol] = clean_bars(df.reindex(columns=list(BAR_COLUMNS)), symbol)
        return out


# yfinance row label(s) for each standard field, in order of preference.
_ROWS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "gross_profit": ("Gross Profit",),
    "operating_income": ("Operating Income", "Total Operating Income As Reported"),
    "net_income": ("Net Income Common Stockholders", "Net Income"),
    "net_income_normalized": ("Normalized Income",),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "operating_cash_flow": ("Operating Cash Flow",),
    "capex": ("Capital Expenditure",),
    "total_assets": ("Total Assets",),
    "total_liabilities": ("Total Liabilities Net Minority Interest",),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
    "total_equity": ("Stockholders Equity", "Common Stock Equity"),
    "total_debt": ("Total Debt",),
    "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued"),
}

# Yahoo has no filing dates, so availability is estimated from the regulatory
# deadline after period end (SEBI LODR: 45 days for quarterly results, 60 for
# audited annual; SEC: 40-45 days for 10-Q, 60-90 for 10-K — we use the
# conservative end).
REPORTING_LAG_DAYS: dict[Market, dict[str, int]] = {
    Market.IN: {"Q": 45, "A": 60},
    Market.US: {"Q": 45, "A": 90},
}


def statements_to_financials(
    market: Market,
    statements: dict[str, list[pd.DataFrame]],
    fx: Callable[[pd.Timestamp], float] | None = None,
) -> pd.DataFrame:
    """Convert yfinance statements into a standard financials frame.

    ``statements`` maps period type ("Q"/"A") to the list of yfinance frames
    (rows = line items, columns = period end dates) for that period type.
    ``fx`` converts the statement currency into the market's currency at a
    period-end date (some Indian listings, e.g. Infosys, report in USD).
    """
    rows = []
    for period_type, frames in statements.items():
        frames = [f for f in frames if f is not None and not f.empty]
        if not frames:
            continue
        merged = pd.concat(frames, axis=0, sort=False)
        merged = merged[~merged.index.duplicated(keep="first")]
        for period_end in merged.columns:
            col = merged[period_end]
            record: dict[str, object] = {"period_type": period_type, "period_end": pd.Timestamp(period_end)}
            for field in FIELDS:
                record[field] = next(
                    (col[label] for label in _ROWS[field] if label in col.index and pd.notna(col[label])),
                    None,
                )
            if all(record[f] is None for f in FIELDS):
                continue
            lag = REPORTING_LAG_DAYS[market][period_type]
            record["available_from"] = pd.Timestamp(period_end) + pd.Timedelta(days=lag)
            rows.append(record)
    if not rows:
        return empty_financials()
    df = pd.DataFrame(rows)
    df["capex"] = df["capex"].astype("float64").abs()  # Yahoo reports capex as negative
    if fx is not None:
        rates = df["period_end"].map(fx)
        for field in MONEY_FIELDS:
            df[field] = pd.to_numeric(df[field], errors="coerce") * rates
    return normalize_financials(df)


# Every field except share counts is an amount of money.
MONEY_FIELDS = tuple(f for f in FIELDS if f != "shares_outstanding")


class FxRates:
    """Daily FX closes from Yahoo (e.g. USDINR=X), fetched once per pair."""

    def __init__(self, download: Callable[[str], pd.Series] | None = None) -> None:
        self._download = download or self._yahoo
        self._series: dict[str, pd.Series] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _yahoo(pair: str) -> pd.Series:
        raw = yf.download(pair, start="2010-01-01", auto_adjust=False, progress=False)
        close = raw["Close"]
        return (close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close).dropna()

    def converter(self, source: str, target: str) -> Callable[[pd.Timestamp], float]:
        pair = f"{source}{target}=X"
        with self._lock:
            if pair not in self._series:
                series = self._download(pair)
                if series.empty:
                    raise ValueError(f"no FX data for {pair}")
                self._series[pair] = series.sort_index()
        series = self._series[pair]

        def rate(day: pd.Timestamp) -> float:
            # Last close on or before the period end (first available if earlier).
            i = series.index.searchsorted(pd.Timestamp(day), side="right") - 1
            return float(series.iloc[max(i, 0)])

        return rate


class CurrencyCache:
    """Statement currency per symbol, persisted (it almost never changes) to spare Yahoo's rate limit."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, str] = json.loads(path.read_text()) if path and path.exists() else {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, currency: str) -> None:
        with self._lock:
            self._data[key] = currency
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data, sort_keys=True))


class YFinanceFundamentalsProvider:
    source = "yfinance"

    def __init__(self, fx: FxRates | None = None, currencies: CurrencyCache | None = None) -> None:
        self.fx = fx or FxRates()
        self.currencies = currencies or CurrencyCache()

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame:
        t = yf.Ticker(yf_symbol(market, symbol))
        statements = _with_retry(
            lambda: {
                "A": [t.income_stmt, t.balance_sheet, t.cashflow],
                "Q": [t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow],
            }
        )
        key = f"{market.value}:{symbol}"
        currency = self.currencies.get(key)
        if currency is None:
            currency = _with_retry(lambda: statement_currency(t))
            self.currencies.set(key, currency)
        converter = None
        if currency and currency != market.currency:
            log.info("%s reports in %s; converting to %s", symbol, currency, market.currency)
            converter = self.fx.converter(currency, market.currency)
        return statements_to_financials(market, statements, converter)


def statement_currency(ticker: yf.Ticker) -> str | None:
    """Currency of the financial statements (may differ from the trading currency).

    Errors propagate, and a missing field is treated as an error: when rate
    limited, Yahoo returns a cut-down ``info`` without it, and guessing the
    trading currency would silently store, say, USD figures as INR.
    """
    currency = (ticker.info or {}).get("financialCurrency")
    if not currency:
        raise LookupError("Yahoo did not report a statement currency (possibly rate limited)")
    return currency


RETRY_DELAYS = (5, 20, 60)  # seconds; Yahoo rate limits recover within about a minute


def _with_retry(fn, delays=RETRY_DELAYS):
    for delay in (*delays, None):
        try:
            return fn()
        except Exception as e:
            if delay is None or "Too Many Requests" not in str(e):
                raise
            log.warning("Yahoo rate limit; retrying in %ss", delay)
            time.sleep(delay)
    return None
