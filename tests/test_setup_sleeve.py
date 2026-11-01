"""Book setups as a paper-trading sleeve next to the core strategy (trading/setup_sleeve.py)."""

from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from tests.test_desk import CLOCK, DAY0, desk, set_bar  # noqa: F401  (fixture)
from trademare.core.models import Market
from trademare.storage.db import OrderRow, PositionRow, SignalRow
from trademare.strategy.setups import SETUPS, SetupContext
from trademare.trading import setup_sleeve

DAY1 = CLOCK.next_session(DAY0)
STATS = {"trades": 150, "win_rate": 0.45, "avg_ret": 0.012, "avg_bench": 0.008, "profit_factor": 1.3}


def stub_universe(monkeypatch, desk, day):  # noqa: F811
    class U:
        def __init__(self, service, market):
            self.data_through = day
            self.bars = {
                s: b.loc[: pd.Timestamp(day)]
                for s in [f"S{i}" for i in range(10)]
                if (b := service.store.read_bars(market, s)) is not None
            }
            self.ctx = SetupContext()

    monkeypatch.setattr(setup_sleeve, "Universe", U)


def stub_record(monkeypatch, day, matches):
    records = {
        sid: {"periods": {"2010-2019": STATS, "2020-now": STATS}, "passes": sid != "ichimoku"}
        for sid in SETUPS
    }
    record = {"records": records, "matches": [{"signal_date": day.isoformat(), **m} for m in matches]}
    monkeypatch.setattr(setup_sleeve, "current_record", lambda *a, **k: record)


def match(symbol, setup="vcp", entry=None, stop=None, strength=1.0, desk=None, day=DAY0):  # noqa: F811
    close = desk.service.store.read_bars(Market.IN, symbol).loc[pd.Timestamp(day), "close"]
    entry = entry or round(close * 1.01, 2)
    return {"setup": setup, "symbol": symbol, "name": symbol, "sector": "Sector3", "close": close,
            "entry": entry, "stop": stop or round(entry * 0.94, 2), "target": round(entry * 1.18, 2),
            "risk_pct": 0.06, "strength": strength, "liquid": True}  # fmt: skip


def enable(desk, selected=("vcp",)):  # noqa: F811
    desk.cfg = desk.cfg.model_copy(deep=True)
    desk.cfg.setups.enabled = True
    desk.cfg.setups.selected = list(selected)


def setup_signals(desk):  # noqa: F811
    with desk.db.session() as s:
        return list(s.query(SignalRow).filter(SignalRow.strategy.is_not(None)))


