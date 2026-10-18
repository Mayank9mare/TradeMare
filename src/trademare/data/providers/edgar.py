"""SEC EDGAR XBRL "companyfacts" -> point-in-time financials for US companies.

Every XBRL fact carries the date it was filed, so ``available_from`` is the
real publication date. For each period we keep the *first* filed value, so
later restatements never leak into the past.

Quirks handled:
- 10-Q cash-flow items are year-to-date (3, 6, 9 months); income items have
  both 3-month and YTD values. Quarters are rebuilt by differencing each
  YTD chain, which also yields Q4 = fiscal year - 9 months.
- Companies switch XBRL tags over time (e.g. Revenues ->
  RevenueFromContractWithCustomerExcludingAssessedTax), so each field has a
  priority list of tags that are merged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd
import requests

from trademare.core.models import Market
from trademare.data.fields import empty_financials, normalize_financials

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}

# Flow (duration) tags, in priority order.
FLOW_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "depreciation": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation"),
    "interest_expense": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
}

# Instant (balance sheet) tags, in priority order.
STOCK_TAGS: dict[str, tuple[str, ...]] = {
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "total_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"),
    "receivables": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "ppe": ("PropertyPlantAndEquipmentNet",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "long_term_debt": ("LongTermDebt",),
    "long_term_debt_noncurrent": ("LongTermDebtNoncurrent",),
    "long_term_debt_current": ("LongTermDebtCurrent",),
    "short_term_borrowings": ("ShortTermBorrowings",),
    "commercial_paper": ("CommercialPaper",),
    "shares_common": ("CommonStockSharesOutstanding",),
}

QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
SHARES_WINDOW_DAYS = 150  # cover-page share counts are dated after period end


def _facts_for(facts: dict[str, Any], taxonomy: str, tags: tuple[str, ...], unit: str) -> pd.DataFrame:
    """All facts for ``tags`` with a priority column (0 = preferred tag)."""
    frames = []
    for priority, tag in enumerate(tags):
        values = facts.get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit)
        if values:
            df = pd.DataFrame(values)
            df["priority"] = priority
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["start", "end", "val", "filed", "form", "priority"])
    df = pd.concat(frames, ignore_index=True)
    df = df[df["form"].isin(FORMS)].copy()
    for col in ("start", "end", "filed"):
        if col in df:
            df[col] = pd.to_datetime(df[col])
    return df


def _first_filed(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Original (point-in-time) value per period: preferred tag, then earliest filing."""
    return df.sort_values(["priority", "filed"]).drop_duplicates(keys, keep="first")


def flow_periods(df: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], tuple[float, pd.Timestamp]]:
    """Map (period_type, period_end) -> (value, available_from) for a duration field."""
    if df.empty or "start" not in df:
        return {}
    df = df.dropna(subset=["start"])
    df = _first_filed(df, ["start", "end"])
    days = (df["end"] - df["start"]).dt.days
    out: dict[tuple[str, pd.Timestamp], tuple[float, pd.Timestamp]] = {}

    for row in df[(days >= YEAR_DAYS[0]) & (days <= YEAR_DAYS[1])].itertuples():
        out[("A", row.end)] = (float(row.val), row.filed)

    # Rebuild quarters from each chain of facts sharing a start date (YTD chains).
    for start, chain in df.groupby("start"):
        prev_end, prev_val, prev_filed = start - pd.Timedelta(days=1), 0.0, pd.Timestamp.min
        for row in chain.sort_values("end").itertuples():
            gap = (row.end - prev_end).days
            if not QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]:
                break  # chain broken (missing quarter); later YTD values are unusable
            value = float(row.val) - prev_val
            available = max(row.filed, prev_filed)
            key = ("Q", row.end)
            if key not in out or available < out[key][1]:
                out[key] = (value, available)
            prev_end, prev_val, prev_filed = row.end, float(row.val), row.filed
    return out


def stock_values(df: pd.DataFrame) -> dict[pd.Timestamp, tuple[float, pd.Timestamp]]:
    """Map instant date -> (value, available_from) for a balance-sheet field."""
    if df.empty:
        return {}
    if "start" in df:
        df = df[df["start"].isna()]
    df = _first_filed(df, ["end"])
    return {row.end: (float(row.val), row.filed) for row in df.itertuples()}


