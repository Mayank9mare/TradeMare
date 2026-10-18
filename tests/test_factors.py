from datetime import date

import pandas as pd
import pytest

from tests.conftest import REPO_CONFIG
from tests.helpers import make_bars
from trademare.analysis.factors import FACTORS, combine_ranks, factor_values
from trademare.analysis.regime import Regime, RegimeLabel
from trademare.backtest.engine import Backtester
from trademare.config import Secrets, load_config
from trademare.config.schema import Rebalance
from trademare.core.models import Direction, Market
from trademare.data.fields import normalize_financials
from trademare.data.service import DataService
from trademare.risk.levels import Levels
from trademare.strategy.scanner import Idea, ScanContext, ScanResult, SymbolScore, is_rebalance_day


def test_low_vol_is_oriented_higher_is_better():
    calm = FACTORS["low_vol"].value({"vol_252": 0.15}, None)
    wild = FACTORS["low_vol"].value({"vol_252": 0.60}, None)
    assert calm > wild


def test_earnings_yield_needs_market_cap():
    ey = FACTORS["earnings_yield"].value
    assert ey({}, {"net_income_ttm": 10.0, "market_cap": 200.0}) == pytest.approx(0.05)
    assert ey({}, {"net_income_ttm": 10.0, "market_cap": None}) is None
    assert ey({}, None) is None


def test_combine_ranks_averages_available_factors():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, None]}, index=["X", "Y", "Z"])
    score = combine_ranks(df, ["a", "b"])
    # X: a lowest (1/3), b highest (1) -> mean 2/3; Z: only a, highest (1)
    assert score["Z"] == pytest.approx(1.0)
    assert score["X"] == pytest.approx(2 * (2 / 3) - 1)


@pytest.mark.parametrize(
    ("prev", "today", "freq", "expected"),
    [
        (None, date(2025, 1, 2), Rebalance.MONTHLY, True),
        (date(2025, 1, 30), date(2025, 1, 31), Rebalance.MONTHLY, False),
        (date(2025, 1, 31), date(2025, 2, 3), Rebalance.MONTHLY, True),
        (date(2025, 1, 3), date(2025, 1, 6), Rebalance.WEEKLY, True),
        (date(2025, 1, 6), date(2025, 1, 7), Rebalance.WEEKLY, False),
        (date(2025, 1, 6), date(2025, 1, 7), Rebalance.DAILY, True),
    ],
)
def test_rebalance_calendar(prev, today, freq, expected):
    assert is_rebalance_day(prev, today, freq) is expected


@pytest.fixture
def factor_service(tmp_path, monkeypatch):
    """12 synthetic stocks: S0 has the strongest momentum, S11 the weakest; S0 trades least."""
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True)
    symbols = [f"S{i}" for i in range(12)]
    (universes / "nifty500.csv").write_text(
        "symbol,name,sector,cik\n"
        + "\n".join(f"{s},{s} Ltd,Sector{i % 3}," for i, s in enumerate(symbols))
        + "\n"
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "data" / "cache")
    india = config.markets[Market.IN]
    india.universe, india.factors, india.min_avg_traded_value = "nifty500", ["mom_12_1"], 0
    india.universe_top_n = None
    service = DataService(config, Secrets(_env_file=None))
    service.store.write_bars(Market.IN, "^NSEI", make_bars(500, drift=0.0005, seed=99))
    for i, s in enumerate(symbols):
        volume = 1e3 if i == 0 else 1e6
        bars = make_bars(500, drift=0.003 - 0.0005 * i, vol=0.002, volume=volume, seed=i)
        service.store.write_bars(Market.IN, s, bars)
        service.store.write_financials(Market.IN, "yfinance", s, normalize_financials(pd.DataFrame()))
    return service