def test_off_by_default_and_costs_nothing(desk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(setup_sleeve, "Universe", lambda *a: pytest.fail("should not load the universe"))
    desk.run_daily(DAY0)
    assert setup_signals(desk) == []


def test_selected_setups_signal_inside_the_sleeve_and_skip_failing_ones(desk, monkeypatch):  # noqa: F811
    enable(desk, ("vcp", "ichimoku"))
    stub_universe(monkeypatch, desk, DAY0)
    stub_record(monkeypatch, DAY0, [match("S7", desk=desk), match("S8", "ichimoku", desk=desk)])
    report = desk.run_daily(DAY0)
    [sig] = setup_signals(desk)
    assert sig.symbol == "S7" and sig.strategy == "setup:vcp" and sig.score == 0.0
    assert sig.quantity * sig.entry_price <= desk.cfg.budget * 0.30 + 1  # the sleeve's share
    assert "Volatility contraction" in sig.reasons["setup"][0] and "buy-stop" in sig.reasons["setup"][1]
    assert any("fail the evidence bar" in n and "Ichimoku" in n for n in report.notes)
    core = [x for x in report.signals if x.strategy is None and x.action == "ENTRY"]
    assert core  # the core strategy still signals as before
    desk.run_daily(DAY0)  # idempotent
    assert len(setup_signals(desk)) == 1


def test_buy_stop_entry_fills_only_above_the_trigger(desk, monkeypatch):  # noqa: F811
    enable(desk)
    stub_universe(monkeypatch, desk, DAY0)
    stub_record(monkeypatch, DAY0, [match("S7", desk=desk)])
    desk.run_daily(DAY0)
    [sig] = setup_signals(desk)
    order = desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    assert order.order_type == "STOP_LIMIT" and order.trigger_price == sig.entry_price
    assert order.limit_price == pytest.approx(sig.entry_price * 1.01, abs=0.01)
    trigger = sig.entry_price
    set_bar(desk, "S7", DAY1, open=trigger * 0.99, high=trigger * 1.004, low=trigger * 0.985, close=trigger)
    stub_universe(monkeypatch, desk, DAY1)
    stub_record(monkeypatch, DAY1, [])
    desk.run_daily(DAY1, generate=False)
    with desk.db.session() as s:
        pos = s.query(PositionRow).filter_by(symbol="S7").one()
        assert pos.entry_price == pytest.approx(trigger) and pos.strategy == "setup:vcp"
        assert s.query(OrderRow).filter_by(position_id=pos.id, purpose="target").count() == 1


def test_no_fill_when_the_price_never_reaches_the_trigger(desk, monkeypatch):  # noqa: F811
    enable(desk)
    stub_universe(monkeypatch, desk, DAY0)
    stub_record(monkeypatch, DAY0, [match("S7", desk=desk)])
    desk.run_daily(DAY0)
    [sig] = setup_signals(desk)
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    t = sig.entry_price
    set_bar(desk, "S7", DAY1, open=t * 0.97, high=t * 0.995, low=t * 0.96, close=t * 0.98)
    desk.run_daily(DAY1, generate=False)
    with desk.db.session() as s:
        assert s.query(PositionRow).filter_by(symbol="S7").count() == 0


def open_setup_position(desk, monkeypatch, setup="vcp", symbol="S7"):  # noqa: F811
    enable(desk, (setup,))
    stub_universe(monkeypatch, desk, DAY0)
    stub_record(monkeypatch, DAY0, [match(symbol, setup, desk=desk)])
    desk.run_daily(DAY0)
    [sig] = setup_signals(desk)
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    t = sig.entry_price
    set_bar(desk, symbol, DAY1, open=t * 0.995, high=t * 1.003, low=t * 0.99, close=t * 1.002)
    stub_universe(monkeypatch, desk, DAY1)
    stub_record(monkeypatch, DAY1, [])
    desk.run_daily(DAY1)
    with desk.db.session() as s:
        return s.query(PositionRow).filter_by(symbol=symbol).one()


def test_core_rebalance_neither_sells_nor_counts_setup_positions(desk, monkeypatch):  # noqa: F811
    pos = open_setup_position(desk, monkeypatch, symbol="S9")  # S9 ranks last in the core strategy
    july = CLOCK.next_session(pd.Timestamp("2025-06-30").date())
    stub_universe(monkeypatch, desk, july)
    stub_record(monkeypatch, july, [])
    desk.run_daily(july)
    with desk.db.session() as s:
        exits = s.query(SignalRow).filter_by(action="EXIT", position_id=pos.id).all()
        assert exits == []  # S9's low core rank does not sell the setup's position


def test_setup_exit_rule_and_holding_limit_create_exit_signals(desk, monkeypatch):  # noqa: F811
    pos = open_setup_position(desk, monkeypatch)
    day2 = CLOCK.next_session(DAY1)
    stub_universe(monkeypatch, desk, day2)
    stub_record(monkeypatch, day2, [])
    real = setup_sleeve.evaluate

    def exit_today(setup, bars, ctx=None, symbol=""):
        f = real(setup, bars, ctx, symbol)
        f.iloc[-1, f.columns.get_loc("exit")] = True
        return f

    monkeypatch.setattr(setup_sleeve, "evaluate", exit_today)
    desk.run_daily(day2)
    with desk.db.session() as s:
        [ex] = s.query(SignalRow).filter_by(action="EXIT", position_id=pos.id).all()
        assert ex.exit_reason == "setup rule" and ex.strategy == "setup:vcp"
        assert "Volatility contraction" in ex.reasons["exit"][0]


def test_holding_limit(desk, monkeypatch):  # noqa: F811
    pos = open_setup_position(desk, monkeypatch)
    monkeypatch.setitem(setup_sleeve.SETUPS, "vcp", replace(SETUPS["vcp"], max_hold=1))
    day2 = CLOCK.next_session(DAY1)
    stub_universe(monkeypatch, desk, day2)
    stub_record(monkeypatch, day2, [])
    desk.run_daily(day2)
    with desk.db.session() as s:
        [ex] = s.query(SignalRow).filter_by(action="EXIT", position_id=pos.id).all()
        assert ex.exit_reason == "time"


def test_trailing_setups_raise_the_stop(desk, monkeypatch):  # noqa: F811
    pos = open_setup_position(desk, monkeypatch, setup="bb_squeeze")  # its real band already trailed once
    day2 = CLOCK.next_session(DAY1)
    close = pos.last_price
    monkeypatch.setattr(setup_sleeve, "trail_level", lambda setup, bars: close * 0.999)
    stub_universe(monkeypatch, desk, day2)
    stub_record(monkeypatch, day2, [])
    set_bar(desk, "S7", day2, open=close, high=close * 1.01, low=close * 0.995, close=close)
    desk.run_daily(day2)
    with desk.db.session() as s:
        moved = s.get(PositionRow, pos.id)
        assert moved.stop_price == pytest.approx(close * 0.999, abs=0.01)
        stop = s.query(OrderRow).filter_by(position_id=pos.id, purpose="stop", status="OPEN").one()
        assert stop.trigger_price == moved.stop_price
