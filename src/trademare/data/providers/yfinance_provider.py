"""Yahoo Finance prices (both markets) and fundamentals (India, US fallback)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

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
) -> pd.DataFrame:
    """Convert yfinance statements into a standard financials frame.

    ``statements`` maps period type ("Q"/"A") to the list of yfinance frames
    (rows = line items, columns = period end dates) for that period type.
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
    return normalize_financials(df)


class YFinanceFundamentalsProvider:
    source = "yfinance"

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame:
        t = yf.Ticker(yf_symbol(market, symbol))
        statements = {
            "A": [t.income_stmt, t.balance_sheet, t.cashflow],
            "Q": [t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow],
        }
        return statements_to_financials(market, statements)
