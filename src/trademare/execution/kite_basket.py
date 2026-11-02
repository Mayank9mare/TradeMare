"""Kite "offsite order" baskets: the signal's order, pre-filled for the owner to confirm in Kite.

The dashboard posts this basket from the owner's browser to Zerodha's basket page
(https://kite.zerodha.com/connect/basket); Kite shows the order and the owner places it there,
logged in to their own account. TradeMare never sends it: no order is placed unless the owner
confirms in Kite. Docs: https://kite.trade/docs/connect/v3/basket/
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

BASKET_URL = "https://kite.zerodha.com/connect/basket"
IST = ZoneInfo("Asia/Kolkata")
TICK = 0.05  # NSE price step for most stocks
SETUP_LIMIT_BUFFER = 0.01  # a buy-stop may fill up to 1% above its trigger, as in the paper desk


def _tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


def variety(now: datetime | None = None) -> str:
    """'regular' during the trading day (pre-open from 9:00), otherwise an after-market order."""
    now = (now or datetime.now(IST)).astimezone(IST)
    if now.weekday() < 5 and time(9, 0) <= now.time() < time(15, 30):
        return "regular"
    return "amo"


def kite_basket(sig, now: datetime | None = None) -> list[dict] | None:
    """The buy order for an Indian entry signal, as Kite basket JSON; None for anything else.

    Core signals are a DAY limit buy at the signal price. Book-setup signals are a buy-stop: an SL
    order that triggers at the signal price with a limit 1% above it. Delivery (CNC) in both cases.
    The stop-loss is not in the basket: a sell can only be placed once the shares are held.
    """
    if sig.market != "IN" or sig.action != "ENTRY" or sig.direction != "LONG":
        return None
    order = {
        "variety": variety(now),
        "tradingsymbol": sig.symbol,
        "exchange": "NSE",
        "transaction_type": "BUY",
        "quantity": int(sig.quantity),
        "product": "CNC",
        "validity": "DAY",
        "tag": "trademare",
    }
    if (sig.strategy or "").startswith("setup:"):
        order |= {
            "order_type": "SL",
            "trigger_price": _tick(sig.entry_price),
            "price": _tick(sig.entry_price * (1 + SETUP_LIMIT_BUFFER)),
        }
    else:
        order |= {"order_type": "LIMIT", "price": _tick(sig.entry_price)}
    return [order]
