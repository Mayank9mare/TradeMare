"""Intraday module, phase 1: independence, settings, costs, candle cache and Kite loader."""

import ast
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from trademare.intraday import config as cfgmod
from trademare.intraday import costs
from trademare.intraday.kite_data import KiteCandles, KiteDataError
from trademare.intraday.store import CandleStore

SRC = Path(__file__).resolve().parents[1] / "src" / "trademare"


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_the_daily_system_never_imports_the_intraday_module():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "intraday" not in p.relative_to(SRC).parts
        and any(m.startswith("trademare.intraday") for m in _imports(p))
    ]
    assert offenders == []


def test_the_intraday_module_never_touches_trading_state():
    forbidden = ("trademare.trading", "trademare.storage", "trademare.scheduler", "trademare.web")
    for p in (SRC / "intraday").rglob("*.py"):
        bad = [m for m in _imports(p) if m.startswith(forbidden)]
        assert bad == [], f"{p.name} imports {bad}"


def test_settings_load_and_local_overrides(tmp_path):
    path = tmp_path / "intraday.yaml"
    path.write_text("interval: 5minute\npaper:\n  capital: 200000\n")
    cfg = cfgmod.load(path)
    assert cfg.minutes == 5 and cfg.paper.capital == 200000 and cfg.squareoff.hour == 15
    cfgmod.save_local({"paper": {"strategies": ["orb"]}}, path)
    again = cfgmod.load(path)
    assert again.paper.strategies == ["orb"] and again.paper.capital == 200000
    assert (tmp_path / "intraday.local.yaml").exists()
    with pytest.raises(ValueError):
        cfgmod.save_local({"interval": "day"}, path)


def test_repo_settings_are_valid():
    import yaml

    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "intraday.yaml").read_text())
    cfg = cfgmod.IntradayConfig.model_validate(raw)  # the committed file only, not personal overrides
    assert cfg.universe == "nifty50" and cfg.paper.strategies == []


def test_intraday_costs():
    # ₹50,000 round trip at the same price: brokerage ₹15 each side (0.03% < ₹20), STT on the sell.
    total = costs.round_trip(500.0, 500.0, 100, long=True)
    assert 50 < total < 60
    assert costs.order_cost(1_000_000, True) < costs.order_cost(1_000_000, False)  # STT on sells
    assert costs.round_trip_pct(500, 500, True, 50_000) == pytest.approx(total / 50_000)
    big = costs.order_cost(200_000, True)
    assert big < 200_000 * 0.0003 * 1.18 + 200_000 * 0.0001  # brokerage capped at ₹20


def candles(start, n, price=100.0):
    idx = pd.date_range(start, periods=n, freq="5min")
    return pd.DataFrame(
        {"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1e4}, index=idx
    )


def test_store_merges_and_newer_wins(tmp_path):
    store = CandleStore(tmp_path, "5minute")
    store.merge("ABC", candles("2026-10-30 09:15", 4))
    updated = candles("2026-10-30 09:25", 4, price=101.0)
    full = store.merge("ABC", updated)
    assert len(full) == 6 and full.loc["2026-10-30 09:25", "close"] == 101.0
    assert store.symbols() == ["ABC"]


class FakeKite:
    def __init__(self):
        self.calls = []

    def instruments(self, exchange):
        return [
            {"tradingsymbol": "ABC", "instrument_token": 11, "segment": "NSE", "instrument_type": "EQ"},
            {"tradingsymbol": "ABC-SM", "instrument_token": 12, "segment": "NSE", "instrument_type": "EQ"},
            {"tradingsymbol": "NIFTY", "instrument_token": 13, "segment": "INDICES", "instrument_type": "EQ"},
        ]

    def historical_data(self, token, start, end, interval):
        self.calls.append((token, start, end, interval))
        return [
            {"date": pd.Timestamp(start).tz_localize("Asia/Kolkata"), "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 10}
        ]  # fmt: skip


def test_kite_fetch_chunks_paces_and_strips_timezones(tmp_path):
    fake = FakeKite()
    waits = []
    k = KiteCandles(fake, tmp_path, sleep=waits.append)
    df = k.fetch("ABC", datetime(2026, 1, 1, 9, 15), datetime(2026, 9, 1, 15, 30))
    assert len(fake.calls) == 3 and all(c[0] == 11 and c[3] == "5minute" for c in fake.calls)
    assert fake.calls[1][1] > fake.calls[0][2]  # chunks do not overlap
    assert len(waits) == 3 and df.index.tz is None and df.index[0] == pd.Timestamp("2026-01-01 09:15")
    assert k.tokens() == {"ABC": 11, "ABC-SM": 12}
    with pytest.raises(KiteDataError):
        k.fetch("NOPE", datetime(2026, 1, 1), datetime(2026, 1, 2))