def test_factor_scan_ranks_by_momentum(factor_service):
    result = ScanContext(factor_service, Market.IN).evaluate(make_bars(500).index[-1].date())
    assert result.mode == "factor"
    by = {s.symbol: s.composite for s in result.scores}
    assert by["S0"] > by["S6"] > by["S11"]
    # Top 10% means percentile >= 0.9: with 12 names that is the top 2 (11/12 = 0.917).
    assert [i.symbol for i in result.ideas] == ["S0", "S1"]
    assert result.ideas[0].levels.target is None  # take_profit: none in settings.yaml
    assert "12-1 month momentum" in result.ideas[0].reasons["factor"][0]
    assert result.exit_long_below == pytest.approx(1 - 2 * 0.30)


def test_universe_top_n_excludes_small_names(factor_service):
    factor_service.config.markets[Market.IN].universe_top_n = 11
    ctx = ScanContext(factor_service, Market.IN)
    asof = make_bars(500).index[-1].date()
    values = factor_values(ctx, asof, ["mom_12_1"])
    assert not values.at["S0", "liquid"]  # least traded name falls outside the top 11
    assert [i.symbol for i in ctx.evaluate(asof).ideas] == ["S1", "S2"]


# ---------------------------------------------------------------- engine in factor mode
class CountingContext:
    def __init__(self, config, bars, composites, regime=RegimeLabel.NEUTRAL, ideas=None):
        self.config, self.bars, self.market, self.benchmark = config, bars, Market.IN, None
        self.calls: list[date] = []
        self._composites, self._regime, self._ideas = composites, regime, ideas or []

    def sessions(self, start, end):
        idx = next(iter(self.bars.values())).index
        return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]

    def fundamental_scores(self, asof):
        raise AssertionError("factor mode must not compute v1 fundamental scores")

    def evaluate(self, asof, fundamentals=None, keep_scores=True):
        self.calls.append(asof)
        scores = [
            SymbolScore(symbol=s, name=s, sector="T", close=1, composite=c, confidence=1, liquid=True)
            for s, c in self._composites.items()
        ]
        regime = Regime(label=self._regime, score=0, index_trend=None, breadth=None, reasons=[])
        ideas = self._ideas if len(self.calls) == 1 else []
        return ScanResult(
            market=Market.IN,
            asof=asof,
            regime=regime,
            weights={},
            ideas=ideas,
            scores=scores,
            mode="factor",
            exit_long_below=0.4,
            exit_short_above=-0.4,
        )


def flat_bars(n=60):
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6}, index=idx)


@pytest.fixture
def factor_config():
    c = load_config(REPO_CONFIG).model_copy(deep=True)
    c.markets[Market.IN].budget = 1_000_000
    c.markets[Market.IN].slippage_bps = 0
    return c


def test_engine_scans_only_on_rebalance_days(factor_config):
    ctx = CountingContext(factor_config, {"A": flat_bars()}, {"A": 0.9})
    Backtester(ctx, factor_config).run(date(2025, 1, 1), date(2025, 3, 26))
    assert [d.month for d in ctx.calls] == [1, 2, 3]  # once per month


def test_engine_trims_positions_in_risk_off(factor_config):
    bars = {s: flat_bars() for s in "ABCD"}
    lv = Levels(entry=100, stop=90, target=None, risk_per_share=10, stop_method="atr")
    ideas = [
        Idea(
            symbol=s,
            name=s,
            sector=s,
            direction=Direction.LONG,
            composite=c,
            confidence=1,
            levels=lv,
            reasons={},
        )
        for s, c in zip("ABCD", (0.95, 0.9, 0.85, 0.8), strict=True)
    ]
    factor_config.markets[Market.IN].max_open_positions = 4
    factor_config.strategy.risk_off_exposure = 0.5
    composites = {"A": 0.95, "B": 0.9, "C": 0.85, "D": 0.8}
    ctx = CountingContext(factor_config, bars, composites, RegimeLabel.RISK_OFF, ideas)
    result = Backtester(ctx, factor_config).run(date(2025, 1, 1), date(2025, 3, 26))
    # Risk-off allows 2 positions: first rebalance fills 2 (slots), none trimmed later.
    held = {p.symbol for p in result.open_positions}
    assert held == {"A", "B"}


