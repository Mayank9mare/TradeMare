from datetime import UTC, datetime

import pytest

from tests.test_desk import (  # noqa: F401  (fixtures)
    DAY0,
    desk,
    entry_signals,
    live_desk,
)
from trademare.core.models import Funds, Market, Order, OrderSide, OrderType
from trademare.risk.pretrade import PreTradeContext, check
from trademare.storage.db import EventRow, OrderRow
from trademare.trading.desk import DeskError


def buy(price=100.0, qty=10, market=Market.IN):
    return Order(market=market, symbol="ABC", side=OrderSide.BUY, quantity=qty,
                 order_type=OrderType.LIMIT, limit_price=price)  # fmt: skip


def opening(**kw):
    base = dict(purpose="entry", opening=True, last_close=100.0, budget=100_000, max_position_pct=15,
                funds=50_000, max_orders_per_day=10)  # fmt: skip
    return PreTradeContext(**(base | kw))


def test_a_sane_buy_passes():
    assert check(buy(), opening()) == []


@pytest.mark.parametrize(
    ("kw", "order_kw", "expected"),
    [
        ({}, {"price": 106.0}, "+6.0% from the last close"),
        ({}, {"qty": 200}, "above the position cap"),
        ({"funds": 500.0}, {}, "only 500 is free"),
        ({"funds": 2_000.0, "committed": 1_500.0}, {}, "this order plus open buys"),
        ({"in_universe": False}, {}, "not in the trading universe"),
        ({"surveillance": "LTASM - Stage I"}, {}, "under exchange surveillance"),
        ({"sessions_behind": 1}, {}, "1 session(s) behind"),
        ({"orders_today": 10}, {}, "already 10 new orders today"),
    ],
)
def test_buys_are_refused_for_each_problem(kw, order_kw, expected):
    problems = check(buy(**order_kw), opening(**kw))
    assert any(expected in p for p in problems), problems


def test_sells_never_exceed_tradeMare_shares_and_are_never_blocked_for_price():
    sell = Order(
        market=Market.IN, symbol="ABC", side=OrderSide.SELL, quantity=10, order_type=OrderType.MARKET
    )
    closing = PreTradeContext(purpose="exit", opening=False, position_qty=10, sessions_behind=5)
    assert check(sell, closing) == []  # stale data, no funds: a protective exit still goes
    closing.position_qty = 6  # the owner's own 4 shares in the same account must not be sold
    [problem] = check(sell, closing)
    assert "holds only 6" in problem and "never sells shares it did not buy" in problem


def test_opening_short_is_refused_in_india_only():
    short_in = Order(market=Market.IN, symbol="ABC", side=OrderSide.SELL, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=100.0)  # fmt: skip
    assert any("short selling" in p for p in check(short_in, opening()))
    short_us = short_in.model_copy(update={"market": Market.US})
    assert check(short_us, opening()) == []


def test_paper_logs_a_warning_but_still_places_the_order(desk):  # noqa: F811
    desk.cfg.pretrade.enabled = True
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))  # replayed 2025 data looks stale today
    with desk.db.session() as s:
        assert s.query(OrderRow).filter_by(purpose="entry").count() == 1
        warning = s.query(EventRow).filter_by(kind="pretrade").one()
        assert warning.data["blocked"] is False and "paper warning" in warning.message


def test_live_refuses_a_buy_the_account_cannot_pay_for(live_desk, monkeypatch):  # noqa: F811
    d = live_desk
    d.cfg = d.cfg.model_copy(deep=True)
    d.cfg.pretrade.enabled = True
    d.run_daily(DAY0)
    sig = entry_signals(d)[0]
    monkeypatch.setattr(d, "_sessions_behind", lambda: 0)
    monkeypatch.setattr(d, "_last_close", lambda symbol, day: sig.entry_price)
    monkeypatch.setattr(d.broker, "get_funds", lambda: Funds(market=Market.IN, cash_available=141.0))
    with pytest.raises(DeskError, match="only 141 is free"):
        d.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    assert d.broker.placed == []

    from trademare.data import surveillance

    monkeypatch.setattr(d.broker, "get_funds", lambda: Funds(market=Market.IN, cash_available=10**7))
    monkeypatch.setattr(
        surveillance.SurveillanceService, "flagged", lambda self, today=None: {sig.symbol: "GSM 0"}
    )
    with pytest.raises(DeskError, match="surveillance"):
        d.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    monkeypatch.setattr(surveillance.SurveillanceService, "flagged", lambda self, today=None: {})
    d.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    assert len(d.broker.placed) == 1
