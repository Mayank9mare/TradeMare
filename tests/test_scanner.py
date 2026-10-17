from datetime import date

import pandas as pd
import pytest

from tests.conftest import REPO_CONFIG
from tests.helpers import make_bars
from trademare.analysis.regime import Regime, RegimeLabel
from trademare.config import Secrets, load_config
from trademare.core.interfaces import AnalystResult
from trademare.core.models import Direction, Market
from trademare.data.fields import normalize_financials
from trademare.data.service import DataService
from trademare.strategy.scanner import SymbolScore, combine, decide, load_latest_scan, run_scan, save_scan


def result(name, score, conf=1.0, setup=0.0):
    return AnalystResult(analyst=name, score=score, confidence=conf, details={"setup": setup})


def regime(label):
    return Regime(label=label, score=0, index_trend=None, breadth=None, reasons=[])


@pytest.fixture
def config():
    return load_config(REPO_CONFIG)


def test_combine_renormalises_missing_analysts():
    composite, conf = combine(
        {"technical": result("technical", 0.8)}, {"technical": 0.5, "fundamental": 0.35}
    )
    assert composite == pytest.approx(0.8) and conf == 1.0
    assert combine({}, {"technical": 1}) == (None, 0.0)


def symbol_score(tech, fund, composite, setup=0.0, liquid=True):
    analysts = {"technical": result("technical", tech, setup=setup)}
    if fund is not None:
        analysts["fundamental"] = result("fundamental", fund)
    return SymbolScore(
        symbol="X",
        name="X",
        sector="S",
        close=1,
        composite=composite,
        confidence=1,
        liquid=liquid,
        analysts=analysts,
    )


def test_decide_rules(config):
    cfg = config.markets[Market.US]
    on, off = regime(RegimeLabel.RISK_ON), regime(RegimeLabel.RISK_OFF)
    assert decide(symbol_score(0.6, 0.3, 0.5), cfg, config, on) is Direction.LONG
    assert decide(symbol_score(0.6, 0.3, 0.5), cfg, config, off) is None  # needs 0.55 in risk-off
    assert decide(symbol_score(0.6, -0.1, 0.5), cfg, config, on) is None  # fundamental gate
    assert decide(symbol_score(0.6, 0.3, 0.5, setup=-0.5), cfg, config, on) is None  # don't chase
    assert decide(symbol_score(0.6, 0.3, 0.5, liquid=False), cfg, config, on) is None
    assert decide(symbol_score(0.6, None, 0.6), cfg, config, on) is None  # fundamentals required
    # shorts are off by default, allowed once enabled
    assert decide(symbol_score(-0.6, -0.3, -0.5), cfg, config, off) is None
    shorts = cfg.model_copy(update={"allow_short": True})
    assert decide(symbol_score(-0.6, -0.3, -0.5), shorts, config, off) is Direction.SHORT
    assert decide(symbol_score(-0.6, -0.3, -0.5), shorts, config, on) is None  # needs -0.55 in risk-on


def test_run_scan_end_to_end(tmp_path, monkeypatch, config):
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True)
    symbols = [f"S{i}" for i in range(8)]
    rows = "\n".join(f"{s},{s} Inc,Tech,000000000{i}" for i, s in enumerate(symbols))
    (universes / "sp500.csv").write_text("symbol,name,sector,cik\n" + rows + "\n")
    monkeypatch.chdir(tmp_path)

    config = config.model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "data" / "cache")
    service = DataService(config, Secrets(_env_file=None))
    service.store.write_bars(Market.US, "^GSPC", make_bars(400, drift=0.0005, seed=99))
    for i, s in enumerate(symbols):
        service.store.write_bars(Market.US, s, make_bars(400, drift=0.003 - 0.001 * i, volume=1e6, seed=i))
        ends = pd.date_range("2023-12-31", periods=8, freq="QE")
        fin = pd.DataFrame(
            [
                dict(
                    period_type="Q",
                    period_end=e,
                    available_from=e + pd.Timedelta(days=40),
                    revenue=100 * (1 + 0.05 * (8 - i)) ** k,
                    net_income=10 + 2 * (8 - i),
                    total_equity=500,
                    total_assets=1000,
                    shares_outstanding=1e6,
                )
                for k, e in enumerate(ends)
            ]
        )
        service.store.write_financials(Market.US, "edgar", s, normalize_financials(fin))

    result = run_scan(service, Market.US)
    assert result.asof == make_bars(400).index[-1].date()
    assert len(result.scores) == 8 and all(s.composite is not None for s in result.scores)
    by = {s.symbol: s.composite for s in result.scores}
    assert by["S0"] > by["S7"]
    for idea in result.ideas:
        assert idea.direction is Direction.LONG
        assert idea.levels.stop < idea.levels.entry

    path = save_scan(service, result)
    assert path.exists()
    assert load_latest_scan(service, Market.US).asof == result.asof

    # Point-in-time: an earlier as-of date scores on earlier data.
    early = run_scan(service, Market.US, date(2024, 1, 15))
    assert early.asof == date(2024, 1, 15)
