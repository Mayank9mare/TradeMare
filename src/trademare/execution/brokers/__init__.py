# Importing the adapter modules registers them under their config names.
from trademare.execution.brokers import alpaca as _alpaca  # noqa: F401
from trademare.execution.brokers import dhan as _dhan  # noqa: F401
from trademare.execution.brokers import groww as _groww  # noqa: F401
from trademare.execution.brokers import paper as _paper  # noqa: F401
from trademare.execution.brokers import zerodha as _zerodha  # noqa: F401
from trademare.execution.brokers.base import (
    BrokerAdapter,
    BrokerError,
    LoginRequired,
    OrderUpdate,
    create_broker,
    register_broker,
    registered_brokers,
)

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "LoginRequired",
    "OrderUpdate",
    "create_broker",
    "register_broker",
    "registered_brokers",
]
