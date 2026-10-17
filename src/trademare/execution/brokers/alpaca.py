"""Alpaca adapter (US equities).

- Auth: ALPACA_API_KEY / ALPACA_SECRET_KEY in .env; ALPACA_PAPER=true (default)
  targets Alpaca's free paper environment, false targets the live account.
- Entries/exits: DAY limit/market orders; our order id is sent as the
  client_order_id so a retried submission cannot create a duplicate.
- Protection: GTC stop order, or an OCO (stop + take-profit) when a target is set.
- Shorts require a margin account and an easy-to-borrow, shortable asset.
"""

from __future__ import annotations

from typing import Any, ClassVar

from trademare.core.models import Direction, Funds, Market, Order, OrderSide, OrderStatus, OrderType, Position
from trademare.execution.brokers.base import (
    BrokerAdapter,
    BrokerError,
    LoginRequired,
    OrderUpdate,
    register_broker,
)

_STATUS = {
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "done_for_day": OrderStatus.CANCELLED,
    "replaced": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}


def _status(order: Any) -> OrderUpdate:
    raw = getattr(order.status, "value", order.status)
    status = _STATUS.get(str(raw), OrderStatus.OPEN)
    price = order.filled_avg_price
    return OrderUpdate(
        status=status,
        filled_quantity=int(float(order.filled_qty or 0)),
        average_price=float(price) if price is not None else None,
    )


@register_broker
class AlpacaBroker(BrokerAdapter):
    name = "alpaca"
    markets: ClassVar[frozenset[Market]] = frozenset({Market.US})
    supports_short_delivery = True

    def __init__(self, market, config, secrets, client: Any = None) -> None:
        super().__init__(market, config, secrets)
        self._client_obj = client

    @property
    def paper(self) -> bool:
        return self.secrets.alpaca_paper

    def connect(self) -> None:
        self._call(self._client().get_account)

    def _client(self):
        if self._client_obj is None:
            if self.secrets.alpaca_api_key is None or self.secrets.alpaca_secret_key is None:
                raise LoginRequired("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set in .env")
            from alpaca.trading.client import TradingClient

            self._client_obj = TradingClient(
                self.secrets.alpaca_api_key.get_secret_value(),
                self.secrets.alpaca_secret_key.get_secret_value(),
                paper=self.paper,
            )
        return self._client_obj

    def _call(self, fn, *args, **kwargs):
        from alpaca.common.exceptions import APIError

        try:
            return fn(*args, **kwargs)
        except APIError as e:
            if getattr(e, "status_code", None) in (401, 403):
                raise LoginRequired(f"Alpaca rejected the API keys: {e}") from e
            raise BrokerError(f"Alpaca: {e}") from e

    # ------------------------------------------------------------------ orders
    def place_order(self, order: Order) -> str:
        from alpaca.trading.enums import OrderSide as ASide
        from alpaca.trading.enums import TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        client = self._client()
        side = ASide.BUY if order.side is OrderSide.BUY else ASide.SELL
        if order.side is OrderSide.SELL and not self._holds(order.symbol, order.quantity):
            asset = self._call(client.get_asset, order.symbol)
            if not (asset.shortable and asset.easy_to_borrow):
                raise BrokerError(f"{order.symbol} is not shortable/easy to borrow on Alpaca")
        common = dict(
            symbol=order.symbol,
            qty=order.quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=order.id,
        )
        if order.order_type is OrderType.LIMIT:
            request = LimitOrderRequest(limit_price=order.limit_price, **common)
        elif order.order_type is OrderType.MARKET:
            request = MarketOrderRequest(**common)
        else:
            raise BrokerError(f"unsupported day order type {order.order_type}")
        return str(self._call(client.submit_order, request).id)

    def _holds(self, symbol: str, quantity: int) -> bool:
        positions = self._call(self._client().get_all_positions)
        return any(p.symbol == symbol and int(float(p.qty)) >= quantity for p in positions)

    def cancel_order(self, broker_order_id: str) -> None:
        self._call(self._client().cancel_order_by_id, broker_order_id)

    def get_order(self, broker_order_id: str) -> OrderUpdate:
        return _status(self._call(self._client().get_order_by_id, broker_order_id))

    # ------------------------------------------------------------------ protection
    def place_protection(self, symbol, side, quantity, stop, target, last_price) -> str:
        from alpaca.trading.enums import OrderClass, TimeInForce
        from alpaca.trading.enums import OrderSide as ASide
        from alpaca.trading.requests import (
            LimitOrderRequest,
            StopLossRequest,
            StopOrderRequest,
            TakeProfitRequest,
        )

        a_side = ASide.SELL if side is OrderSide.SELL else ASide.BUY
        if target is None:
            request = StopOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=a_side,
                time_in_force=TimeInForce.GTC,
                stop_price=round(stop, 2),
            )
        else:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=a_side,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OCO,
                limit_price=round(target, 2),
                take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            )
        return str(self._call(self._client().submit_order, request).id)

    def modify_protection(self, protection_id, symbol, side, quantity, stop, target, last_price) -> str:
        if target is not None:
            return super().modify_protection(protection_id, symbol, side, quantity, stop, target, last_price)
        from alpaca.trading.requests import ReplaceOrderRequest

        replaced = self._call(
            self._client().replace_order_by_id, protection_id, ReplaceOrderRequest(stop_price=round(stop, 2))
        )
        return str(replaced.id)

    def cancel_protection(self, protection_id: str) -> None:
        self.cancel_order(protection_id)

    def protection_status(self, protection_id: str) -> OrderUpdate:
        order = self._call(self._client().get_order_by_id, protection_id)
        update = _status(order)
        if update.is_filled:
            return update
        for leg in getattr(order, "legs", None) or []:  # OCO: either leg may have executed
            leg_update = _status(leg)
            if leg_update.is_filled:
                return leg_update
        return update

    # ------------------------------------------------------------------ account
    def get_positions(self) -> list[Position]:
        out = []
        for p in self._call(self._client().get_all_positions):
            qty = int(float(p.qty))
            out.append(
                Position(
                    market=Market.US,
                    symbol=p.symbol,
                    direction=Direction.LONG if qty > 0 else Direction.SHORT,
                    quantity=abs(qty),
                    average_price=float(p.avg_entry_price),
                )
            )
        return out

    def get_funds(self) -> Funds:
        account = self._call(self._client().get_account)
        return Funds(
            market=Market.US, cash_available=float(account.cash), margin_available=float(account.buying_power)
        )

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        return {
            p.symbol: float(p.current_price)
            for p in self._call(self._client().get_all_positions)
            if p.symbol in symbols
        }
