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


def test_recurring_earnings_preferred_over_reported(financials):
    # A one-off gain lifts reported income; normalized income excludes it.
    financials["net_income_normalized"] = -2.0
    r = key_ratios(financials, date(2026, 3, 1), price=50.0)
    assert r["net_income_ttm"] == 62 and r["earnings_ttm"] == -8
    assert r["pe"] is None  # no P/E on recurring losses
    # profitability is judged on recurring earnings too (IDEA's one-off gain showed an 82% net margin)
    assert r["net_margin"] < 0 and r["roe"] < 0


def test_earnings_growth_compares_like_with_like(financials):
    financials["net_income_normalized"] = financials["net_income"] / 2
    r = key_ratios(financials, date(2026, 3, 1), price=50.0)
    reported = key_ratios(financials.assign(net_income_normalized=float("nan")), date(2026, 3, 1), price=50.0)
    assert r["earnings_growth"] == pytest.approx(reported["earnings_growth"])  # both years halved
    assert r["net_margin"] == pytest.approx(reported["net_margin"] / 2)


def test_recurring_loss_is_a_red_flag_despite_reported_profit():
    from trademare.analysis.fundamental import score_universe
    from trademare.analysis.ratios import RATIO_KEYS

    base = dict.fromkeys(RATIO_KEYS) | dict(net_margin=0.1, roe=0.1, total_equity=100.0)
    ratios = {
        "IDEA": base | {"net_income_ttm": 50.0, "earnings_ttm": -30.0},
        "OK": base | {"net_income_ttm": 50.0, "earnings_ttm": 40.0},
    }
    scores = score_universe(ratios, {"IDEA": "Telecom", "OK": "Telecom"})
    assert scores["IDEA"].reasons[0].startswith("Red flag: loss-making")
    assert not any("loss-making" in r for r in scores["OK"].reasons)


def test_falls_back_to_reported_income_without_normalized(financials):
    r = key_ratios(financials, date(2026, 3, 1), price=50.0)
    assert r["earnings_ttm"] == r["net_income_ttm"] == 62
