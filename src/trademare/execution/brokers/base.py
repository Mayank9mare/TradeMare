"""Broker abstraction (strategy pattern).

Each broker (Zerodha, Dhan, Groww, Alpaca, Paper) implements ``BrokerAdapter`` and
registers itself under the name used in ``settings.yaml`` (``markets.<M>.broker``).
The trading desk only ever talks to this interface.

Two kinds of orders:
- regular day orders (entries as LIMIT, exits as MARKET) via ``place_order``;
- *protection* for an open position: a stop (and optional target) that
  survives across sessions. Indian exchange orders are day-only, so this
  maps to a Zerodha or Groww GTT, a Dhan Forever order, or an Alpaca GTC stop/OCO.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from trademare.config.schema import MarketConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Funds, Market, Order, OrderSide, OrderStatus, Position


class BrokerError(Exception):
    """Raised for broker API failures and rejected requests."""


class LoginRequired(BrokerError):
    """The broker session is missing or expired; the user must log in again."""


@dataclass(frozen=True)
class OrderUpdate:
    """Broker-side state of an order (or of a triggered protection)."""

    status: OrderStatus
    filled_quantity: int = 0
    average_price: float | None = None
    message: str | None = None

    @property
    def is_filled(self) -> bool:
        return self.status is OrderStatus.FILLED

    @property
    def is_dead(self) -> bool:
        return self.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED)


class BrokerAdapter(ABC):
    name: ClassVar[str]
    markets: ClassVar[frozenset[Market]]
    supports_short_delivery: ClassVar[bool] = False
    simulated: ClassVar[bool] = False  # True for the paper broker: no real orders

    def __init__(self, market: Market, config: MarketConfig, secrets: Secrets) -> None:
        if market not in self.markets:
            raise BrokerError(f"broker '{self.name}' does not trade market {market}")
        self.market = market
        self.config = config
        self.secrets = secrets

    @abstractmethod
    def connect(self) -> None:
        """Authenticate / load session tokens. Raises LoginRequired if the user must log in."""

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Submit a day order (LIMIT or MARKET); return the broker's order id."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def get_order(self, broker_order_id: str) -> OrderUpdate:
        """Current state of a day order as reported by the broker."""

    def place_protection(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        stop: float,
        target: float | None,
        last_price: float,
    ) -> str:
        """Standing stop (OCO with ``target`` if given) that survives sessions; returns its id."""
        raise BrokerError(f"{self.name} does not support standing protective orders")

    def modify_protection(
        self,
        protection_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        stop: float,
        target: float | None,
        last_price: float,
    ) -> str:
        """Move the stop/target; returns the (possibly new) protection id."""
        self.cancel_protection(protection_id)
        return self.place_protection(symbol, side, quantity, stop, target, last_price)

    def cancel_protection(self, protection_id: str) -> None:
        raise BrokerError(f"{self.name} does not support standing protective orders")

    def protection_status(self, protection_id: str) -> OrderUpdate:
        """OPEN while armed; FILLED with the execution price once triggered and executed."""
        raise BrokerError(f"{self.name} does not support standing protective orders")

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_funds(self) -> Funds: ...

    @abstractmethod
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        """Last traded price per symbol."""


_REGISTRY: dict[str, type[BrokerAdapter]] = {}


def register_broker(cls: type[BrokerAdapter]) -> type[BrokerAdapter]:
    if cls.name in _REGISTRY:
        raise ValueError(f"broker '{cls.name}' already registered")
    _REGISTRY[cls.name] = cls
    return cls


def registered_brokers() -> dict[str, type[BrokerAdapter]]:
    return dict(_REGISTRY)


def create_broker(
    market: Market,
    config: MarketConfig,
    secrets: Secrets,
    registry: Callable[[], dict[str, type[BrokerAdapter]]] = registered_brokers,
) -> BrokerAdapter:
    """Instantiate the broker configured for ``market``."""
    brokers = registry()
    try:
        cls = brokers[config.broker]
    except KeyError:
        raise BrokerError(
            f"broker '{config.broker}' is not implemented yet (available: {sorted(brokers) or 'none'})"
        ) from None
    return cls(market, config, secrets)
