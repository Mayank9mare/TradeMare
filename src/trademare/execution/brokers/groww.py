"""Groww Trade API adapter (India, equity delivery / CNC).

Talks to Groww's REST API directly (https://api.groww.in/v1). Groww's own Python SDK pins
pandas < 3, which conflicts with this project, so the few calls needed are made with ``requests``
using the same endpoints, headers and payloads as SDK 1.5.0.

- Auth: ``GROWW_API_KEY`` plus either ``GROWW_TOTP_SECRET`` (TOTP key: no daily approval, so the
  access token is renewed automatically) or ``GROWW_API_SECRET`` (approval key: Groww asks you to
  approve it in the Groww app once a day). The access token is cached in data/tokens/groww.json
  until 06:00 IST the next day and renewed when Groww rejects it (token requests are limited to
  150 a day).
- Entries/exits: CNC day orders on NSE (LIMIT entries, MARKET exits).
- Protection: GTT smart orders. A stop is a GTT that triggers DOWN (for a long) and places a LIMIT
  sell priced ``stop_loss.limit_buffer_pct`` past the trigger. Groww's OCO is documented for
  intraday (MIS) only, so a target becomes a second GTT; when one leg triggers the other is
  cancelled. GTTs placed via the API are valid for a year.
- Live prices (``get_ltp``) need Groww's paid data plan; the trading calls do not.

Not yet verified against a live account (no account was available when this was written): how
Groww reports the order a triggered GTT placed (matched here by symbol, side and quantity in the
order list) and how orders placed outside market hours are queued. Reconcile in the Groww app
during the first live weeks, as for Dhan.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid
from typing import Any, ClassVar

import requests

from trademare.core.models import Direction, Funds, Market, Order, OrderSide, OrderStatus, OrderType, Position
from trademare.execution.brokers.base import (
    BrokerAdapter,
    BrokerError,
    LoginRequired,
    OrderUpdate,
    register_broker,
)
from trademare.execution.brokers.tokens import TOKEN_DIR, load_token, save_token
from trademare.execution.brokers.zerodha import token_valid_for

API = "https://api.groww.in/v1"
TOKEN_URL = f"{API}/token/api/access"
CASH, NSE, CNC, DAY = "CASH", "NSE", "CNC", "DAY"
LTP_BATCH = 50  # instruments per live-data call

_STATUS = {
    "NEW": OrderStatus.OPEN,
    "ACKED": OrderStatus.OPEN,
    "TRIGGER_PENDING": OrderStatus.OPEN,
    "APPROVED": OrderStatus.OPEN,
    "MODIFICATION_REQUESTED": OrderStatus.OPEN,
    "CANCELLATION_REQUESTED": OrderStatus.OPEN,
    "EXECUTED": OrderStatus.FILLED,
    "DELIVERY_AWAITED": OrderStatus.FILLED,
    "COMPLETED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "FAILED": OrderStatus.REJECTED,
}
_GTT_DEAD = {"CANCELLED", "EXPIRED", "FAILED"}
_GTT_FIRED = {"TRIGGERED", "COMPLETED"}


def totp_now(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (what an authenticator app shows) from a base32 secret."""
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    counter = struct.pack(">Q", int((time.time() if at is None else at) // step))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def tick(price: float) -> float:
    """Round to NSE's 0.05 tick."""
    return round(round(price * 20) / 20, 2)


def _headers(token: str) -> dict[str, str]:
    return {
        "x-request-id": str(uuid.uuid4()),
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-client-id": "growwapi",
        "x-client-platform": "growwapi-python-client",
        "x-client-platform-version": "1.5.0",
        "x-api-version": "1.0",
    }


def http_transport(method: str, url: str, headers: dict, params: dict | None, body: dict | None):
    """(status_code, json) for one request. Tests replace this."""
    resp = requests.request(method, url, headers=headers, params=params, json=body, timeout=20)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data


@register_broker
class GrowwBroker(BrokerAdapter):
    name = "groww"
    markets: ClassVar[frozenset[Market]] = frozenset({Market.IN})

    def __init__(self, market, config, secrets, transport=None, token_dir=TOKEN_DIR) -> None:
        super().__init__(market, config, secrets)
        self._send = transport or http_transport
        self._token_dir = token_dir
        self._token: str | None = None

    # ------------------------------------------------------------------ auth
    def connect(self) -> None:
        self._access_token()

    def _access_token(self, renew: bool = False) -> str:
        if self._token and not renew:
            return self._token
        if not renew and (stored := load_token(self.name, self._token_dir)) and token_valid_for(stored[1]):
            self._token = stored[0]
            return self._token
        if self.secrets.groww_api_key is None:
            raise LoginRequired("GROWW_API_KEY is not set in .env")
        if self.secrets.groww_totp_secret is not None:
            body: dict[str, Any] = {
                "key_type": "totp",
                "totp": totp_now(self.secrets.groww_totp_secret.get_secret_value()),
            }
        elif self.secrets.groww_api_secret is not None:
            ts = int(time.time())
            secret = self.secrets.groww_api_secret.get_secret_value()
            checksum = hashlib.sha256(f"{secret}{ts}".encode()).hexdigest()
            body = {"key_type": "approval", "checksum": checksum, "timestamp": ts}
        else:
            raise LoginRequired("set GROWW_TOTP_SECRET (recommended) or GROWW_API_SECRET in .env")
        status, data = self._send(
            "POST", TOKEN_URL, _headers(self.secrets.groww_api_key.get_secret_value()), None, body
        )
        if status != 200 or not data.get("token"):
            message = (
                (data.get("error") or {}).get("displayMessage") or data.get("message") or f"HTTP {status}"
            )
            hint = " (approve today's API access in the Groww app)" if body["key_type"] == "approval" else ""
            raise LoginRequired(f"Groww login failed: {message}{hint}")
        self._token = str(data["token"])
        save_token(self.name, self._token, directory=self._token_dir)
        return self._token

    def _api(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        for attempt in (0, 1):
            status, data = self._send(
                method, API + path, _headers(self._access_token(renew=attempt > 0)), params, body
            )
            if status == 401 and attempt == 0:
                continue  # expired token: renew once and retry
            break
        if status == 401:
            raise LoginRequired("Groww rejected the access token")
        if isinstance(data, dict) and data.get("status") == "FAILURE":
            err = data.get("error") or {}
            raise BrokerError(f"Groww {err.get('code', '')}: {err.get('message', 'request failed')}".strip())
        if status == 429:
            raise BrokerError("Groww rate limit reached; try again shortly")
        if status >= 400:
            raise BrokerError(f"Groww HTTP {status} on {path}")
        return data.get("payload", data) if isinstance(data, dict) else data

    # ------------------------------------------------------------------ orders
    def place_order(self, order: Order) -> str:
        if order.side is OrderSide.SELL and not self._holds(order.symbol, order.quantity):
            raise BrokerError("short selling equity delivery is not possible in India (use futures)")
        if order.order_type not in (OrderType.LIMIT, OrderType.MARKET):
            raise BrokerError(f"unsupported day order type {order.order_type}")
        limit = order.order_type is OrderType.LIMIT
        data = self._api(
            "POST",
            "/order/create",
            body={
                "trading_symbol": order.symbol,
                "quantity": order.quantity,
                "price": tick(order.limit_price) if limit and order.limit_price else None,
                "trigger_price": None,
                "validity": DAY,
                "exchange": NSE,
                "segment": CASH,
                "product": CNC,
                "order_type": "LIMIT" if limit else "MARKET",
                "transaction_type": order.side.value,
                "order_reference_id": order.id.replace("-", "")[:20],
            },
        )
        return str(data["groww_order_id"])

    def _holds(self, symbol: str, quantity: int) -> bool:
        return sum(p.quantity for p in self.get_positions() if p.symbol == symbol) >= quantity

    def cancel_order(self, broker_order_id: str) -> None:
        self._api("POST", "/order/cancel", body={"segment": CASH, "groww_order_id": broker_order_id})

    def get_order(self, broker_order_id: str) -> OrderUpdate:
        return _update(self._api("GET", f"/order/detail/{broker_order_id}", params={"segment": CASH}))

    # ------------------------------------------------------------------ protection (GTT)
    def _gtt(
        self, symbol: str, side: OrderSide, quantity: int, trigger: float, direction: str, price: float
    ) -> str:
        data = self._api(
            "POST",
            "/order-advance/create",
            body={
                "smart_order_type": "GTT",
                "reference_id": uuid.uuid4().hex[:20],
                "segment": CASH,
                "trading_symbol": symbol,
                "quantity": quantity,
                "product_type": CNC,
                "exchange": NSE,
                "duration": DAY,  # validity of the order placed when the GTT triggers
                "trigger_price": f"{tick(trigger):.2f}",
                "trigger_direction": direction,
                "order": {
                    "order_type": "LIMIT",
                    "price": f"{tick(price):.2f}",
                    "transaction_type": side.value,
                },
            },
        )
        return str(data["smart_order_id"])

    def place_protection(self, symbol, side, quantity, stop, target, last_price) -> str:
        buffer = self.config.stop_loss.limit_buffer_pct / 100
        selling = side is OrderSide.SELL
        stop_limit = stop * (1 - buffer if selling else 1 + buffer)
        legs = [self._gtt(symbol, side, quantity, stop, "DOWN" if selling else "UP", stop_limit)]
        if target is not None:
            try:
                legs.append(self._gtt(symbol, side, quantity, target, "UP" if selling else "DOWN", target))
            except BrokerError:
                self._cancel_gtt(legs[0])  # never leave a half-built pair behind
                raise
        return ",".join(legs)

    def _get_gtt(self, gtt_id: str) -> dict:
        return self._api("GET", f"/order-advance/status/{CASH}/GTT/internal/{gtt_id}")

    def _cancel_gtt(self, gtt_id: str) -> None:
        self._api("POST", f"/order-advance/cancel/{CASH}/GTT/{gtt_id}")

    def _child_order(self, gtt: dict) -> dict | None:
        """The order a triggered GTT placed: same symbol, side and quantity, placed after the trigger."""
        side = (gtt.get("order") or {}).get("transaction_type")
        since = str(gtt.get("triggered_at") or "")
        matches = [
            o
            for o in (
                self._api("GET", "/order/list", params={"segment": CASH, "page": 0}).get("order_list") or []
            )
            if o.get("trading_symbol") == gtt.get("trading_symbol")
            and o.get("transaction_type") == side
            and int(o.get("quantity") or 0) == int(gtt.get("quantity") or 0)
            and str(o.get("created_at") or "") >= since
        ]
        return max(matches, key=lambda o: str(o.get("created_at") or ""), default=None)

    def cancel_protection(self, protection_id: str) -> None:
        for gtt_id in protection_id.split(","):
            gtt = self._get_gtt(gtt_id)
            status = str(gtt.get("status", "")).upper()
            if status == "ACTIVE":
                self._cancel_gtt(gtt_id)
            elif (
                status in _GTT_FIRED
                and (child := self._child_order(gtt))
                and _update(child).status is OrderStatus.OPEN  # fired, but its order is still working
            ):
                self.cancel_order(str(child["groww_order_id"]))

    def protection_status(self, protection_id: str) -> OrderUpdate:
        ids = protection_id.split(",")
        gtts = {gid: self._get_gtt(gid) for gid in ids}
        fired = [gid for gid, g in gtts.items() if str(g.get("status", "")).upper() in _GTT_FIRED]
        if fired:
            for gid, g in gtts.items():  # the other leg of a stop/target pair must not fire too
                if gid not in fired and str(g.get("status", "")).upper() == "ACTIVE":
                    self._cancel_gtt(gid)
            child = self._child_order(gtts[fired[0]])
            if child is None:
                return OrderUpdate(OrderStatus.OPEN, message="GTT triggered; order not found yet")
            return _update(child)
        if all(str(g.get("status", "")).upper() in _GTT_DEAD for g in gtts.values()):
            statuses = ", ".join(sorted({str(g.get("status")) for g in gtts.values()}))
            return OrderUpdate(OrderStatus.CANCELLED, message=f"GTT {statuses}")
        return OrderUpdate(OrderStatus.OPEN)

    # ------------------------------------------------------------------ account
    def get_positions(self) -> list[Position]:
        holdings = self._api("GET", "/holdings/user").get("holdings") or []
        out = []
        for h in holdings:
            qty = int(h.get("quantity") or 0) + int(h.get("t1_quantity") or 0)
            avg = float(h.get("average_price") or 0)
            if qty > 0 and avg > 0:
                out.append(
                    Position(
                        market=Market.IN,
                        symbol=h["trading_symbol"],
                        direction=Direction.LONG,
                        quantity=qty,
                        average_price=avg,
                    )
                )
        return out

    def get_funds(self) -> Funds:
        data = self._api("GET", "/margins/detail/user")
        equity = data.get("equity_margin_details") or {}
        cash = equity.get("cnc_balance_available", data.get("clear_cash", 0.0))
        return Funds(market=Market.IN, cash_available=float(cash or 0.0))

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in range(0, len(symbols), LTP_BATCH):
            batch = symbols[i : i + LTP_BATCH]
            data = self._api(
                "GET",
                "/live-data/ltp",
                params={"segment": CASH, "exchange_symbols": [f"NSE_{s}" for s in batch]},
            )
            out.update({k.removeprefix("NSE_"): float(v) for k, v in (data or {}).items() if v is not None})
        return out


def _update(row: dict) -> OrderUpdate:
    raw = str(row.get("order_status", "")).upper()
    status = _STATUS.get(raw, OrderStatus.OPEN)
    filled = int(row.get("filled_quantity") or 0)
    if status is OrderStatus.OPEN and filled:
        status = OrderStatus.PARTIALLY_FILLED
    price = row.get("average_fill_price")
    return OrderUpdate(
        status=status,
        filled_quantity=filled,
        average_price=float(price) if price and status is OrderStatus.FILLED else None,
        message=row.get("remark") or None,
    )


__all__ = ["GrowwBroker", "tick", "totp_now"]
