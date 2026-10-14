"""Valuation, profitability and balance-sheet ratios from point-in-time financials."""

from __future__ import annotations

from datetime import date, timedelta

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
    "revenue_ttm", "net_income_ttm",
)  # fmt: skip


def key_ratios(financials: pd.DataFrame, asof: date, price: float | None) -> dict[str, float | None]:
    """Ratios using only data public on ``asof``. Always returns every key; missing inputs give None."""
    now = ttm(financials, asof)
    if now is None:
        return dict.fromkeys(RATIO_KEYS)
    year_ago = ttm(financials, asof - timedelta(days=365))

    def g(field: str) -> float | None:
        return _clean(now.get(field))

    shares, net_income = g("shares_outstanding"), g("net_income")
    market_cap = price * shares if price is not None and shares is not None else None
    ocf, capex = g("operating_cash_flow"), g("capex")
    fcf = None if ocf is None else ocf - (capex or 0.0)

    revenue_growth = None
    # Compare against a different (older) TTM window only.
    if year_ago is not None and year_ago["flow_period_end"] != now["flow_period_end"]:
        growth = _div(g("revenue"), _clean(year_ago.get("revenue")))
        revenue_growth = None if growth is None else growth - 1

    return {
        "market_cap": market_cap,
        "pe": _div(market_cap, net_income) if net_income is not None and net_income > 0 else None,
        "price_to_book": _div(market_cap, g("total_equity")),
        "fcf_yield": _div(fcf, market_cap),
        "gross_margin": _div(g("gross_profit"), g("revenue")),
        "operating_margin": _div(g("operating_income"), g("revenue")),
        "net_margin": _div(net_income, g("revenue")),
        "roe": _div(net_income, g("total_equity")),
        "roa": _div(net_income, g("total_assets")),
        "debt_to_equity": _div(g("total_debt"), g("total_equity")),
        "current_ratio": _div(g("current_assets"), g("current_liabilities")),
        "interest_coverage": _div(g("operating_income"), g("interest_expense")),
        "revenue_growth": revenue_growth,
        "revenue_ttm": g("revenue"),
        "net_income_ttm": net_income,
    }


def _clean(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
