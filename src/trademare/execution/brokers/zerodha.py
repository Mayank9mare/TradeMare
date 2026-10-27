"""Zerodha Kite Connect adapter (India, equity delivery / CNC).

- Login: Kite access tokens expire every morning (~6 AM IST). Run
  ``trademare broker login zerodha`` once per trading day: it prints the login
  URL, you paste the ``request_token`` from the redirect, and the access token
  is stored in data/tokens/zerodha.json.
- Entries/exits: regular CNC orders (LIMIT entries, MARKET exits), DAY validity.
- Protection: GTT (single-leg stop, or two-leg OCO when a target is set),
  which persists across sessions unlike exchange SL orders.
- SEBI retail-algo rules require API orders from a broker-whitelisted static
  IP; that is an account/network setting, not something this code can do.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from trademare.core.models import Funds, Market, Order, OrderSide, OrderStatus, OrderType, Position
from trademare.execution.brokers.base import (
    BrokerAdapter,
    BrokerError,
    LoginRequired,
    OrderUpdate,
    register_broker,
)
from trademare.execution.brokers.tokens import load_token

IST = ZoneInfo("Asia/Kolkata")
TOKEN_ROLLOVER = time(6, 0)  # Kite tokens are invalidated around 6 AM IST

_STATUS = {
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "OPEN": OrderStatus.OPEN,
    "TRIGGER PENDING": OrderStatus.OPEN,
}


def token_valid_for(token_day: date, now: datetime | None = None) -> bool:
    """A token obtained on IST day D is valid until 06:00 IST on D+1."""
    now = (now or datetime.now(IST)).astimezone(IST)
    return now < datetime.combine(token_day + timedelta(days=1), TOKEN_ROLLOVER, IST)


def session_active(now: datetime | None = None) -> bool:
    """True when today's Zerodha login is saved and still valid (no network call)."""
    stored = load_token("zerodha")
    return stored is not None and token_valid_for(stored[1], now)