def companyfacts_to_financials(payload: dict[str, Any]) -> pd.DataFrame:
    facts = payload.get("facts", {})
    flows = {f: flow_periods(_facts_for(facts, "us-gaap", tags, "USD")) for f, tags in FLOW_TAGS.items()}
    stocks = {f: stock_values(_facts_for(facts, "us-gaap", tags, "USD")) for f, tags in STOCK_TAGS.items()}
    stocks["shares_common"] = stock_values(
        _facts_for(facts, "us-gaap", STOCK_TAGS["shares_common"], "shares")
    )
    cover_shares = _facts_for(facts, "dei", ("EntityCommonStockSharesOutstanding",), "shares")

    periods = sorted({key for field in ("revenue", "net_income") for key in flows[field]})
    rows = []
    for period_type, end in periods:
        rec: dict[str, Any] = {"period_type": period_type, "period_end": end}
        filed: dict[str, pd.Timestamp] = {}

        def put(field: str, entry: tuple[float, pd.Timestamp] | None, rec=rec, filed=filed) -> None:
            if entry is not None:
                rec[field], filed[field] = entry

        for f in FLOW_TAGS:
            put(f, flows[f].get((period_type, end)))
        for f in STOCK_TAGS:
            put(f, stocks[f].get(end))
        put("shares_outstanding", _cover_shares(cover_shares, end) or stocks["shares_common"].get(end))

        _derive(rec)
        # Row is public when its primary filing is; drop any field first seen in
        # a much later filing (late tagging), which would otherwise leak the future.
        available = min(filed.get(f, pd.Timestamp.max) for f in ("revenue", "net_income"))
        for f, when in filed.items():
            if when > available + pd.Timedelta(days=7):
                rec[f] = None
        rec["available_from"] = available
        rows.append(rec)

    if not rows:
        return empty_financials()
    return normalize_financials(pd.DataFrame(rows))


def _cover_shares(cover: pd.DataFrame, period_end: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    if cover.empty:
        return None
    window = cover[
        (cover["end"] >= period_end) & (cover["end"] <= period_end + pd.Timedelta(days=SHARES_WINDOW_DAYS))
    ]
    if window.empty:
        return None
    row = window.sort_values(["filed", "end"]).iloc[0]
    return float(row["val"]), row["filed"]


def _num(rec: dict[str, Any], key: str) -> float | None:
    value = rec.get(key)
    return None if value is None or pd.isna(value) else float(value)


def _derive(rec: dict[str, Any]) -> None:
    """Fill standard fields that need arithmetic from related tags."""
    revenue, cost = _num(rec, "revenue"), _num(rec, "cost_of_revenue")
    if _num(rec, "gross_profit") is None and revenue is not None and cost is not None:
        rec["gross_profit"] = revenue - cost

    op_income, dep = _num(rec, "operating_income"), _num(rec, "depreciation")
    if op_income is not None and dep is not None:
        rec["ebitda"] = op_income + dep

    if _num(rec, "total_liabilities") is None:
        le, equity = _num(rec, "liabilities_and_equity"), _num(rec, "total_equity")
        if le is not None and equity is not None:
            rec["total_liabilities"] = le - equity

    long_term = _num(rec, "long_term_debt")
    if long_term is None:
        parts = [_num(rec, "long_term_debt_noncurrent"), _num(rec, "long_term_debt_current")]
        long_term = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None
    short_term = [_num(rec, "short_term_borrowings"), _num(rec, "commercial_paper")]
    if long_term is not None or any(p is not None for p in short_term):
        rec["total_debt"] = (long_term or 0.0) + sum(p for p in short_term if p is not None)


class SecClient:
    """Polite EDGAR HTTP client: identifying User-Agent, <= ~8 req/s, retries."""

    MIN_INTERVAL = 0.125

    def __init__(self, user_agent: str, session: requests.Session | None = None) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC requires a User-Agent with a contact email, e.g. 'TradeMare you@example.com'"
            )
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._lock = threading.Lock()
        self._last = 0.0

    def get_json(self, url: str, retries: int = 3) -> Any:
        for attempt in range(retries + 1):
            with self._lock:
                wait = self.MIN_INTERVAL - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                resp = self.session.get(url, timeout=60)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == retries:
                    raise
                time.sleep(2**attempt)
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        return None


class EdgarFundamentalsProvider:
    source = "edgar"

    def __init__(self, client: SecClient, ciks: dict[str, str] | None = None) -> None:
        self.client = client
        self._ciks = {k.upper(): v for k, v in (ciks or {}).items() if v}
        self._ticker_map: dict[str, str] | None = None

    def cik_for(self, symbol: str) -> str | None:
        symbol = symbol.upper()
        if symbol in self._ciks:
            return self._ciks[symbol]
        if self._ticker_map is None:
            data = self.client.get_json(TICKERS_URL) or {}
            self._ticker_map = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
        return self._ticker_map.get(symbol) or self._ticker_map.get(symbol.replace(".", "-"))

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame:
        if market is not Market.US:
            raise ValueError("EDGAR only covers US-listed companies")
        cik = self.cik_for(symbol)
        if cik is None:
            log.warning("%s: no SEC CIK found", symbol)
            return empty_financials()
        payload = self.client.get_json(COMPANYFACTS_URL.format(cik=cik))
        if not payload:
            return empty_financials()
        return companyfacts_to_financials(payload)
