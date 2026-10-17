"""Paper broker: orders live in the local database and are filled by the
trading desk against each session's bar using the shared fill rules
(``trademare.execution.fills``) — the same rules as the backtester.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from trademare.core.models import Funds, Market, Order, Position
from trademare.execution.brokers.base import BrokerAdapter, register_broker


@register_broker
class PaperBroker(BrokerAdapter):
    name = "paper"
    markets: ClassVar[frozenset[Market]] = frozenset({Market.IN, Market.US})
    supports_short_delivery = True
    simulated = True

    def connect(self) -> None:  # nothing to authenticate
        return None

    def place_order(self, order: Order) -> str:
        return f"paper-{uuid.uuid4().hex[:12]}"

    def cancel_order(self, broker_order_id: str) -> None:
        return None

    # State is owned by the trading desk's database for paper accounts.
    def get_order(self, broker_order_id: str) -> Order:
        raise NotImplementedError("paper order state lives in the trading database")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("paper positions live in the trading database")

    def get_funds(self) -> Funds:
        raise NotImplementedError("paper cash lives in the trading database")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        raise NotImplementedError("use cached bars for paper prices")
