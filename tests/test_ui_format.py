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


def test_plain_names():
    from tests.conftest import REPO_CONFIG
    from trademare.config import load_config
    from trademare.core.models import Market
    from trademare.web.formatting import factor_name, stop_rule, universe_name

    assert universe_name("nifty500") == "Nifty 500" and universe_name("odd") == "odd"
    assert factor_name("mom_12_1") == "12-1 month momentum" and factor_name("technical") == "Price trend"
    rule = stop_rule(load_config(REPO_CONFIG).markets[Market.IN])
    assert rule.startswith("stop-loss 3× the average daily range (ATR) below entry")  # noqa: RUF001
    assert "no profit target" in rule
