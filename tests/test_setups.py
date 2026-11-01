import numpy as np
import pandas as pd
import pytest

from tests.helpers import make_bars
from trademare.strategy.setups import SETUPS, SetupContext, evaluate


def bars_from(closes, volumes=None, wick=0.005, start="2023-01-02"):
    close = np.asarray(closes, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    idx = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1 + wick),
            "low": np.minimum(open_, close) * (1 - wick),
            "close": close,
            "volume": np.full(len(close), 1e6) if volumes is None else np.asarray(volumes, dtype=float),
        },
        index=idx,
    )


@pytest.mark.parametrize("setup_id", sorted(SETUPS))
def test_every_setup_is_causal_and_its_trades_are_sane(setup_id):
    setup = SETUPS[setup_id]
    for seed in range(6):
        bars = make_bars(500, drift=0.0008 * (seed % 3 - 1), vol=0.02, seed=seed)
        ctx = SetupContext({"X": bars}, {"X": "Tech"}, make_bars(500, seed=99))
        full = evaluate(setup, bars, ctx, "X")
        part = evaluate(setup, bars.iloc[:350], SetupContext({"X": bars.iloc[:350]}, {"X": "Tech"},
                        make_bars(500, seed=99).iloc[:350]), "X")  # fmt: skip
        cols = ["signal", "stop", "target", "exit"]
        pd.testing.assert_frame_equal(full[cols].iloc[:350], part[cols], check_freq=False)
        sig = full[full["signal"]]
        assert (sig["stop"] < sig["entry"]).all()
        assert (sig["target"].isna() | (sig["target"] > sig["entry"])).all()


def test_rally_base_breakout_fires_on_the_breakout_day_only():
    closes = list(np.linspace(100, 100, 80)) + list(np.linspace(100, 125, 25))  # rally of 25%
    closes += [125 + (1 if i % 2 else -1) for i in range(20)]  # quiet 20-day base around 125
    closes += [131]  # breakout close above the base high
    volumes = [1e6] * 105 + [5e5] * 20 + [3e6]
    f = evaluate(SETUPS["rally_base_breakout"], bars_from(closes, volumes))
    assert f["signal"].iloc[-1] and f["signal"].sum() == 1
    row = f.iloc[-1]
    assert row["stop"] <= row["entry"] * 0.95  # at least 5% below
    assert row["target"] == pytest.approx(row["entry"] + 3 * (row["entry"] - row["stop"]))


def test_rsi_w_needs_a_shallower_second_dip(monkeypatch):
    from trademare.strategy import setups

    def with_rsi(path):
        values = [50.0] * 10 + path
        monkeypatch.setattr(setups.ind, "rsi", lambda close, n=14: pd.Series(values, index=close.index))
        return evaluate(SETUPS["rsi_w"], bars_from(np.linspace(100, 90, len(values))))

    w = with_rsi([40, 28, 22, 26, 35, 38, 29, 26, 33])  # dips to 22, then only to 26: a W
    assert w["signal"].iloc[-1] and w["signal"].sum() == 1
    deeper = with_rsi([40, 28, 26, 29, 35, 38, 24, 20, 33])  # second dip deeper: not a W
    assert not deeper["signal"].any()
    one_dip = with_rsi([40, 35, 28, 22, 25, 33, 40, 45, 50])  # a single dip is not a W
    assert not one_dip["signal"].any()


def test_breakout_retest_waits_for_the_retest():
    base = [100.0 + (i % 5) * 0.4 for i in range(60)]  # range below ~101.6
    breakout = [108, 110, 109]
    retest = [104, 102.5, 102.2]  # back within 2% of the old high (about 101.8)
    confirm = [104.5]
    f = evaluate(SETUPS["breakout_retest"], bars_from(base + breakout + retest + confirm, wick=0.002))
    assert f["signal"].iloc[-1]
    no_retest = evaluate(
        SETUPS["breakout_retest"], bars_from(base + breakout + [111, 112, 113, 114], wick=0.002)
    )
    assert not no_retest["signal"].any()


def test_supertrend_rsi_trails_and_exits_with_the_line():
    closes = list(np.linspace(100, 95, 60)) + list(np.linspace(95, 130, 30)) + list(np.linspace(130, 100, 20))
    f = evaluate(SETUPS["supertrend_rsi"], bars_from(closes))
    first = f.index[f["signal"]][0]
    assert f.loc[first, "trail"] == pytest.approx(f.loc[first, "stop"])
    assert f["exit"].iloc[-1]  # the slide took the price back below the line


def test_sector_rs_buys_the_sector_leader_only():
    bench = bars_from([100.0] * 300)
    lagging = [100.0] * 150
    surge = list(np.linspace(100, 140, 150))
    bars = {
        "BIG": bars_from(lagging + surge, volumes=[5e6] * 300),
        "MID": bars_from(lagging + surge, volumes=[1e6] * 300),
        "SMALL": bars_from(lagging + surge, volumes=[1e5] * 300),
    }
    ctx = SetupContext(bars, {s: "Metals" for s in bars}, bench)
    assert evaluate(SETUPS["sector_rs"], bars["BIG"], ctx, "BIG")["signal"].sum() == 1
    assert not evaluate(SETUPS["sector_rs"], bars["MID"], ctx, "MID")["signal"].any()
    assert not evaluate(SETUPS["sector_rs"], bars["BIG"], SetupContext(), "BIG")["signal"].any()
