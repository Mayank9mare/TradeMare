from pydantic import SecretStr

from tests.test_broker_adapters import cfg, order
from trademare.config import Secrets
from trademare.core.models import Market, OrderSide, OrderStatus, OrderType
from trademare.execution.brokers.base import BrokerError, LoginRequired
from trademare.execution.brokers.groww import API, TOKEN_URL, GrowwBroker, tick, totp_now


class FakeGroww:
    """Scripted Groww REST API: routes (method, path) to canned JSON, records every request."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, dict | None, str]] = []
        self.gtts: dict[str, dict] = {}
        self.orders: list[dict] = []
        self.holdings = [{"trading_symbol": "INFY", "quantity": 10, "t1_quantity": 2, "average_price": 1500}]
        self.reject_tokens: set[str] = set()
        self.tokens_issued = 0

    def __call__(self, method, url, headers, params, body):
        token = headers["Authorization"].removeprefix("Bearer ")
        self.calls.append((method, url.removeprefix(API), params, body, token))
        if url == TOKEN_URL:
            self.tokens_issued += 1
            return 200, {"token": f"tok{self.tokens_issued}"}
        if token in self.reject_tokens:
            return 401, {}
        path = url.removeprefix(API)
        ok = lambda payload: (200, {"status": "SUCCESS", "payload": payload})  # noqa: E731
        if path == "/order/create":
            return ok({"groww_order_id": f"G{len(self.calls)}", "order_status": "NEW"})
        if path.startswith("/order/detail/"):
            return ok({"order_status": "EXECUTED", "filled_quantity": 10, "average_fill_price": 1501.5})
        if path == "/order/cancel":
            return ok({"order_status": "CANCELLATION_REQUESTED"})
        if path == "/order-advance/create":
            gid = f"gtt{len(self.gtts) + 1}"
            self.gtts[gid] = body | {"smart_order_id": gid, "status": "ACTIVE"}
            return ok({"smart_order_id": gid, "status": "ACTIVE"})
        if path.startswith("/order-advance/status/"):
            return ok(self.gtts[path.rsplit("/", 1)[1]])
        if path.startswith("/order-advance/cancel/"):
            self.gtts[path.rsplit("/", 1)[1]]["status"] = "CANCELLED"
            return ok({"status": "CANCELLED"})
        if path == "/order/list":
            return ok({"order_list": self.orders})
        if path == "/holdings/user":
            return ok({"holdings": self.holdings})
        if path == "/margins/detail/user":
            return ok({"clear_cash": 900.0, "equity_margin_details": {"cnc_balance_available": 850.5}})
        if path == "/live-data/ltp":
            return ok({s: 100.0 + i for i, s in enumerate(params["exchange_symbols"])})
        return 404, {}

    def paths(self):
        return [c[1] for c in self.calls]


def groww(api, tmp_path, **secrets):
    keys = {"groww_api_key": SecretStr("key"), "groww_totp_secret": SecretStr("JBSWY3DPEHPK3PXP")} | secrets
    return GrowwBroker(
        Market.IN, cfg(Market.IN), Secrets(_env_file=None, **keys), transport=api, token_dir=tmp_path
    )


def test_totp_matches_rfc6238_and_ticks():
    # RFC 6238 test vector (SHA-1 secret "12345678901234567890", T=59 -> 94287082, 6-digit: 287082)
    assert totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59) == "287082"
    assert tick(1406.62) == 1406.6 and tick(1406.63) == 1406.65


def test_limit_entry_and_market_exit(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    assert b.place_order(order(order_type=OrderType.LIMIT)).startswith("G")
    body = api.calls[-1][3]
    assert body | {"order_reference_id": "x"} == {
        "trading_symbol": "INFY", "quantity": body["quantity"], "price": tick(body["price"]),
        "trigger_price": None, "validity": "DAY", "exchange": "NSE", "segment": "CASH", "product": "CNC",
        "order_type": "LIMIT",
        "transaction_type": "BUY", "order_reference_id": "x",
    }  # fmt: skip
    assert 8 <= len(body["order_reference_id"]) <= 20 and body["order_reference_id"].isalnum()
    b.place_order(order(side=OrderSide.SELL, order_type=OrderType.MARKET))
    assert api.calls[-1][3]["order_type"] == "MARKET" and api.calls[-1][3]["price"] is None
    assert b.get_order("G1") == b.get_order("G1") and b.get_order("G1").status is OrderStatus.FILLED
    assert b.get_order("G1").average_price == 1501.5
    assert api.tokens_issued == 1  # token cached across calls


def test_no_naked_shorts(tmp_path):
    api = FakeGroww()
    api.holdings = []
    try:
        groww(api, tmp_path).place_order(order(side=OrderSide.SELL, order_type=OrderType.MARKET))
        raise AssertionError("expected BrokerError")
    except BrokerError as e:
        assert "short selling" in str(e)


def test_gtt_stop_is_a_buffered_limit_sell(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    pid = b.place_protection("INFY", OrderSide.SELL, 10, stop=1400.0, target=None, last_price=1500.0)
    gtt = api.gtts[pid]
    assert (
        gtt["smart_order_type"] == "GTT"
        and gtt["product_type"] == "CNC"
        and gtt["trigger_direction"] == "DOWN"
    )
    assert gtt["trigger_price"] == "1400.00"
    assert gtt["order"] == {
        "order_type": "LIMIT",
        "price": "1386.00",
        "transaction_type": "SELL",
    }  # 1% buffer
    assert b.protection_status(pid).status is OrderStatus.OPEN

    gtt["status"], gtt["triggered_at"] = "TRIGGERED", "2026-10-21T10:15:00"
    api.orders = [
        {"groww_order_id": "OLD", "trading_symbol": "INFY", "transaction_type": "SELL", "quantity": 10,
         "created_at": "2026-10-01T10:00:00", "order_status": "EXECUTED", "filled_quantity": 10,
         "average_fill_price": 1700},
        {"groww_order_id": "C1", "trading_symbol": "INFY", "transaction_type": "SELL", "quantity": 10,
         "created_at": "2026-10-21T10:15:01", "order_status": "EXECUTED", "filled_quantity": 10,
         "average_fill_price": 1395.2},
    ]  # fmt: skip
    update = b.protection_status(pid)
    assert (
        update.status is OrderStatus.FILLED and update.average_price == 1395.2
    )  # the child, not the old sell


def test_target_pair_cancels_the_other_leg(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    pid = b.place_protection("INFY", OrderSide.SELL, 10, stop=1400.0, target=1700.0, last_price=1500.0)
    stop_id, target_id = pid.split(",")
    assert (
        api.gtts[target_id]["trigger_direction"] == "UP"
        and api.gtts[target_id]["order"]["price"] == "1700.00"
    )
    api.gtts[target_id] |= {"status": "TRIGGERED", "triggered_at": "2026-10-21T11:00:00"}
    api.orders = [
        {"groww_order_id": "C2", "trading_symbol": "INFY", "transaction_type": "SELL", "quantity": 10,
         "created_at": "2026-10-21T11:00:02", "order_status": "EXECUTED", "filled_quantity": 10,
         "average_fill_price": 1701.0}
    ]  # fmt: skip
    assert b.protection_status(pid).average_price == 1701.0
    assert api.gtts[stop_id]["status"] == "CANCELLED"


def test_cancel_and_dead_protection(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    pid = b.place_protection("INFY", OrderSide.SELL, 10, stop=1400.0, target=None, last_price=1500.0)
    b.cancel_protection(pid)
    assert api.gtts[pid]["status"] == "CANCELLED"
    update = b.protection_status(pid)
    assert update.is_dead and "CANCELLED" in update.message  # the desk re-places a lost stop


def test_expired_token_is_renewed_once_then_login_required(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    b.get_funds()
    api.reject_tokens.add("tok1")
    assert b.get_funds().cash_available == 850.5 and api.tokens_issued == 2
    assert groww(api, tmp_path).get_funds()  # a new instance reuses the cached token
    assert api.tokens_issued == 2
    api.reject_tokens.update({"tok2", "tok3"})
    try:
        b.get_funds()
        raise AssertionError("expected LoginRequired")
    except LoginRequired:
        pass


def test_login_needs_a_key(tmp_path):
    b = GrowwBroker(
        Market.IN, cfg(Market.IN), Secrets(_env_file=None), transport=FakeGroww(), token_dir=tmp_path
    )
    try:
        b.connect()
        raise AssertionError("expected LoginRequired")
    except LoginRequired as e:
        assert "GROWW_API_KEY" in str(e)


def test_approval_key_sends_checksum(tmp_path):
    api = FakeGroww()
    groww(api, tmp_path, groww_totp_secret=None, groww_api_secret=SecretStr("s3cret")).connect()
    body = api.calls[0][3]
    assert (
        body["key_type"] == "approval" and len(body["checksum"]) == 64 and isinstance(body["timestamp"], int)
    )


def test_positions_funds_and_ltp_batches(tmp_path):
    api = FakeGroww()
    b = groww(api, tmp_path)
    [p] = b.get_positions()
    assert (p.symbol, p.quantity, p.average_price) == ("INFY", 12, 1500.0)
    symbols = [f"S{i}" for i in range(60)]
    prices = b.get_ltp(symbols)
    assert len(prices) == 60 and prices["S0"] == 100.0
    assert sum(1 for c in api.calls if c[1] == "/live-data/ltp") == 2  # 50 + 10
