"""Broker abstraction (strategy pattern).

Each broker (Zerodha, Dhan, Alpaca, Paper) implements ``BrokerAdapter`` and
registers itself under the name used in ``settings.yaml`` (``markets.<M>.broker``).
The executor only ever talks to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from trademare.config.schema import MarketConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Funds, Market, Order, Position


class BrokerError(Exception):
    """Raised for broker API failures and rejected requests."""


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
        """Authenticate / refresh session tokens."""

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Submit ``order``; return the broker's order id."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def get_order(self, broker_order_id: str) -> Order:
        """Current state of an order as reported by the broker."""

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
