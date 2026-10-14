"""Domain models shared by analysis, risk, execution and the dashboard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


class Market(StrEnum):
    IN = "IN"
    US = "US"

    @property
    def currency(self) -> str:
        return {"IN": "INR", "US": "USD"}[self.value]

    @property
    def calendar_code(self) -> str:
        # exchange_calendars has no separate NSE calendar; BSE (XBOM) shares its holidays.
        return {"IN": "XBOM", "US": "XNYS"}[self.value]


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalAction(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class SignalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"  # stop-market (SL-M)
    STOP_LIMIT = "STOP_LIMIT"  # SL


class ProductType(StrEnum):
    DELIVERY = "DELIVERY"  # CNC in India, cash account in the US
    MARGIN = "MARGIN"  # NRML (F&O) in India, margin account in the US
    INTRADAY = "INTRADAY"  # MIS in India


class OrderStatus(StrEnum):
    PENDING = "PENDING"  # created locally, not yet sent
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


def order_side_for(direction: Direction, action: SignalAction) -> OrderSide:
    opening = action is SignalAction.ENTRY
    is_long = direction is Direction.LONG
    return OrderSide.BUY if opening == is_long else OrderSide.SELL


class Signal(BaseModel):
    """A proposed trade awaiting approval."""

    id: str = Field(default_factory=_id)
    market: Market
    symbol: str
    direction: Direction
    action: SignalAction = SignalAction.ENTRY
    quantity: int = Field(gt=0)
    entry_price: float = Field(gt=0, description="Limit price for the order")
    stop_price: float | None = Field(None, gt=0)
    target_price: float | None = Field(None, gt=0)
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    reasons: dict[str, list[str]] = Field(default_factory=dict, description="Analyst name -> reasons")
    status: SignalStatus = SignalStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _levels_consistent(self) -> Self:
        if self.action is SignalAction.ENTRY and self.stop_price is None:
            raise ValueError("entry signals require a stop_price")
        sign = 1 if self.direction is Direction.LONG else -1
        if self.stop_price is not None and sign * (self.entry_price - self.stop_price) <= 0:
            raise ValueError(f"{self.direction} stop must be on the losing side of entry")
        if self.target_price is not None and sign * (self.target_price - self.entry_price) <= 0:
            raise ValueError(f"{self.direction} target must be on the winning side of entry")
        return self

    @property
    def order_side(self) -> OrderSide:
        return order_side_for(self.direction, self.action)

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price

    @property
    def risk_per_share(self) -> float | None:
        return None if self.stop_price is None else abs(self.entry_price - self.stop_price)

    @property
    def risk_amount(self) -> float | None:
        rps = self.risk_per_share
        return None if rps is None else rps * self.quantity

    @property
    def reward_risk(self) -> float | None:
        rps = self.risk_per_share
        if rps is None or self.target_price is None:
            return None
        return abs(self.target_price - self.entry_price) / rps


class Order(BaseModel):
    id: str = Field(default_factory=_id)
    signal_id: str | None = None
    market: Market
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    order_type: OrderType = OrderType.LIMIT
    product: ProductType = ProductType.DELIVERY
    limit_price: float | None = Field(None, gt=0)
    trigger_price: float | None = Field(None, gt=0)
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: str | None = None
    filled_quantity: int = Field(0, ge=0)
    average_fill_price: float | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _prices_for_type(self) -> Self:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type} orders require limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.trigger_price is None:
            raise ValueError(f"{self.order_type} orders require trigger_price")
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity cannot exceed quantity")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)


class Position(BaseModel):
    market: Market
    symbol: str
    direction: Direction
    quantity: int = Field(gt=0)
    average_price: float = Field(gt=0)
    stop_price: float | None = None
    target_price: float | None = None
    opened_at: datetime = Field(default_factory=_now)

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        sign = 1 if self.direction is Direction.LONG else -1
        return sign * (price - self.average_price) * self.quantity


class Funds(BaseModel):
    market: Market
    cash_available: float
    margin_available: float | None = None
