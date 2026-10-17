from datetime import date

import numpy as np
import pandas as pd
import pytest

from tests.conftest import REPO_CONFIG
from trademare.analysis.regime import Regime, RegimeLabel
from trademare.backtest.costs import IndiaDeliveryCosts, USEquityCosts
from trademare.backtest.engine import Backtester, Trade
from trademare.backtest.metrics import curve_metrics, summarize, trade_metrics
from trademare.backtest.report import apply_overrides
from trademare.config import load_config
from trademare.config.schema import MarketConfig
from trademare.core.models import Direction, Market, OrderSide
from trademare.risk.levels import Levels
from trademare.risk.sizing import position_size
from trademare.strategy.scanner import Idea, ScanResult


# ---------------------------------------------------------------- sizing / costs
def cfg(**kw) -> MarketConfig:
    return MarketConfig(broker="paper", universe="x", budget=100_000, **kw)


def test_position_size_is_risk_based():
    # 1% of 100k = 1000 risk; 5/share -> 200 shares (position 20k > 15% cap -> 150)
    assert position_size(100, 95, 100_000, 100_000, cfg()) == 150
    assert position_size(100, 90, 100_000, 100_000, cfg()) == 100  # 1000/10
    assert position_size(100, 90, 100_000, 5_000, cfg()) == 50  # cash-limited
    assert position_size(100, 90, 100_000, 100_000, cfg(), sector_exposure=29_000) == 10  # sector cap 30%
    assert position_size(100, 90, 100_000, 100_000, cfg(), gross_exposure=89_000) == 10  # 10% cash buffer
    assert position_size(100, 100, 100_000, 100_000, cfg()) == 0


def test_india_costs():
    c = IndiaDeliveryCosts()
    buy = c.cost(OrderSide.BUY, 1000, 100)  # 1L buy: STT 100 + stamp 15 + exch/sebi ~3.07 + GST ~0.55
    sell = c.cost(OrderSide.SELL, 1000, 100)
    assert buy == pytest.approx(118.6, abs=0.5)
    assert sell == pytest.approx(100 + 3.07 + 0.55 + 15.93, abs=0.5)


def test_us_costs_only_on_sells():
    c = USEquityCosts()
    assert c.cost(OrderSide.BUY, 100, 100) == 0
    assert c.cost(OrderSide.SELL, 100, 100) == pytest.approx(10_000 * 27.8e-6 + 100 * 0.000166)
    assert c.cost(OrderSide.SELL, 1, 1_000_000) == pytest.approx(27.8 + 8.30)  # TAF capped


# ---------------------------------------------------------------- engine
class FakeContext:
    """Minimal ScanContext: fixed bars and a scripted list of ideas per date."""

    def __init__(self, bars: dict[str, pd.DataFrame], ideas: dict[date, list[Idea]], config, composites=None):
        self.bars = bars
        self.market = Market.US
        self.config = config
        self.benchmark = None
        self._ideas = ideas
        self._composites = composites or {}

    def sessions(self, start, end):
        idx = next(iter(self.bars.values())).index
        return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]

    def fundamental_scores(self, asof):
        return {}

    def evaluate(self, asof, fundamentals=None, keep_scores=True):
        from trademare.strategy.scanner import SymbolScore

        scores = [
            SymbolScore(symbol=s, name=s, sector="T", close=1, composite=c, confidence=1, liquid=True)
            for s, c in self._composites.get(asof, {}).items()
        ]
        regime = Regime(label=RegimeLabel.NEUTRAL, score=0, index_trend=None, breadth=None, reasons=[])
        return ScanResult(
            market=Market.US,
            asof=asof,
            regime=regime,
            weights={},
            ideas=self._ideas.get(asof, []),
            scores=scores,
        )


def ohlc(rows):
    idx = pd.bdate_range("2025-01-06", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e6)


def idea(symbol, entry, stop, target, direction=Direction.LONG):
    lv = Levels(
        entry=entry, stop=stop, target=target, risk_per_share=abs(entry - stop), stop_method="percent"
    )
    return Idea(
        symbol=symbol,
        name=symbol,
        sector="T",
        direction=direction,
        composite=0.6,
        confidence=1,
        levels=lv,
        reasons={},
    )


@pytest.fixture
def config():
    c = load_config(REPO_CONFIG).model_copy(deep=True)
    us = c.markets[Market.US]
    us.budget = 100_000
    us.slippage_bps = 0
    us.stop_loss.trailing = False
    us.time_stop_days = None
    return c


def run(config, bars, ideas, composites=None):
    ctx = FakeContext(bars, ideas, config, composites)
    sessions = ctx.sessions(date(2000, 1, 1), date(2100, 1, 1))
    return Backtester(ctx, config).run(sessions[0].date(), sessions[-1].date())


