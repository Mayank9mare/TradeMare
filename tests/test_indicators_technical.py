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


def test_supertrend_follows_the_trend_and_flips():
    up = ind.supertrend(make_bars(200, drift=0.004))
    assert up["up"].iloc[-1] and up["line"].iloc[-1] < make_bars(200, drift=0.004)["close"].iloc[-1]
    falling = make_bars(300, drift=-0.004, seed=3)
    down = ind.supertrend(falling)
    assert not down["up"].iloc[-1] and down["line"].iloc[-1] > falling["close"].iloc[-1]
    # The lower band never falls while the trend stays up.
    st = ind.supertrend(make_bars(300, drift=0.003, seed=2))
    run = st[st["up"]]["line"]
    same_run = (st["up"] == st["up"].shift()).loc[run.index]
    assert (run.diff()[same_run.to_numpy()].dropna() >= -1e-9).all()


def test_bollinger_ichimoku_williams_basics():
    bars = make_bars(300)
    bb = ind.bollinger(bars["close"])
    valid = bb.dropna()
    assert (valid["upper"] >= valid["mid"]).all() and (bb["width"].dropna() > 0).all()
    flat = ind.bollinger(pd.Series([10.0] * 30))
    assert flat["width"].iloc[-1] == 0
    ich = ind.ichimoku(bars)
    assert ich["span_a"].first_valid_index() > ich["base"].first_valid_index()
    wr = ind.williams_r(bars).dropna()
    assert wr.between(-100, 0).all()


def test_new_indicators_are_causal():
    bars = make_bars(300, seed=4)
    for full, part in [
        (ind.supertrend(bars), ind.supertrend(bars.iloc[:200])),
        (ind.bollinger(bars["close"]), ind.bollinger(bars["close"].iloc[:200])),
        (ind.ichimoku(bars), ind.ichimoku(bars.iloc[:200])),
    ]:
        pd.testing.assert_frame_equal(full.iloc[:200], part, check_freq=False)
