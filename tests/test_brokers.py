import pytest

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.core.models import Funds, Market
from trademare.execution.brokers import BrokerAdapter, BrokerError, create_broker


class FakeBroker(BrokerAdapter):
    name = "paper"
    markets = frozenset({Market.IN, Market.US})

    def connect(self): ...
    def place_order(self, order):
        return "1"

    def cancel_order(self, broker_order_id): ...
    def get_order(self, broker_order_id): ...
    def get_positions(self):
        return []

    def get_funds(self):
        return Funds(market=self.market, cash_available=0)

    def get_ltp(self, symbols):
        return {}


class USOnlyBroker(FakeBroker):
    name = "alpaca"
    markets = frozenset({Market.US})


def registry():
    return {"paper": FakeBroker, "alpaca": USOnlyBroker}


@pytest.fixture
def config():
    return load_config(REPO_CONFIG)


def test_create_broker_from_config(config):
    broker = create_broker(Market.IN, config.markets[Market.IN], Secrets(), registry=registry)
    assert isinstance(broker, FakeBroker) and broker.market is Market.IN


def test_unimplemented_broker_has_clear_error(config):
    cfg = config.markets[Market.IN].model_copy(update={"broker": "zerodha"})
    with pytest.raises(BrokerError, match="'zerodha' is not implemented yet"):
        create_broker(Market.IN, cfg, Secrets(), registry=registry)


def test_broker_rejects_wrong_market(config):
    with pytest.raises(BrokerError, match="does not trade market IN"):
        USOnlyBroker(Market.IN, config.markets[Market.IN], Secrets())
