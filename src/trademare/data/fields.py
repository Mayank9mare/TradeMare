"""Standardised financial-statement fields and point-in-time helpers.

Every fundamentals provider returns a "financials frame": one row per fiscal
period with these columns

    period_type     "Q" (quarter) or "A" (fiscal year)
    period_end      last day of the period
    available_from  first date the numbers were public (filing date, or an
                    estimate from regulatory deadlines when the source lacks it)
    <FLOW_FIELDS>   amounts for the period (quarter or year)
    <STOCK_FIELDS>  balance-sheet values at period_end

Analysts must only look at rows with ``available_from <= asof`` (see
``point_in_time``) so backtests never use numbers before they were published.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

# Income statement and cash flow: summed over a period.
FLOW_FIELDS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "net_income_normalized",  # excludes unusual / one-off items where the source provides it
    "ebitda",
    "interest_expense",
    "operating_cash_flow",
    "capex",  # positive number = cash spent
    "depreciation",  # depreciation and amortisation (forensic scores)
    "sga",  # selling, general and administrative expense
)

# Balance sheet: a snapshot at period end.
STOCK_FIELDS = (
    "total_assets",
    "total_liabilities",
    "current_assets",
    "current_liabilities",
    "total_equity",
    "total_debt",
    "cash",
    "shares_outstanding",
    "receivables",
    "ppe",  # net property, plant and equipment
    "retained_earnings",
)

FIELDS = FLOW_FIELDS + STOCK_FIELDS
META_COLUMNS = ("period_type", "period_end", "available_from")
COLUMNS = META_COLUMNS + FIELDS


def empty_financials() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in FIELDS})
    df.insert(0, "available_from", pd.Series(dtype="datetime64[ns]"))
    df.insert(0, "period_end", pd.Series(dtype="datetime64[ns]"))
    df.insert(0, "period_type", pd.Series(dtype="object"))
    return df


def normalize_financials(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a provider frame to the canonical column set, types and order."""
    out = df.reindex(columns=list(COLUMNS)).copy()
    out["period_end"] = pd.to_datetime(out["period_end"]).astype("datetime64[ns]")
    out["available_from"] = pd.to_datetime(out["available_from"]).astype("datetime64[ns]")
    for c in FIELDS:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    out = out.dropna(subset=["period_end", "available_from"])
    out = out.drop_duplicates(subset=["period_type", "period_end"], keep="first")
    return out.sort_values(["period_type", "period_end"]).reset_index(drop=True)


def point_in_time(financials: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Rows that were public on ``asof``."""
    return financials[financials["available_from"] <= pd.Timestamp(asof)]


def ttm(financials: pd.DataFrame, asof: date) -> pd.Series | None:
    """Trailing-twelve-month view as of ``asof``.

    Flow fields are the sum of the last four consecutive quarters (falling back
    to the latest fiscal year when quarters are missing); stock fields come
    from the most recent balance sheet. Returns None if nothing is available.
    """
    known = point_in_time(financials, asof)
    if known.empty:
        return None
    quarters = known[known["period_type"] == "Q"].sort_values("period_end").tail(4)
    annual = known[known["period_type"] == "A"].sort_values("period_end").tail(1)
    latest = known.sort_values(["period_end", "available_from"]).iloc[-1]

    use_quarters = len(quarters) == 4 and _consecutive(quarters["period_end"])
    if use_quarters and not annual.empty:
        # Prefer the fiscal year if it is at least as recent as the quarters.
        use_quarters = quarters["period_end"].iloc[-1] > annual["period_end"].iloc[0]
    if use_quarters:
        # min_count=4: a field missing in any quarter stays NaN rather than undercounting.
        flows = quarters[list(FLOW_FIELDS)].sum(min_count=4)
        flow_end = quarters["period_end"].iloc[-1]
    elif not annual.empty:
        flows = annual[list(FLOW_FIELDS)].iloc[0]
        flow_end = annual["period_end"].iloc[0]
    else:
        return None

    stocks = known.sort_values("period_end")[list(STOCK_FIELDS)].ffill().iloc[-1]
    values: dict[str, object] = {**flows.to_dict(), **stocks.to_dict()}
    values["flow_period_end"] = flow_end
    values["latest_period_end"] = latest["period_end"]
    return pd.Series(values)


def _consecutive(ends: pd.Series) -> bool:
    gaps = ends.sort_values().diff().dropna().dt.days
    return bool(((gaps >= 80) & (gaps <= 100)).all())
