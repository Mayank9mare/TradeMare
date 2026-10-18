from datetime import UTC, datetime

from trademare.web.formatting import local_time, run_label


def test_local_time_in_market_zone():
    t = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
    assert local_time(t, "IN") == "Mon 19 Oct, 15:30 IST"
    assert local_time(t.replace(tzinfo=None), "US") == "Mon 19 Oct, 06:00 New York"  # naive = UTC
    assert local_time(None, "IN") == "—"


def test_run_label_names_what_a_backtest_tested():
    overrides = [
        "markets.US.strategy_mode=factor", "markets.US.factors=[mom_12_1,earnings_yield]",
        "markets.US.budget=100000", "strategy.vol_target=true", "strategy.exclude_high_vol_pct=0.2",
        "markets.US.take_profit.method=none",
    ]  # fmt: skip
    assert run_label(overrides) == "momentum + earnings yield · vol targeting · skip top 20% volatile"
    assert run_label([]) == "settings as configured"
