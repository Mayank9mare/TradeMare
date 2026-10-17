"""Valuation, profitability and balance-sheet ratios from point-in-time financials.

Split in two so backtests can cache the expensive part:
``statement_ratios`` depends only on which filings are public (changes a few
times a year); ``finalize_ratios`` adds price- and date-dependent values.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from trademare.data.fields import ttm


def _div(a: float | None, b: float | None) -> float | None:
    """a / b, or None when an input is missing or the denominator is not positive."""
    if a is None or b is None or b <= 0:
        return None
    return a / b


RATIO_KEYS = (
    "market_cap", "pe", "price_to_book", "fcf_yield", "gross_margin", "operating_margin", "net_margin",
    "roe", "roa", "debt_to_equity", "current_ratio", "interest_coverage", "revenue_growth",
    "earnings_growth", "gross_profitability", "revenue_ttm", "net_income_ttm", "total_equity",
    "data_age_days",
)  # fmt: skip


def statement_ratios(financials: pd.DataFrame, asof: date) -> dict[str, Any] | None:
    """Price-independent ratios from filings public on ``asof`` (None if nothing is public)."""
    now = ttm(financials, asof)
    if now is None:
        return None
    year_ago = ttm(financials, asof - timedelta(days=365))

    def g(field: str) -> float | None:
        return _clean(now.get(field))

    net_income = g("net_income")
    ocf, capex = g("operating_cash_flow"), g("capex")

    revenue_growth = earnings_growth = None
    # Compare against a different (older) TTM window only.
    if year_ago is not None and year_ago["flow_period_end"] != now["flow_period_end"]:
        growth = _div(g("revenue"), _clean(year_ago.get("revenue")))
        revenue_growth = None if growth is None else growth - 1
        prev_ni = _clean(year_ago.get("net_income"))
        # Growth from a loss is not meaningful as a ratio.
        if prev_ni is not None and prev_ni > 0 and net_income is not None:
            earnings_growth = net_income / prev_ni - 1

    return {
        "gross_margin": _div(g("gross_profit"), g("revenue")),
        "operating_margin": _div(g("operating_income"), g("revenue")),
        "net_margin": _div(net_income, g("revenue")),
        "roe": _div(net_income, g("total_equity")),
        "roa": _div(net_income, g("total_assets")),
        "debt_to_equity": _div(g("total_debt"), g("total_equity")),
        "current_ratio": _div(g("current_assets"), g("current_liabilities")),
        "interest_coverage": _div(g("operating_income"), g("interest_expense")),
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "gross_profitability": _div(g("gross_profit"), g("total_assets")),  # Novy-Marx (2013)
        "revenue_ttm": g("revenue"),
        "net_income_ttm": net_income,
        "total_equity": g("total_equity"),
        # Inputs for finalize_ratios:
        "_shares": g("shares_outstanding"),
        "_fcf": None if ocf is None else ocf - (capex or 0.0),
        "_flow_period_end": now["flow_period_end"],
    }


def finalize_ratios(base: dict[str, Any] | None, asof: date, price: float | None) -> dict[str, float | None]:
    """Add valuation ratios at ``price`` and data age at ``asof``. Always returns every key."""
    if base is None:
        return dict.fromkeys(RATIO_KEYS)
    shares, net_income = base["_shares"], base["net_income_ttm"]
    market_cap = price * shares if price is not None and shares is not None else None
    out = {k: v for k, v in base.items() if not k.startswith("_")}
    out.update(
        market_cap=market_cap,
        pe=_div(market_cap, net_income) if net_income is not None and net_income > 0 else None,
        price_to_book=_div(market_cap, base["total_equity"]),
        fcf_yield=_div(base["_fcf"], market_cap),
        data_age_days=float((pd.Timestamp(asof) - base["_flow_period_end"]).days),
    )
    return {k: out.get(k) for k in RATIO_KEYS}


def key_ratios(financials: pd.DataFrame, asof: date, price: float | None) -> dict[str, float | None]:
    """Ratios using only data public on ``asof``. Always returns every key; missing inputs give None."""
    return finalize_ratios(statement_ratios(financials, asof), asof, price)


def _clean(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
