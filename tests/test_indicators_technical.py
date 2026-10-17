import numpy as np
import pandas as pd
import pytest

from tests.helpers import make_bars
from trademare.analysis import indicators as ind
from trademare.analysis.technical import score_features, technical_features


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0)
    assert ind.rsi(pd.Series([5.0] * 30)).iloc[-1] == 50
    r = ind.rsi(make_bars(300)["close"]).dropna()
    assert r.between(0, 100).all()


def test_atr_matches_constant_range():
    bars = pd.DataFrame({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1}, index=range(50))
    assert ind.atr(bars).iloc[-1] == pytest.approx(2.0)


def test_indicators_are_causal():
    bars = make_bars(300)
    full = technical_features(bars)
    partial = technical_features(bars.iloc[:200])
    cols = ["sma50", "rsi", "atr", "adx", "high_prior", "vol_ratio"]
    pd.testing.assert_frame_equal(full[cols].iloc[:200], partial[cols], check_freq=False)


def test_uptrend_scores_positive_downtrend_negative():
    up = score_features(technical_features(make_bars(400, drift=0.002)).iloc[-1])
    down = score_features(technical_features(make_bars(400, drift=-0.002)).iloc[-1])
    assert up.score > 0.3 and down.score < -0.3
    assert any("Uptrend" in r for r in up.reasons)
    assert any("Downtrend" in r for r in down.reasons)


def test_relative_strength_uses_benchmark():
    stock = make_bars(400, drift=0.001, seed=1)
    strong_index = make_bars(400, drift=0.003, seed=2)
    row = technical_features(stock, strong_index).iloc[-1]
    assert row["rel_6m"] < row["ret_6m"]


def test_breakout_detected():
    bars = make_bars(300, drift=0.0, vol=0.005, seed=3)
    last = bars.index[-1]
    bars.loc[last, ["close", "high"]] = bars["high"].iloc[-60:-1].max() * 1.05
    bars.loc[last, "volume"] = bars["volume"].iloc[-21:-1].mean() * 3
    s = score_features(technical_features(bars).iloc[-1])
    assert s.components["setup"] > 0
    assert any("Breakout" in r for r in s.reasons)


def test_too_little_history_returns_none():
    assert score_features(technical_features(make_bars(40)).iloc[-1]) is None
