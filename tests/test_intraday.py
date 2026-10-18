from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tests.test_desk import (  # noqa: F401  (fixtures)
    DAY0,
    SESSIONS,
    desk,
    entry_signals,
    live_desk,
    set_bar,
)
from trademare.core.models import OrderStatus
from trademare.execution.brokers import base as broker_base
from trademare.storage.db import PositionRow

IST = ZoneInfo("Asia/Kolkata")


def opened(d):
    """Approve the first signal on DAY0 and fill it on the next session; returns (signal, day1)."""
    d.run_daily(DAY0)
    sig = entry_signals(d)[0]
    d.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    day1 = SESSIONS[SESSIONS.index(DAY0) + 1]
    px = sig.entry_price
    set_bar(d, sig.symbol, day1, open=px * 0.999, high=px * 1.01, low=px * 0.998, close=px)
    return sig, day1


def at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def test_paper_breach_alerts_once_per_day_and_leaves_fill_to_daily_run(desk, monkeypatch):  # noqa: F811
    sig, day1 = opened(desk)
    desk.run_daily(day1, generate=False)
    day2 = SESSIONS[SESSIONS.index(day1) + 1]
    quotes = {sig.symbol: sig.stop_price * 0.99}
    monkeypatch.setattr("trademare.data.quotes.latest_prices", lambda market, symbols: quotes)

    assert desk.intraday_check(at(day2, 18)) == []  # market closed
    alerts = desk.intraday_check(at(day2, 11))
    assert len(alerts) == 1 and "through its stop" in alerts[0] and "paper trading" in alerts[0]
    assert desk.intraday_check(at(day2, 11, 30)) == []  # once per day
    with desk.db.session() as s:
        assert s.query(PositionRow).one().status == "open"  # the daily run fills paper stops

    quotes[sig.symbol] = sig.stop_price * 1.05
    assert desk.intraday_check(at(SESSIONS[SESSIONS.index(day2) + 1], 11)) == []  # back above the stop


def test_live_stuck_stop_exits_now_instead_of_next_open(live_desk):  # noqa: F811
    desk, broker = live_desk, live_desk.broker  # noqa: F811
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    day1, day2 = SESSIONS[SESSIONS.index(DAY0) + 1 : SESSIONS.index(DAY0) + 3]
    bid, placed = broker.placed[0]
    broker.orders[bid] = broker_base.OrderUpdate(OrderStatus.FILLED, placed.quantity, sig.entry_price)
    desk.run_daily(day1, generate=False)

    broker.get_ltp = lambda symbols: {
        sig.symbol: sig.stop_price * 0.995
    }  # through the stop, inside the limit
    alerts = desk.intraday_check(at(day2, 10))
    assert len(alerts) == 1 and "should be triggering" in alerts[0] and "P0" not in broker.cancelled

    broker.get_ltp = lambda symbols: {sig.symbol: sig.stop_price * 0.95}  # past the 1% limit leg: stuck
    alerts = desk.intraday_check(at(day2, 10, 15))
    assert any("gapped past limit" in a and "market exit placed now" in a for a in alerts)
    assert "P0" in broker.cancelled
    _, exit_order = broker.placed[-1]
    assert exit_order.order_type.value == "MARKET" and exit_order.side.value == "SELL"
    assert desk.intraday_check(at(day2, 10, 30)) == []  # the stop is gone and today's alert was sent
