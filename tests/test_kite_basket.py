from datetime import datetime
from types import SimpleNamespace

from trademare.execution.kite_basket import IST, kite_basket, variety


def sig(**kw):
    base = dict(market="IN", action="ENTRY", direction="LONG", symbol="SAIL", quantity=54, entry_price=165.3,
                stop_price=147.05, strategy=None)  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def test_core_signal_is_a_limit_delivery_buy():
    during = datetime(2026, 11, 3, 10, 0, tzinfo=IST)
    assert kite_basket(sig(), during) == [
        {
            "variety": "regular", "tradingsymbol": "SAIL", "exchange": "NSE", "transaction_type": "BUY",
            "quantity": 54, "product": "CNC", "validity": "DAY", "tag": "tmare",
            "order_type": "LIMIT", "price": 165.3,
        }
    ]  # fmt: skip


def test_setup_signal_is_a_buy_stop_and_after_hours_is_amo():
    evening = datetime(2026, 11, 2, 21, 0, tzinfo=IST)
    (order,) = kite_basket(sig(symbol="HSCL", entry_price=509.2, strategy="setup:vcp"), evening)
    assert order["variety"] == "amo" and order["order_type"] == "SL"
    assert order["trigger_price"] == 509.2 and order["price"] == 514.3  # 1% above, on the 0.05 tick
    assert variety(datetime(2026, 11, 7, 11, 0, tzinfo=IST)) == "amo"  # Saturday


def test_only_indian_long_entries():
    assert kite_basket(sig(market="US")) is None
    assert kite_basket(sig(action="EXIT")) is None
    assert kite_basket(sig(direction="SHORT")) is None
