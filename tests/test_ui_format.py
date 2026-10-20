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


def test_tone_neutral_near_zero_and_vs_benchmark():
    from trademare.web.formatting import tone, vs

    assert tone(-0.0003) == "" and tone(-0.01) == "down" and tone(5.0) == "up"
    assert vs(0.07, 0.121) == "down" and vs(0.15, 0.12) == "up"
    assert vs(-0.239, -0.198) == "down"  # deeper drawdown is worse
    assert vs(0.20, 0.15, higher_better=False) == "down" and vs(None, 0.1) == ""


def test_link_symbol_links_first_mention_and_escapes():
    from trademare.web.formatting import link_symbol

    out = str(link_symbol("Buy 3 M&M at <limit>; M&M again", "IN", "M&M"))
    assert out.startswith('Buy 3 <a href="/m/IN/M%26M"><b>M&amp;M</b></a> at &lt;limit&gt;')
    assert out.endswith("; M&amp;M again")
