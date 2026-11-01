"""Intraday module, phase 3: the trade simulator and the study."""

import numpy as np
import pandas as pd
import pytest

from tests.test_intraday_strategies import session_index, walk
from trademare.intraday import config as cfgmod
from trademare.intraday import study
from trademare.intraday.sim import Rules, simulate
from trademare.intraday.store import CandleStore

RULES = Rules(slippage_bps=0, cost_notional=1e12)  # no slippage; costs ~0 on a huge notional


def day_frame(rows, start="2026-01-05"):
    idx = session_index(1, start)[: len(rows)]
    f = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    f["volume"] = 1e4
    f["day"] = f.index.normalize()
    return f


def signals(f, at, side=1, trigger=None, stop=None, target=np.nan, exit_at=None):
    out = pd.DataFrame(index=f.index)
    out["side"] = 0
    out["trigger"] = np.nan
    out["stop"] = np.nan
    out["target"] = np.nan
    out["exit_long"] = False
    out["exit_short"] = False
    out.iloc[at, 0] = side
    out.iloc[at, 1] = (
        trigger if trigger is not None else (f["high"].iloc[at] if side > 0 else f["low"].iloc[at])
    )
    out.iloc[at, 2] = stop
    out.iloc[at, 3] = target
    if exit_at is not None:
        out.iloc[exit_at, out.columns.get_loc("exit_long" if side > 0 else "exit_short")] = True
    return out


FLAT = [100, 100.5, 99.5, 100]


def test_long_fills_on_the_trigger_then_hits_the_target():
    f = day_frame(
        [FLAT] * 2
        + [[100, 101, 99.8, 100.8], [100.8, 101.5, 100.6, 101.2], [101.2, 103.5, 101, 103]]
        + [FLAT] * 5
    )
    [t] = simulate("x", "S", f, signals(f, 2, stop=99.0, target=103.0), RULES)
    assert t.entry == 101.0 and t.reason == "target" and t.exit == 103.0 and t.r == pytest.approx(1.0)


def test_short_stop_and_gap_through_stop():
    f = day_frame(
        [FLAT] * 2 + [[100, 100.2, 99, 99.2], [99.2, 99.3, 98.5, 98.8], [101.5, 102, 101, 101.8]] + [FLAT] * 3
    )
    [t] = simulate("x", "S", f, signals(f, 2, side=-1, stop=100.5), RULES)
    assert t.side == -1 and t.entry == 99.0 and t.reason == "stop" and t.exit == 101.5  # gapped past the stop


def test_stop_on_the_entry_candle_counts_as_a_loss():
    f = day_frame([FLAT] * 2 + [[100, 101, 99.8, 100.8], [100.8, 101.2, 98.0, 98.5]] + [FLAT] * 3)
    [t] = simulate("x", "S", f, signals(f, 2, stop=99.0, target=101.1), RULES)
    assert t.reason == "stop" and t.candles == 0


def test_rule_exit_next_open_and_square_off():
    rows = [FLAT] * 2 + [
        [100, 101, 99.8, 100.8],
        [100.8, 101.4, 100.6, 101.2],
        [101.2, 102, 101, 101.8],
        [101.6, 102, 101.5, 101.9],
    ]
    f = day_frame(rows + [FLAT] * 3)
    [t] = simulate("x", "S", f, signals(f, 2, stop=99.0, exit_at=4), RULES)
    assert t.reason == "rule" and t.exit == 101.6
    whole = day_frame(
        [[100 + i * 0.01, 100.2 + i * 0.01, 99.9 + i * 0.01, 100.1 + i * 0.01] for i in range(75)]
    )
    [s] = simulate("x", "S", whole, signals(whole, 2, stop=90.0), RULES)
    assert s.reason == "squareoff" and pd.Timestamp(s.exit_time).time() == pd.Timestamp("15:15").time()


def test_order_lapses_after_three_candles_and_entry_window():
    f = day_frame(
        [FLAT] * 2 + [[100, 101, 99.8, 100.5]] + [FLAT] * 3 + [[100, 101.5, 99.9, 101.4]] + [FLAT] * 3
    )
    assert simulate("x", "S", f, signals(f, 2, stop=99.0), RULES) == []  # 101 not traded within 3 candles
    late = day_frame([FLAT] * 75)
    assert simulate("x", "S", late, signals(late, 70, trigger=100.4, stop=99.0), RULES) == []  # after 14:45
    early = day_frame([FLAT] * 5)
    assert (
        simulate("x", "S", early, signals(early, 0, trigger=100.4, stop=99.0), RULES) != []
    )  # 09:20 close is allowed


def test_one_trade_per_day():
    f = day_frame(
        [FLAT, [100, 101, 99.8, 100.8], [100.8, 101.5, 100.6, 101.2], [101, 101.2, 98, 98.2]] + [FLAT] * 6
    )
    sig = signals(f, 1, stop=99.0)
    sig.iloc[5, 0], sig.iloc[5, 1], sig.iloc[5, 2] = 1, 100.4, 99.0
    assert len(simulate("x", "S", f, sig, RULES)) == 1


def test_costs_make_flat_trades_lose():
    f = day_frame([FLAT] * 2 + [[100, 101, 99.8, 100.8]] + [[101, 101.1, 100.9, 101]] * 72)
    [t] = simulate("x", "S", f, signals(f, 2, stop=90.0), Rules())
    assert t.reason == "squareoff" and t.ret < 0 and t.r == pytest.approx(0.0, abs=0.01)


def test_study_end_to_end_and_the_bar(tmp_path, capsys):
    path = tmp_path / "intraday.yaml"
    path.write_text(f"data_dir: {tmp_path / 'data'}\nhistory_start: 2025-01-01\n")
    cfg = cfgmod.load(path)
    store = CandleStore(cfg.root, cfg.interval)
    for seed in range(2):
        c = walk(30, seed=seed, vol=0.003)
        store.write(f"S{seed}", c[["open", "high", "low", "close", "volume"]])
    result = study.run(cfg, ["orb", "vwap_bands"], progress=lambda m: None)
    rec = result["records"]["orb"]
    assert rec["periods"]["2022-now"]["trades"] > 0 and not rec["passes"]
    assert "only" in rec["verdict"]  # far fewer than 300 trades
    study.save(cfg, result)
    from trademare.intraday.cli import main

    assert main(["--config", str(path), "study", "--show"]) == 0
    assert "orb" in capsys.readouterr().out
