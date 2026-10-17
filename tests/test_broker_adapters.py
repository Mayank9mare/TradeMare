"""Adapter tests against fake SDK clients (no network, no real accounts)."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.core.models import Market, Order, OrderSide, OrderStatus, OrderType
from trademare.execution.brokers import BrokerError, LoginRequired
from trademare.execution.brokers.alpaca import AlpacaBroker
from trademare.execution.brokers.dhan import DhanBroker, parse_scrip_master
from trademare.execution.brokers.zerodha import ZerodhaBroker, token_valid_for

IST = ZoneInfo("Asia/Kolkata")


def cfg(market):
    return load_config(REPO_CONFIG).markets[market]


def order(side=OrderSide.BUY, order_type=OrderType.LIMIT, symbol="INFY", market=Market.IN):
    return Order(
        market=market,
        symbol=symbol,
        side=side,
        quantity=10,
        order_type=order_type,
        limit_price=1500.0 if order_type is OrderType.LIMIT else None,
    )


# ---------------------------------------------------------------- Zerodha
class FakeKite:
    VARIETY_REGULAR, EXCHANGE_NSE, PRODUCT_CNC, VALIDITY_DAY = "regular", "NSE", "CNC", "DAY"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL = "BUY", "SELL"
    ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET = "LIMIT", "MARKET"
    GTT_TYPE_SINGLE, GTT_TYPE_OCO = "single", "two-leg"

    def __init__(self, holdings=()):
        self.calls, self._holdings = [], list(holdings)

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return "K1"

    def holdings(self):
        return self._holdings

    def order_history(self, order_id):
        return [{"status": "OPEN"}, {"status": "COMPLETE", "filled_quantity": 10, "average_price": 1499.5}]

    def place_gtt(self, **kw):
        self.calls.append(("place_gtt", kw))
        return {"trigger_id": 77}

    def get_gtt(self, trigger_id):
        return {"status": "triggered", "orders": [{"result": {"order_result": {"order_id": "K9"}}}]}


def zerodha(kite):
    return ZerodhaBroker(Market.IN, cfg(Market.IN), Secrets(_env_file=None), client=kite)


def test_zerodha_limit_order_mapping():
    kite = FakeKite()
    assert zerodha(kite).place_order(order()) == "K1"
    _, kw = kite.calls[0]
    assert kw["product"] == "CNC" and kw["exchange"] == "NSE" and kw["order_type"] == "LIMIT"
    assert kw["price"] == 1500.0 and kw["validity"] == "DAY" and kw["tag"] == "trademare"


def test_zerodha_market_order_has_protection_and_no_naked_shorts():
    kite = FakeKite(holdings=[{"tradingsymbol": "INFY", "quantity": 10, "t1_quantity": 0}])
    zerodha(kite).place_order(order(OrderSide.SELL, OrderType.MARKET))
    assert kite.calls[0][1]["market_protection"] == -1
    with pytest.raises(BrokerError, match="short selling"):
        zerodha(FakeKite()).place_order(order(OrderSide.SELL, OrderType.MARKET))


def test_zerodha_order_status_and_gtt():
    broker = zerodha(FakeKite())
    update = broker.get_order("K1")
    assert update.is_filled and update.average_price == 1499.5
    kite = FakeKite()
    zerodha(kite).place_protection("INFY", OrderSide.SELL, 10, 1400.0, None, 1500.0)
    zerodha(kite).place_protection("INFY", OrderSide.SELL, 10, 1400.0, 1700.0, 1500.0)
    single, oco = kite.calls[0][1], kite.calls[1][1]
    assert single["trigger_type"] == "single" and single["trigger_values"] == [1400.0]
    assert single["orders"][0]["price"] < 1400.0  # stop leg priced through the trigger
    assert oco["trigger_type"] == "two-leg" and oco["trigger_values"] == [1400.0, 1700.0]
    assert broker.protection_status("77").is_filled  # triggered GTT -> child order K9 filled


def test_zerodha_token_expiry_and_login_required(monkeypatch):
    day = date(2025, 6, 2)
    assert token_valid_for(day, datetime(2025, 6, 3, 5, 59, tzinfo=IST))
    assert not token_valid_for(day, datetime(2025, 6, 3, 6, 0, tzinfo=IST))
    secrets = Secrets(_env_file=None, zerodha_api_key=SecretStr("k"))
    monkeypatch.setattr("trademare.execution.brokers.zerodha.load_token", lambda name: None)
    with pytest.raises(LoginRequired, match="broker login zerodha"):
        ZerodhaBroker(Market.IN, cfg(Market.IN), secrets).connect()


# ---------------------------------------------------------------- Dhan
class FakeDhan:
    NSE, BUY, SELL, LIMIT, MARKET, CNC, DAY = "NSE_EQ", "BUY", "SELL", "LIMIT", "MARKET", "CNC", "DAY"

    def __init__(self):
        self.calls = []

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return {"status": "success", "data": {"orderId": "D1"}}

    def get_order_by_id(self, order_id):
        return {
            "status": "success",
            "data": [{"orderStatus": "TRADED", "filledQty": 10, "averageTradedPrice": 1501}],
        }

    def place_forever(self, **kw):
        self.calls.append(("place_forever", kw))
        return {"status": "success", "data": {"orderId": "F1"}}

    def get_fund_limits(self):
        return {"status": "success", "data": {"availabelBalance": 12345.5}}

    def get_holdings(self):
        return {"status": "failure", "remarks": {"error_message": "Invalid Token / token expired"}}


def dhan(client):
    return DhanBroker(
        Market.IN, cfg(Market.IN), Secrets(_env_file=None), client=client, security_ids={"INFY": "1594"}
    )


def test_dhan_order_mapping_and_status():
    client = FakeDhan()
    assert dhan(client).place_order(order()) == "D1"
    kw = client.calls[0][1]
    assert kw["security_id"] == "1594" and kw["product_type"] == "CNC" and kw["exchange_segment"] == "NSE_EQ"
    update = dhan(client).get_order("D1")
    assert update.is_filled and update.average_price == 1501
    assert dhan(client).get_funds().cash_available == 12345.5  # Dhan's misspelled field


def test_dhan_forever_oco_and_token_errors():
    client = FakeDhan()
    dhan(client).place_protection("INFY", OrderSide.SELL, 10, 1400.0, 1700.0, 1500.0)
    kw = client.calls[0][1]
    assert kw["order_flag"] == "OCO" and kw["trigger_Price"] == 1400.0 and kw["trigger_Price1"] == 1700.0
    with pytest.raises(LoginRequired):
        dhan(client).get_positions()
    with pytest.raises(BrokerError, match="security id"):
        dhan(client).security_id("NOPE")


def test_dhan_scrip_master_parsing():
    csv = (
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES\n"
        "NSE,E,1594,EQUITY,INFY,EQ\nNSE,E,9999,EQUITY,INFY-BE,BE\nBSE,E,500209,EQUITY,INFY,A\n"
        "NSE,D,35000,FUTSTK,INFY-FUT,\n"
    )
    assert parse_scrip_master(csv) == {"INFY": "1594"}


# ---------------------------------------------------------------- Alpaca
class FakeAlpaca:
    def __init__(self, positions=()):
        self.requests, self._positions = [], list(positions)

    def submit_order(self, request):
        self.requests.append(request)
        return SimpleNamespace(id="A1")

    def get_all_positions(self):
        return self._positions

    def get_asset(self, symbol):
        return SimpleNamespace(shortable=False, easy_to_borrow=False)

    def get_order_by_id(self, order_id):
        leg_open = SimpleNamespace(status="new", filled_qty="0", filled_avg_price=None)
        leg_filled = SimpleNamespace(status="filled", filled_qty="10", filled_avg_price="95.5")
        return SimpleNamespace(
            status="canceled", filled_qty="0", filled_avg_price=None, legs=[leg_open, leg_filled]
        )


def alpaca(client):
    return AlpacaBroker(Market.US, cfg(Market.US), Secrets(_env_file=None), client=client)


def test_alpaca_orders_use_client_order_id_and_day_tif():
    client = FakeAlpaca()
    o = order(symbol="AAPL", market=Market.US)
    alpaca(client).place_order(o)
    req = client.requests[0]
    assert req.client_order_id == o.id and req.limit_price == 1500.0 and req.time_in_force.value == "day"
    with pytest.raises(BrokerError, match="not shortable"):
        alpaca(client).place_order(order(OrderSide.SELL, OrderType.MARKET, symbol="AAPL", market=Market.US))


def test_alpaca_protection_gtc_stop_and_oco():
    client = FakeAlpaca()
    alpaca(client).place_protection("AAPL", OrderSide.SELL, 10, 95.0, None, 100.0)
    alpaca(client).place_protection("AAPL", OrderSide.SELL, 10, 95.0, 120.0, 100.0)
    stop, oco = client.requests
    assert stop.time_in_force.value == "gtc" and stop.stop_price == 95.0
    assert oco.order_class.value == "oco" and oco.stop_loss.stop_price == 95.0
    # An OCO whose stop leg executed reports that fill.
    update = alpaca(client).protection_status("A1")
    assert update.status is OrderStatus.FILLED and update.average_price == 95.5
