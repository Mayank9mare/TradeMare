"""Dhan (DhanHQ v2) adapter (India, equity delivery / CNC).

- Auth: DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in .env (tokens generated in the
  Dhan web console are valid for about 30 days).
- Instruments: Dhan uses numeric security ids; the NSE equity map is built from
  Dhan's public scrip master and cached for a week.
- Entries/exits: CNC day orders. Protection: Forever orders (SINGLE stop, or
  OCO stop + target), which persist across sessions.

Note: Forever-order status values are mapped from Dhan's documentation and
have not yet been verified against a live account; reconcile manually in the
Dhan app during the first live weeks.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import requests

from trademare.core.models import Direction, Funds, Market, Order, OrderSide, OrderStatus, OrderType, Position
from trademare.execution.brokers.base import (
    BrokerAdapter,
    BrokerError,
    LoginRequired,
    OrderUpdate,
    register_broker,
)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_CACHE = Path("data/cache/dhan_nse_equity.json")
SCRIP_MAX_AGE = 7 * 24 * 3600

_STATUS = {
    "TRADED": OrderStatus.FILLED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "PENDING": OrderStatus.OPEN,
    "TRANSIT": OrderStatus.OPEN,
    "CONFIRM": OrderStatus.OPEN,  # Forever order armed
    "TRIGGERED": OrderStatus.OPEN,  # Forever order fired; child order working
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.CANCELLED,
}


def parse_scrip_master(text: str) -> dict[str, str]:
    """NSE equity symbol -> Dhan security id."""
    df = pd.read_csv(
        io.StringIO(text),
        usecols=[
            "SEM_EXM_EXCH_ID",
            "SEM_SEGMENT",
            "SEM_SMST_SECURITY_ID",
            "SEM_TRADING_SYMBOL",
            "SEM_SERIES",
        ],
        dtype=str,
    )
    eq = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_SEGMENT"] == "E") & (df["SEM_SERIES"] == "EQ")]
    return dict(zip(eq["SEM_TRADING_SYMBOL"], eq["SEM_SMST_SECURITY_ID"], strict=True))


def load_security_ids(cache: Path = SCRIP_CACHE, fetch=None) -> dict[str, str]:
    if cache.exists() and time.time() - cache.stat().st_mtime < SCRIP_MAX_AGE:
        return json.loads(cache.read_text())
    if fetch is None:
        resp = requests.get(SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()
        text = resp.text
    else:
        text = fetch()
    mapping = parse_scrip_master(text)
    if not mapping:
        raise BrokerError("Dhan scrip master contained no NSE equities")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping))
    return mapping


@register_broker
class DhanBroker(BrokerAdapter):
    name = "dhan"
    markets: ClassVar[frozenset[Market]] = frozenset({Market.IN})

    def __init__(
        self, market, config, secrets, client: Any = None, security_ids: dict[str, str] | None = None
    ):
        super().__init__(market, config, secrets)
        self._dhan = client
        self._ids = security_ids

    def connect(self) -> None:
        self._client()

    def _client(self):
        if self._dhan is None:
            if self.secrets.dhan_client_id is None or self.secrets.dhan_access_token is None:
                raise LoginRequired("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are not set in .env")
            from dhanhq import DhanContext, dhanhq

            ctx = DhanContext(
                self.secrets.dhan_client_id.get_secret_value(),
                self.secrets.dhan_access_token.get_secret_value(),
            )
            self._dhan = dhanhq(ctx)
        return self._dhan

    def security_id(self, symbol: str) -> str:
        if self._ids is None:
            self._ids = load_security_ids()
        try:
            return self._ids[symbol]
        except KeyError:
            raise BrokerError(f"Dhan has no NSE equity security id for {symbol}") from None

    @staticmethod
    def _data(response: dict) -> Any:
        if not isinstance(response, dict) or response.get("status") != "success":
            remarks = response.get("remarks") if isinstance(response, dict) else response
            text = str(remarks)
            if "token" in text.lower() and ("invalid" in text.lower() or "expired" in text.lower()):
                raise LoginRequired(f"Dhan access token rejected: {text}")
            raise BrokerError(f"Dhan: {text}")
        return response.get("data")

    # ------------------------------------------------------------------ orders
    def place_order(self, order: Order) -> str:
        dhan = self._client()
        if order.side is OrderSide.SELL and not self._holds(order.symbol, order.quantity):
            raise BrokerError("short selling equity delivery is not possible in India (use futures)")
        if order.order_type not in (OrderType.LIMIT, OrderType.MARKET):
            raise BrokerError(f"unsupported day order type {order.order_type}")
        data = self._data(
            dhan.place_order(
                security_id=self.security_id(order.symbol),
                exchange_segment=dhan.NSE,
                transaction_type=dhan.BUY if order.side is OrderSide.BUY else dhan.SELL,
                quantity=order.quantity,
                order_type=dhan.LIMIT if order.order_type is OrderType.LIMIT else dhan.MARKET,
                product_type=dhan.CNC,
                price=order.limit_price or 0,
                validity=dhan.DAY,
                # Outside market hours the order is queued as an after-market order for the open.
                after_market_order=not self._market_open(),
                amo_time="OPEN",
                tag="trademare",
            )
        )
        return str(data["orderId"])

    @staticmethod
    def _market_open() -> bool:
        from trademare.core.clock import MarketClock

        try:
            return MarketClock(Market.IN).is_open()
        except ValueError:
            return False

    def _holds(self, symbol: str, quantity: int) -> bool:
        holdings = self._data(self._client().get_holdings()) or []
        return (
            sum(int(h.get("totalQty", 0)) for h in holdings if h.get("tradingSymbol") == symbol) >= quantity
        )

    def cancel_order(self, broker_order_id: str) -> None:
        self._data(self._client().cancel_order(broker_order_id))

    def get_order(self, broker_order_id: str) -> OrderUpdate:
        data = self._data(self._client().get_order_by_id(broker_order_id))
        row = data[0] if isinstance(data, list) else data
        return _update(row)

    # ------------------------------------------------------------------ protection (Forever orders)
    def place_protection(self, symbol, side, quantity, stop, target, last_price) -> str:
        dhan = self._client()
        buffer = self.config.stop_loss.limit_buffer_pct / 100
        stop_limit = round(stop * (1 - buffer if side is OrderSide.SELL else 1 + buffer), 1)
        kwargs: dict[str, Any] = dict(
            security_id=self.security_id(symbol),
            exchange_segment=dhan.NSE,
            transaction_type=dhan.SELL if side is OrderSide.SELL else dhan.BUY,
            product_type=dhan.CNC,
            order_type=dhan.LIMIT,
            quantity=quantity,
            price=stop_limit,
            trigger_Price=stop,
            tag="trademare",
        )
        if target is not None:
            kwargs.update(order_flag="OCO", price1=target, trigger_Price1=target, quantity1=quantity)
        return str(self._data(dhan.place_forever(**kwargs))["orderId"])

    def cancel_protection(self, protection_id: str) -> None:
        self._data(self._client().cancel_forever(protection_id))

    def protection_status(self, protection_id: str) -> OrderUpdate:
        rows = self._data(self._client().get_forever()) or []
        for row in rows:
            if str(row.get("orderId")) == str(protection_id):
                return _update(row)
        return OrderUpdate(OrderStatus.CANCELLED, message="Forever order no longer listed")

    # ------------------------------------------------------------------ account
    def get_positions(self) -> list[Position]:
        holdings = self._data(self._client().get_holdings()) or []
        return [
            Position(
                market=Market.IN,
                symbol=h["tradingSymbol"],
                direction=Direction.LONG,
                quantity=int(h["totalQty"]),
                average_price=float(h["avgCostPrice"]),
            )
            for h in holdings
            if int(h.get("totalQty", 0)) > 0 and float(h.get("avgCostPrice", 0)) > 0
        ]

    def get_funds(self) -> Funds:
        data = self._data(self._client().get_fund_limits())
        # Dhan's API spells this field "availabelBalance".
        balance = data.get("availabelBalance", data.get("availableBalance", 0.0))
        return Funds(market=Market.IN, cash_available=float(balance))

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        dhan = self._client()
        ids = {self.security_id(s): s for s in symbols}
        data = self._data(dhan.ticker_data({dhan.NSE: [int(i) for i in ids]}))
        quotes = (data.get("data") or data).get(dhan.NSE, {}) if isinstance(data, dict) else {}
        return {ids[str(k)]: float(v["last_price"]) for k, v in quotes.items() if str(k) in ids}


def _update(row: dict) -> OrderUpdate:
    status = _STATUS.get(str(row.get("orderStatus", "")).upper(), OrderStatus.OPEN)
    price = row.get("averageTradedPrice") or row.get("price")
    return OrderUpdate(
        status=status,
        filled_quantity=int(row.get("filledQty") or 0),
        average_price=float(price) if price and status is OrderStatus.FILLED else None,
        message=row.get("omsErrorDescription") or None,
    )
