import math

import numpy as np
import pandas as pd
import pytest

from trademare.core.models import Market
from trademare.strategy.setup_study import (
    PERIODS,
    PeriodStats,
    Trade,
    judge,
    simulate,
    summarize,
)
from trademare.strategy.setups import Setup


def bars(rows):
    idx = pd.bdate_range("2021-01-04", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e6)


def frame(b, signal_at, stop, target=np.nan, trail=None, exit_at=None):
    f = pd.DataFrame(index=b.index)
    f["signal"] = False
    f.iloc[signal_at, 0] = True
    f["entry"] = b["high"]
    f["stop"] = stop
    f["target"] = target
    f["trail"] = np.nan if trail is None else trail
    f["exit"] = False
    if exit_at is not None:
        f.iloc[exit_at, f.columns.get_loc("exit")] = True
    f["strength"] = 0.0
    return f


SETUP = Setup("t", "Test", "0", "Trend", "", rule=lambda *a: None, max_hold=60)


def run(b, f, **kw):
    return simulate(SETUP, "X", b, f, pd.Series(True, index=b.index), Market.US, slippage_bps=0, **kw)


def test_buy_stop_fills_at_the_trigger_or_the_gap_open_and_stops_out():
    b = bars([[100, 101, 99, 100], [100.5, 103, 100, 102], [102, 102, 95, 96], [96, 97, 95, 96]])
    [t] = run(b, frame(b, 0, stop=97.0))
    assert t.entry == 101 and t.reason == "stop" and t.exit == 97 and t.r == pytest.approx(-1)
    small = bars([[100, 101, 99, 100], [101.5, 106, 101, 105], [105, 106, 96, 97], [97, 98, 96, 97]])
    [g] = run(small, frame(small, 0, stop=97.0))
    assert g.entry == 101.5  # a gap within the 1% stop-limit buffer fills at the open
    big = bars([[100, 101, 99, 100], [104, 106, 103, 105], [105, 106, 96, 97], [97, 98, 96, 97]])
    assert run(big, frame(big, 0, stop=97.0)) == []  # a bigger gap: the stop-limit would not fill


def test_no_fill_when_the_next_day_stays_below_the_trigger():
    b = bars([[100, 101, 99, 100], [100, 100.9, 98, 99], [99, 100, 98, 99]])
    assert run(b, frame(b, 0, stop=95.0)) == []


def test_target_trailing_stop_rule_exit_and_gaps():
    up = bars([[100, 101, 99, 100], [101, 102, 100, 101], [101, 108, 101, 107], [107, 112, 106, 111]])
    [t] = run(up, frame(up, 0, stop=98.0, target=110.0))
    assert t.reason == "target" and t.exit == 110
    trail = pd.Series([np.nan, 99.0, 104.0, 104.0, 104.0], index=pd.bdate_range("2021-01-04", periods=5))
    tb = bars(
        [
            [100, 101, 99, 100],
            [101, 102, 100, 101],
            [101, 108, 101, 107],
            [107, 107, 103, 104],
            [104, 105, 103, 104],
        ]
    )
    [tr] = run(tb, frame(tb, 0, stop=98.0, trail=trail.to_numpy()))
    assert tr.reason == "stop" and tr.exit == 104  # the stop had trailed up to 104
    rb = bars([[100, 101, 99, 100], [101, 102, 100, 101], [101, 103, 100, 102], [99, 100, 98, 99]])
    [rule] = run(rb, frame(rb, 0, stop=90.0, exit_at=2))
    assert rule.reason == "rule" and rule.exit == 99  # exit at the next open
    gb = bars([[100, 101, 99, 100], [101, 102, 100, 101], [90, 91, 89, 90], [90, 91, 89, 90]])
    [gapped] = run(gb, frame(gb, 0, stop=98.0))
    assert gapped.reason == "stop" and gapped.exit == 90  # a gap through the stop fills at the open


def test_one_trade_at_a_time_and_open_trades_are_marked():
    b = bars([[100, 101, 99, 100]] + [[100 + i, 102 + i, 99 + i, 101 + i] for i in range(1, 6)])
    f = frame(b, 0, stop=90.0)
    f.loc[f.index[2], "signal"] = True  # a second signal while the first trade is open
    trades = run(b, f)
    assert len(trades) == 1 and trades[0].reason == "open"


def test_costs_lower_returns_in_india():
    b = bars([[100, 101, 99, 100], [101, 102, 100, 101], [101, 108, 101, 107], [107, 112, 106, 111]])
    us = run(b, frame(b, 0, stop=98.0, target=110.0))[0]
    india = simulate(
        SETUP, "X", b, frame(b, 0, stop=98.0, target=110.0), pd.Series(True, index=b.index), Market.IN, 0
    )[0]
    assert india.ret < us.ret and india.ret > 0


def test_evidence_bar():
    good = PeriodStats(trades=150, avg_ret=0.01, profit_factor=1.3)
    assert judge({PERIODS[0]: good, PERIODS[1]: good})[0]
    few = PeriodStats(trades=40, avg_ret=0.02, profit_factor=2.0)
    ok, why = judge({PERIODS[0]: few, PERIODS[1]: good})
    assert not ok and "only 40 trades" in why
    weak = PeriodStats(trades=150, avg_ret=0.001, profit_factor=1.02)
    ok, why = judge({PERIODS[0]: good, PERIODS[1]: weak})
    assert not ok and "profit factor 1.02" in why
    losing = PeriodStats(trades=150, avg_ret=-0.002, profit_factor=0.9)
    assert not judge({PERIODS[0]: losing, PERIODS[1]: good})[0]


def test_summary_splits_periods_and_ignores_open_trades():
    def t(day, ret, reason="target"):
        return Trade("X", day, day, 100, 95, day, 101, reason, 5, ret, 0.2, bench=0.001)

    rec = summarize(
        "t",
        [t("2015-03-02", 0.02), t("2015-04-01", -0.01), t("2021-05-03", 0.03), t("2026-10-01", 0.5, "open")],
    )
    early, later = rec.periods[PERIODS[0]], rec.periods[PERIODS[1]]
    assert early.trades == 2 and early.win_rate == 0.5 and math.isclose(early.profit_factor, 2.0)
    assert later.trades == 1 and later.avg_bench == pytest.approx(0.001)
    assert rec.by_year == {"2015": 2, "2021": 1, "2026": 1}