@register_broker
class ZerodhaBroker(BrokerAdapter):
    name = "zerodha"
    markets: ClassVar[frozenset[Market]] = frozenset({Market.IN})

    def __init__(self, market, config, secrets, client: Any = None) -> None:
        super().__init__(market, config, secrets)
        self._kite = client

    # ------------------------------------------------------------------ session
    def login_url(self) -> str:
        return self._client(require_token=False).login_url()

    def exchange_request_token(self, request_token: str) -> str:
        if self.secrets.zerodha_api_secret is None:
            raise BrokerError("ZERODHA_API_SECRET is not set in .env")
        kite = self._client(require_token=False)
        session = kite.generate_session(
            request_token, api_secret=self.secrets.zerodha_api_secret.get_secret_value()
        )
        return session["access_token"]

    def connect(self) -> None:
        self._client()

    def _client(self, require_token: bool = True):
        if self._kite is not None:
            return self._kite
        if self.secrets.zerodha_api_key is None:
            raise BrokerError("ZERODHA_API_KEY is not set in .env")
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=self.secrets.zerodha_api_key.get_secret_value())
        if require_token:
            stored = load_token(self.name)
            if stored is None or not token_valid_for(stored[1]):
                raise LoginRequired("Zerodha session expired: run `trademare broker login zerodha`")
            kite.set_access_token(stored[0])
            self._kite = kite
        return kite

    def _call(self, fn, *args, **kwargs):
        from kiteconnect import exceptions as kex

        try:
            return fn(*args, **kwargs)
        except kex.TokenException as e:
            raise LoginRequired(f"Zerodha session rejected: {e}") from e
        except kex.KiteException as e:
            raise BrokerError(f"Zerodha: {e}") from e

    def get_holdings_detail(self) -> list[dict]:
        """Demat holdings with average buy price and Kite's latest price (read-only)."""
        kite = self._client()
        out = []
        for r in self._call(kite.holdings):
            qty = int(r.get("quantity") or 0) + int(r.get("t1_quantity") or 0)
            if qty <= 0:
                continue
            out.append({
                "symbol": r["tradingsymbol"],
                "exchange": r.get("exchange"),
                "quantity": qty,
                "average_price": float(r.get("average_price") or 0),
                "last_price": float(r.get("last_price") or 0),
                "day_change_pct": float(r.get("day_change_percentage") or 0),
            })  # fmt: skip
        return out

    # ------------------------------------------------------------------ orders
    def _market_open(self) -> bool:
        from trademare.core.clock import MarketClock

        try:
            return MarketClock(Market.IN).is_open()
        except ValueError:
            return False

    @staticmethod
    def _split_id(broker_order_id: str) -> tuple[str, str]:
        """Order ids for after-market orders are returned as 'amo:<id>' (cancel needs the variety)."""
        variety, sep, order_id = broker_order_id.partition(":")
        return (variety, order_id) if sep else ("regular", broker_order_id)

    def place_order(self, order: Order) -> str:
        kite = self._client()
        # Orders approved outside market hours must go in as after-market orders (AMO).
        variety = kite.VARIETY_REGULAR if self._market_open() else kite.VARIETY_AMO
        if order.side is OrderSide.SELL and not self._holds(order.symbol, order.quantity):
            raise BrokerError("short selling equity delivery is not possible in India (use futures)")
        kwargs = dict(
            variety=variety,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=order.symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY
            if order.side is OrderSide.BUY
            else kite.TRANSACTION_TYPE_SELL,
            quantity=order.quantity,
            product=kite.PRODUCT_CNC,
            validity=kite.VALIDITY_DAY,
            tag="trademare",
        )
        if order.order_type is OrderType.LIMIT:
            kwargs.update(order_type=kite.ORDER_TYPE_LIMIT, price=order.limit_price)
        elif order.order_type is OrderType.MARKET:
            # Kite requires market protection for API market orders; -1 = exchange default.
            kwargs.update(order_type=kite.ORDER_TYPE_MARKET, market_protection=-1)
        else:
            raise BrokerError(f"unsupported day order type {order.order_type}")
        order_id = str(self._call(kite.place_order, **kwargs))
        return order_id if variety == kite.VARIETY_REGULAR else f"{variety}:{order_id}"

    def _holds(self, symbol: str, quantity: int) -> bool:
        kite = self._client()
        held = sum(
            h["quantity"] + h.get("t1_quantity", 0)
            for h in self._call(kite.holdings)
            if h["tradingsymbol"] == symbol
        )
        return held >= quantity

    def cancel_order(self, broker_order_id: str) -> None:
        kite = self._client()
        variety, order_id = self._split_id(broker_order_id)
        self._call(kite.cancel_order, variety=variety, order_id=order_id)

    def get_order(self, broker_order_id: str) -> OrderUpdate:
        kite = self._client()
        history = self._call(kite.order_history, self._split_id(broker_order_id)[1])
        if not history:
            raise BrokerError(f"Zerodha has no order {broker_order_id}")
        last = history[-1]
        return OrderUpdate(
            status=_STATUS.get(last["status"], OrderStatus.OPEN),
            filled_quantity=int(last.get("filled_quantity") or 0),
            average_price=float(last["average_price"]) if last.get("average_price") else None,
            message=last.get("status_message"),
        )

    # ------------------------------------------------------------------ protection (GTT)
    def place_protection(self, symbol, side, quantity, stop, target, last_price) -> str:
        kite = self._client()
        txn = kite.TRANSACTION_TYPE_SELL if side is OrderSide.SELL else kite.TRANSACTION_TYPE_BUY
        leg = lambda price: {  # noqa: E731
            "exchange": kite.EXCHANGE_NSE,
            "tradingsymbol": symbol,
            "transaction_type": txn,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_LIMIT,
            "product": kite.PRODUCT_CNC,
            "price": price,
        }
        # GTT legs must be limit orders: price the stop leg through the trigger by the configured
        # buffer so it fills in a fast market. A gap beyond the buffer is handled by the desk.
        buffer = self.config.stop_loss.limit_buffer_pct / 100
        stop_limit = round(stop * (1 - buffer if side is OrderSide.SELL else 1 + buffer), 1)
        if target is None:
            result = self._call(
                kite.place_gtt,
                trigger_type=kite.GTT_TYPE_SINGLE,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NSE,
                trigger_values=[stop],
                last_price=last_price,
                orders=[leg(stop_limit)],
            )
        else:
            result = self._call(
                kite.place_gtt,
                trigger_type=kite.GTT_TYPE_OCO,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NSE,
                trigger_values=[stop, target],
                last_price=last_price,
                orders=[leg(stop_limit), leg(target)],
            )
        return str(result["trigger_id"])

    def cancel_protection(self, protection_id: str) -> None:
        kite = self._client()
        gtt = self._call(kite.get_gtt, int(protection_id))
        if gtt.get("status") == "triggered":
            # The GTT already fired: cancel the limit order it placed if that is still working.
            for leg in gtt.get("orders", []):
                order_id = ((leg.get("result") or {}).get("order_result") or {}).get("order_id")
                if order_id and self.get_order(str(order_id)).status is OrderStatus.OPEN:
                    self.cancel_order(str(order_id))
            return
        self._call(kite.delete_gtt, int(protection_id))

    def protection_status(self, protection_id: str) -> OrderUpdate:
        kite = self._client()
        gtt = self._call(kite.get_gtt, int(protection_id))
        status = gtt.get("status")
        if status == "active":
            return OrderUpdate(OrderStatus.OPEN)
        if status != "triggered":
            return OrderUpdate(OrderStatus.CANCELLED, message=f"GTT {status}")
        for leg in gtt.get("orders", []):
            order_id = ((leg.get("result") or {}).get("order_result") or {}).get("order_id")
            if order_id:
                return self.get_order(str(order_id))
        return OrderUpdate(OrderStatus.OPEN, message="GTT triggered; order pending")

    # ------------------------------------------------------------------ account
    def get_positions(self) -> list[Position]:
        """Delivery shares held: demat holdings (including yesterday's unsettled buys) plus today's
        delivery buys and sells, which Kite lists under positions until they settle."""
        from trademare.core.models import Direction

        kite = self._client()
        qty: dict[str, int] = {}
        avg: dict[str, float] = {}
        for h in self._call(kite.holdings):
            q = int(h["quantity"]) + int(h.get("t1_quantity") or 0)
            if q > 0 and h["average_price"] > 0:
                qty[h["tradingsymbol"]] = q
                avg[h["tradingsymbol"]] = float(h["average_price"])
        for p in (self._call(kite.positions) or {}).get("net", []):
            if p.get("product") != "CNC" or p.get("exchange") != "NSE":
                continue
            sym = p["tradingsymbol"]
            day_qty = int(p.get("day_buy_quantity") or 0) - int(p.get("day_sell_quantity") or 0)
            if day_qty:
                qty[sym] = qty.get(sym, 0) + day_qty
                avg.setdefault(sym, float(p.get("average_price") or 0) or 1.0)
        return [
            Position(market=Market.IN, symbol=s, direction=Direction.LONG, quantity=q, average_price=avg[s])
            for s, q in qty.items()
            if q > 0
        ]

    def get_funds(self) -> Funds:
        kite = self._client()
        equity = self._call(kite.margins, "equity")
        return Funds(market=Market.IN, cash_available=float(equity["available"]["live_balance"]))

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        kite = self._client()
        data = self._call(kite.ltp, [f"NSE:{s}" for s in symbols])
        return {k.split(":", 1)[1]: float(v["last_price"]) for k, v in data.items()}