def test_limit_entry_then_target(config):
    bars = {
        "A": ohlc(
            [[100, 101, 99, 100], [100.5, 101, 99.5, 100.5], [101, 112, 100, 111], [111, 112, 110, 111]]
        )
    }
    d0 = bars["A"].index[0].date()
    result = run(config, bars, {d0: [idea("A", 100, 95, 110)]})
    (t,) = result.trades
    assert t.entry_price == 100  # open 100.5 above limit, low 99.5 reached it
    assert t.exit_price == 110 and t.exit_reason == "target"
    assert t.qty == 150  # 15% position cap binds before 1% risk (200 shares)
    assert t.r_multiple == pytest.approx(2.0, abs=0.01)


def test_gap_through_limit_means_no_fill(config):
    bars = {"A": ohlc([[100, 101, 99, 100], [103, 105, 102, 104], [104, 105, 103, 104]])}
    result = run(config, bars, {bars["A"].index[0].date(): [idea("A", 100, 95, 110)]})
    assert result.trades == [] and result.open_positions == []


def test_stop_and_target_same_day_assumes_stop(config):
    bars = {"A": ohlc([[100, 101, 99, 100], [100, 100, 99.8, 100], [100, 115, 90, 100], [100, 101, 99, 100]])}
    result = run(config, bars, {bars["A"].index[0].date(): [idea("A", 100, 95, 110)]})
    assert result.trades[0].exit_reason == "stop" and result.trades[0].exit_price == 95


def test_gap_down_through_stop_fills_at_open(config):
    bars = {"A": ohlc([[100, 101, 99, 100], [100, 100, 99.8, 100], [90, 91, 88, 89], [89, 90, 88, 89]])}
    result = run(config, bars, {bars["A"].index[0].date(): [idea("A", 100, 95, 110)]})
    t = result.trades[0]
    assert t.exit_reason == "stop (gap)" and t.exit_price == 90


def test_short_trade_profits_when_price_falls(config):
    config.markets[Market.US].allow_short = True
    bars = {"A": ohlc([[100, 101, 99, 100], [99.5, 100.5, 99, 99.5], [99, 99.5, 89, 90], [90, 91, 89, 90]])}
    result = run(config, bars, {bars["A"].index[0].date(): [idea("A", 100, 105, 90, Direction.SHORT)]})
    t = result.trades[0]
    assert t.direction == "SHORT" and t.exit_reason == "target" and t.pnl > 0
    assert result.equity.iloc[-1] > 100_000


def test_signal_exit_next_open(config):
    bars = {
        "A": ohlc([[100, 101, 99, 100], [100, 101, 99.5, 100], [100, 101, 99, 100.5], [102, 103, 101, 102]])
    }
    d = [ts.date() for ts in bars["A"].index]
    result = run(config, bars, {d[0]: [idea("A", 100, 95, 120)]}, composites={d[2]: {"A": -0.1}})
    t = result.trades[0]
    assert t.exit_reason == "signal" and t.exit_date == d[3] and t.exit_price == 102


def test_equity_accounts_for_costs(config):
    bars = {"A": ohlc([[100, 101, 99, 100]] * 3 + [[100, 112, 100, 111], [111, 112, 110, 111]])}
    result = run(config, bars, {bars["A"].index[0].date(): [idea("A", 100, 95, 110)]})
    t = result.trades[0]
    assert result.equity.iloc[-1] == pytest.approx(100_000 + t.pnl)
    assert t.costs > 0


# ---------------------------------------------------------------- metrics / overrides
def test_curve_metrics():
    idx = pd.bdate_range("2024-01-01", periods=253)
    eq = pd.Series(np.linspace(100, 110, 253), index=idx)
    eq.iloc[100] = 90  # one-day drawdown
    m = curve_metrics(eq)
    assert m["total_return"] == pytest.approx(0.10)
    assert m["max_drawdown"] < -0.1
    assert m["sharpe"] is not None


def trade(pnl, r=1.0):
    return Trade(
        "A", "T", "LONG", date(2024, 1, 1), 100, date(2024, 1, 5), 100, 1, pnl, pnl / 100, r, 0, 4, "x", 0.5
    )


def test_trade_metrics():
    m = trade_metrics([trade(20, 2), trade(-10, -1), trade(-10, -1)])
    assert m["win_rate"] == pytest.approx(1 / 3)
    assert m["profit_factor"] == pytest.approx(1.0)
    assert m["avg_r"] == 0
    assert trade_metrics([]) == {"trades": 0.0}


def test_summarize_has_splits(config):
    bars = {"A": ohlc([[100, 101, 99, 100]] * 40)}
    s = summarize(run(config, bars, {}), out_of_sample_fraction=0.4)
    assert "in_sample" in s and "out_of_sample" in s and "2025" in s["by_year"]


def test_apply_overrides():
    c = load_config(REPO_CONFIG)
    new = apply_overrides(c, ["strategy.min_composite_score=0.55", "markets.US.stop_loss.method=percent"])
    assert new.strategy.min_composite_score == 0.55
    assert new.markets[Market.US].stop_loss.method == "percent"
    with pytest.raises(ValueError, match="unknown config key"):
        apply_overrides(c, ["strategy.nope=1"])
    with pytest.raises(ValueError):
        apply_overrides(c, ["strategy.min_composite_score=5"])  # fails validation
