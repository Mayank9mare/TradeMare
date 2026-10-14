from datetime import date

import pandas as pd
import pytest

from trademare.analysis.ratios import key_ratios
from trademare.data.fields import normalize_financials


def q(end, avail, revenue, net_income, **extra):
    return dict(
        period_type="Q", period_end=end, available_from=avail, revenue=revenue, net_income=net_income, **extra
    )


@pytest.fixture
def financials():
    ends = pd.date_range("2024-03-31", periods=8, freq="QE")
    rows = [
        q(
            e,
            e + pd.Timedelta(days=40),
            revenue=100 + 10 * i,
            net_income=10 + i,
            operating_cash_flow=15,
            capex=5,
            total_equity=200,
            total_debt=100,
            shares_outstanding=10,
            current_assets=50,
            current_liabilities=25,
        )
        for i, e in enumerate(ends)
    ]
    return normalize_financials(pd.DataFrame(rows))


def test_key_ratios(financials):
    r = key_ratios(financials, date(2026, 3, 1), price=50.0)
    # Last 4 quarters known on 2026-03-01: 2025-03..2025-12 (i=4..7)
    assert r["revenue_ttm"] == 140 + 150 + 160 + 170
    assert r["net_income_ttm"] == 14 + 15 + 16 + 17
    assert r["market_cap"] == 500
    assert r["pe"] == pytest.approx(500 / 62)
    assert r["roe"] == pytest.approx(62 / 200)
    assert r["debt_to_equity"] == 0.5
    assert r["current_ratio"] == 2
    assert r["fcf_yield"] == pytest.approx(40 / 500)
    assert r["revenue_growth"] == pytest.approx(620 / 460 - 1)  # vs 2024-03..2024-12


def test_no_pe_for_losses(financials):
    financials["net_income"] = -1.0
    assert key_ratios(financials, date(2026, 3, 1), price=50.0)["pe"] is None


def test_empty_before_data(financials):
    ratios = key_ratios(financials, date(2020, 1, 1), price=50.0)
    assert ratios and all(v is None for v in ratios.values())