def test_earnings_yield_prefers_recurring_earnings():
    ey = FACTORS["earnings_yield"].value
    assert ey({}, {"earnings_ttm": -5.0, "net_income_ttm": 50.0, "market_cap": 100.0}) == pytest.approx(-0.05)
    assert ey({}, {"earnings_ttm": None, "net_income_ttm": 10.0, "market_cap": 100.0}) == pytest.approx(0.10)


# ---------------------------------------------------------------- strategy v3 risk controls
def test_slot_limit_combines_regime_and_volatility(factor_config):
    from trademare.risk.manager import slot_limit

    cfg, strat = factor_config.markets[Market.IN], factor_config.strategy
    cfg.max_open_positions, strat.risk_off_exposure = 10, 0.5
    assert slot_limit(cfg, strat, RegimeLabel.NEUTRAL) == 10
    assert slot_limit(cfg, strat, RegimeLabel.RISK_OFF) == 5
    assert slot_limit(cfg, strat, RegimeLabel.NEUTRAL, 0.6) == 6
    assert slot_limit(cfg, strat, RegimeLabel.RISK_OFF, 0.6) == 3
    assert slot_limit(cfg, strat, RegimeLabel.NEUTRAL, 1.7) == 10  # never above the maximum


def test_vol_scale_shrinks_when_index_turns_volatile(factor_service):
    idx = pd.bdate_range("2020-01-01", periods=600)
    rng = __import__("numpy").random.default_rng(1)
    returns = rng.normal(0, 0.01, 600)
    returns[-63:] = rng.normal(0, 0.03, 63)  # last quarter three times as volatile
    close = 100 * (1 + pd.Series(returns, index=idx)).cumprod()
    bench = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1e6})
    factor_service.store.write_bars(Market.IN, "^NSEI", bench)
    factor_service.config.strategy.vol_target = False
    ctx = ScanContext(factor_service, Market.IN)
    assert ctx.vol_scale_at(idx[-1]) == 1.0  # switched off
    factor_service.config.strategy.vol_target = True
    ctx = ScanContext(factor_service, Market.IN)
    assert ctx.vol_scale_at(idx[-1]) == pytest.approx(1 / 3, abs=0.08)
    assert ctx.vol_scale_at(idx[-100]) == 1.0  # calm period
    assert ctx.vol_scale_at(idx[100]) == 1.0  # not enough history yet


def test_high_vol_exclusion_blocks_entries_only(factor_service):
    asof = make_bars(500).index[-1].date()
    # Make the top-momentum name S0 the most volatile stock.
    noisy = make_bars(500, drift=0.003, vol=0.04, volume=1e6, seed=0)
    factor_service.store.write_bars(Market.IN, "S0", noisy)
    factor_service.config.strategy.exclude_high_vol_pct = 0.1
    result = ScanContext(factor_service, Market.IN).evaluate(asof)
    assert "S0" not in [i.symbol for i in result.ideas]
    assert any(s.symbol == "S0" and s.composite is not None for s in result.scores)  # still ranked


def test_engine_uses_vol_scale_for_slots(factor_config):
    bars = {s: flat_bars() for s in "ABCD"}
    lv = Levels(entry=100, stop=90, target=None, risk_per_share=10, stop_method="atr")
    ideas = [
        Idea(
            symbol=s,
            name=s,
            sector=s,
            direction=Direction.LONG,
            composite=c,
            confidence=1,
            levels=lv,
            reasons={},
        )
        for s, c in zip("ABCD", (0.95, 0.9, 0.85, 0.8), strict=True)
    ]
    factor_config.markets[Market.IN].max_open_positions = 4

    class Volatile(CountingContext):
        def evaluate(self, asof, fundamentals=None, keep_scores=True):
            return super().evaluate(asof, fundamentals, keep_scores).model_copy(update={"vol_scale": 0.5})

    ctx = Volatile(factor_config, bars, {"A": 0.95, "B": 0.9, "C": 0.85, "D": 0.8}, ideas=ideas)
    result = Backtester(ctx, factor_config).run(date(2025, 1, 1), date(2025, 3, 26))
    assert {p.symbol for p in result.open_positions} == {"A", "B"}
