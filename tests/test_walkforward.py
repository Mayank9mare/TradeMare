import pandas as pd
import pytest

from trademare.backtest.walkforward import walk_forward


def curve(daily_returns: dict[int, float]) -> pd.Series:
    """Equity from a constant daily return per calendar year."""
    idx = pd.bdate_range("2015-01-01", "2019-12-31")
    rets = pd.Series([daily_returns[ts.year] for ts in idx], index=idx)
    noise = pd.Series([0.001 if i % 2 else -0.001 for i in range(len(idx))], index=idx)
    return 100 * (1 + rets + noise).cumprod()


def test_picks_trailing_winner_and_never_looks_ahead():
    # A wins 2015-2016, B wins 2017-2019. Selection for year Y must use only years before Y.
    a = curve({2015: 0.002, 2016: 0.002, 2017: -0.001, 2018: -0.001, 2019: -0.001})
    b = curve({2015: 0.0, 2016: 0.0, 2017: 0.002, 2018: 0.002, 2019: 0.002})
    wf = walk_forward({"A": a, "B": b}, lookback_years=1)
    picks = {c.year: c.variant for c in wf.choices}
    assert 2015 not in picks  # no history yet
    assert picks[2016] == "A" and picks[2017] == "A"  # 2017 is chosen on 2016, before B's run starts
    assert picks[2018] == "B" and picks[2019] == "B"
    year_2017 = a[a.index.year >= 2016]
    expected = (
        year_2017[year_2017.index.year == 2017].iloc[-1] / year_2017[year_2017.index.year == 2016].iloc[-1]
        - 1
    )
    got = next(c.year_return for c in wf.choices if c.year == 2017)
    assert got == pytest.approx(expected, rel=1e-9)


def test_benchmark_covers_the_same_sessions():
    a = curve({y: 0.001 for y in range(2015, 2020)})
    bench = curve({y: 0.0005 for y in range(2015, 2020)})
    wf = walk_forward({"A": a}, benchmark=bench, lookback_years=1)
    assert wf.benchmark.index.equals(wf.equity.index)
    assert wf.metrics()["benchmark"]["total_return"] < wf.metrics()["strategy"]["total_return"]
