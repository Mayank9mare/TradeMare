"""Intraday module, phase 2: features and strategy rules on synthetic 5-minute candles."""

import numpy as np
import pandas as pd
import pytest

from trademare.intraday import features
from trademare.intraday.strategies import STRATEGIES, evaluate


def session_index(days, start="2026-01-05"):
    out = []
    for d in pd.bdate_range(start, periods=days):
        out += list(pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min"))
    return pd.DatetimeIndex(out)


def walk(days=30, seed=0, drift=0.0, vol=0.002, price=100.0):
    rng = np.random.default_rng(seed)
    idx = session_index(days)
    close = price * np.exp(np.cumsum(drift + vol * rng.standard_normal(len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + vol / 3)
    low = np.minimum(open_, close) * (1 - vol / 3)
    volume = 1e4 * (1 + 0.5 * rng.random(len(idx)))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


def test_pivots_come_from_the_previous_session():
    c = walk(3)
    f = features.build(c)
    day1 = c[c.index.normalize() == c.index.normalize()[0]]
    h, lo, cl = day1["high"].max(), day1["low"].min(), day1["close"].iloc[-1]
    p = (h + lo + cl) / 3
    second = f[f["day"] == f["day"].unique()[1]].iloc[0]
    assert second["P"] == pytest.approx(p) and second["R1"] == pytest.approx(2 * p - lo)
    assert second["S1"] == pytest.approx(2 * p - h)
    assert f[f["day"] == f["day"].unique()[0]]["P"].isna().all()


def test_vwap_of_a_flat_price_is_the_price_with_no_band():
    idx = session_index(1)
    c = pd.DataFrame({"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "volume": 1e3}, index=idx)
    f = features.build(c)
    assert (f["vwap"] == 50).all() and np.allclose(f["vwap_up2"], 50)


def test_hourly_rsi_only_changes_when_an_hour_closes():
    f = features.build(walk(20, seed=3))
    day = f[f["day"] == f["day"].unique()[-1]]
    first_hour = day.iloc[:11]["rsi_hour"]  # 09:15-10:10 candles: the hour is still open
    assert first_hour.nunique() == 1
    assert day.iloc[11]["rsi_hour"] != day.iloc[10]["rsi_hour"]  # the 10:10 candle closes the hour


def test_features_are_causal():
    c = walk(30, seed=4)
    full = features.build(c)
    part = features.build(c.iloc[: 75 * 20 + 30])
    cols = [
        "P",
        "R1",
        "vwap",
        "vwap_dn2",
        "rsi",
        "sma200",
        "st_line",
        "vol_osc",
        "rsi_hour",
        "cpr_rank",
        "or_high",
    ]
    pd.testing.assert_frame_equal(full[cols].iloc[: len(part)], part[cols], check_freq=False)


@pytest.mark.parametrize("sid", sorted(STRATEGIES))
def test_strategies_are_causal_and_sane(sid):
    for seed in range(3):
        c = walk(40, seed=seed, drift=0.0002 * (seed - 1), vol=0.003)
        full = evaluate(STRATEGIES[sid], features.build(c))
        n = 75 * 30 + 17
        part = evaluate(STRATEGIES[sid], features.build(c.iloc[:n]))
        cols = ["side", "trigger", "stop", "target", "exit_long", "exit_short"]
        pd.testing.assert_frame_equal(full[cols].iloc[:n], part[cols], check_freq=False)
        sig = full[full["side"] != 0]
        assert set(sig["side"].unique()) <= {-1, 1}
        assert ((sig["trigger"] - sig["stop"]) * sig["side"] > 0).all()
        tgt = sig.dropna(subset=["target"])
        assert ((tgt["target"] - tgt["trigger"]) * tgt["side"] > 0).all()


def test_opening_range_breakout_waits_for_the_range():
    idx = session_index(1)
    close = np.r_[[100, 101, 100.5], np.linspace(100.5, 103, 72)]
    c = pd.DataFrame(
        {"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1e4}, index=idx
    )
    out = evaluate(STRATEGIES["orb"], features.build(c))
    first = out.index[out["side"] == 1][0]
    assert first.time() >= pd.Timestamp("09:30").time()
    row = out.loc[first]
    assert row["stop"] == pytest.approx(99.8) and row["target"] == pytest.approx(
        row["trigger"] + 2 * (row["trigger"] - 99.8)
    )


def test_vwap_band_reversal():
    idx = session_index(1)
    close = np.r_[np.full(40, 100.0), [97.0, 97.6], np.full(33, 98.0)]  # a plunge, then a green candle
    open_ = np.r_[np.full(40, 100.0), [100.0, 97.1], np.full(33, 98.0)]
    high, low = np.maximum(open_, close) + 0.05, np.minimum(open_, close) - 0.05
    c = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e4}, index=idx)
    out = evaluate(STRATEGIES["vwap_bands"], features.build(c))
    assert out["side"].iloc[41] == 1 and out["target"].iloc[41] > out["trigger"].iloc[41]
    assert out["stop"].iloc[41] == pytest.approx(97.05)
