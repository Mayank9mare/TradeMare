import pytest
from pydantic import ValidationError

from trademare.core.models import (
    Direction,
    Market,
    Order,
    OrderSide,
    OrderType,
    Position,
    Signal,
    SignalAction,
)


def make_signal(**overrides) -> Signal:
    fields = dict(
        market=Market.IN,
        symbol="INFY",
        direction=Direction.LONG,
        quantity=10,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        score=0.6,
        confidence=0.7,
    )
    return Signal(**(fields | overrides))


@pytest.mark.parametrize(
    ("direction", "action", "side"),
    [
        (Direction.LONG, SignalAction.ENTRY, OrderSide.BUY),
        (Direction.LONG, SignalAction.EXIT, OrderSide.SELL),
        (Direction.SHORT, SignalAction.ENTRY, OrderSide.SELL),
        (Direction.SHORT, SignalAction.EXIT, OrderSide.BUY),
    ],
)
def test_order_side_mapping(direction, action, side):
    stop, target = (95.0, 110.0) if direction is Direction.LONG else (105.0, 90.0)
    sig = make_signal(direction=direction, action=action, stop_price=stop, target_price=target)
    assert sig.order_side is side


def test_signal_risk_metrics():
    sig = make_signal()
    assert sig.notional == 1000
    assert sig.risk_per_share == 5
    assert sig.risk_amount == 50
    assert sig.reward_risk == pytest.approx(2.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"stop_price": 105.0},  # long stop above entry
        {"target_price": 90.0},  # long target below entry
        {"direction": Direction.SHORT},  # short with long-style levels
        {"stop_price": None},  # entries need a stop
        {"score": 1.5},
        {"quantity": 0},
    ],
)
def test_invalid_signals_rejected(overrides):
    with pytest.raises(ValidationError):
        make_signal(**overrides)


def test_exit_signal_needs_no_stop():
    sig = make_signal(action=SignalAction.EXIT, stop_price=None, target_price=None)
    assert sig.risk_amount is None and sig.reward_risk is None


def test_order_price_requirements():
    with pytest.raises(ValidationError, match="limit_price"):
        Order(market=Market.US, symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.LIMIT)
    with pytest.raises(ValidationError, match="trigger_price"):
        Order(market=Market.US, symbol="AAPL", side=OrderSide.SELL, quantity=1, order_type=OrderType.STOP)
    Order(market=Market.US, symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)


def test_position_pnl_long_and_short():
    long = Position(market=Market.US, symbol="AAPL", direction=Direction.LONG, quantity=10, average_price=100)
    short = Position(
        market=Market.US, symbol="AAPL", direction=Direction.SHORT, quantity=10, average_price=100
    )
    assert long.unrealized_pnl(110) == 100
    assert short.unrealized_pnl(110) == -100


def test_market_metadata():
    assert Market.IN.currency == "INR" and Market.US.currency == "USD"
