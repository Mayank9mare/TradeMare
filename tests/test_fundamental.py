from trademare.analysis.fundamental import score_universe


def ratios(**kw):
    base = dict(
        roe=0.15,
        roa=0.08,
        operating_margin=0.2,
        net_margin=0.1,
        debt_to_equity=0.5,
        current_ratio=1.5,
        interest_coverage=8,
        revenue_growth=0.1,
        earnings_growth=0.1,
        pe=20,
        fcf_yield=0.04,
        price_to_book=3,
        net_income_ttm=100,
        total_equity=1000,
        data_age_days=60,
    )
    return base | kw


def universe(n=8, sector="Tech", **best):
    data = {f"S{i}": ratios(roe=0.05 + 0.02 * i, revenue_growth=0.02 * i) for i in range(n)}
    data["BEST"] = ratios(roe=0.4, revenue_growth=0.5, debt_to_equity=0.1, pe=12, **best)
    data["WORST"] = ratios(roe=0.01, revenue_growth=-0.3, debt_to_equity=3, pe=80, fcf_yield=-0.02)
    return data, {s: sector for s in data}


def test_ranking_orders_best_above_worst():
    data, sectors = universe()
    scores = score_universe(data, sectors)
    assert scores["BEST"].score > 0.3
    assert scores["WORST"].score < -0.2
    assert all(
        scores["WORST"].score < v.score < scores["BEST"].score for k, v in scores.items() if k.startswith("S")
    )
    assert any("#1 of 10 Tech peers" in r for r in scores["BEST"].reasons)


def test_small_sector_falls_back_to_market_ranking():
    data, sectors = universe()
    data["LONER"] = ratios(roe=0.5)
    sectors["LONER"] = "Tiny"
    reasons = score_universe(data, sectors)["LONER"].reasons
    assert any("in the market" in r for r in reasons)


def test_losses_capped_and_flagged():
    data, sectors = universe(**{"net_income_ttm": -5})
    best = score_universe(data, sectors)["BEST"]
    assert best.score <= -0.2
    assert best.reasons[0].startswith("Red flag: loss-making")


def test_negative_equity_with_profits_not_flagged():
    data, sectors = universe(total_equity=-50)
    best = score_universe(data, sectors)["BEST"]
    assert not any(r.startswith("Red flag") for r in best.reasons)
    assert any("buybacks" in r for r in best.reasons)


def test_financials_skip_leverage_metrics():
    data, sectors = universe(sector="Financials")
    s = score_universe(data, sectors)["WORST"]
    assert not any("Debt/equity" in r for r in s.reasons)


def test_stale_data_lowers_confidence():
    data, sectors = universe(data_age_days=400)
    fresh = score_universe(universe()[0], sectors)["BEST"].confidence
    stale = score_universe(data, sectors)["BEST"]
    assert stale.confidence < fresh
    assert any("Stale" in r for r in stale.reasons)


def test_empty():
    assert score_universe({}, {}) == {}
