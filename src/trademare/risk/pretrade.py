"""Pre-trade checks: the last look at every order before it reaches the broker, buys and sells alike.

New buys must pass all of them: a sane price (near the last close), a size inside the position cap,
the cash to pay for it, a stock in the universe and not under exchange surveillance, current prices,
and a daily order limit. Sells are checked for one thing that matters above all: never sell more
shares than TradeMare itself holds, so the owner's own shares in the same account are never touched.
Protective exits and stop-losses are never blocked for price or limits: they exist to cap a loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trademare.core.models import Market, Order, OrderSide, OrderType


@dataclass
class PreTradeContext:
    purpose: str  # entry | exit | stop
    opening: bool  # True for a new position (buy to open), False for closing one
    last_close: float | None = None
    position_qty: int | None = None  # shares TradeMare holds in this stock (closing orders)
    budget: float | None = None
    max_position_pct: float | None = None
    price_band_pct: float = 5.0
    funds: float | None = None  # free cash at the broker (live), None when unknown or paper
    committed: float = 0.0  # open buy orders not yet filled
    orders_today: int = 0
    max_orders_per_day: int | None = None
    in_universe: bool = True
    surveillance: str | None = None  # why the stock is on an ASM/GSM list, if it is
    sessions_behind: int = 0
    notes: list[str] = field(default_factory=list)


def check(order: Order, ctx: PreTradeContext) -> list[str]:
    """Problems with this order; empty when it may be sent."""
    problems: list[str] = []
    if order.quantity <= 0:
        problems.append("quantity must be positive")
    if not ctx.opening:
        held = ctx.position_qty or 0
        if order.quantity > held:
            problems.append(
                f"would sell {order.quantity} {order.symbol} but TradeMare holds only {held}; "
                "it never sells shares it did not buy"
            )
        return problems  # closing orders protect capital: nothing else may stop them

    price = order.limit_price if order.order_type is OrderType.LIMIT else ctx.last_close
    if not ctx.in_universe:
        problems.append(f"{order.symbol} is not in the trading universe")
    if ctx.surveillance:
        problems.append(f"{order.symbol} is under exchange surveillance ({ctx.surveillance})")
    if ctx.sessions_behind > 0:
        problems.append(f"prices are {ctx.sessions_behind} session(s) behind; wait for current data")
    if order.order_type is OrderType.LIMIT and ctx.last_close and price:
        move = price / ctx.last_close - 1
        if abs(move) * 100 > ctx.price_band_pct:
            problems.append(
                f"limit {price:,.2f} is {move:+.1%} from the last close {ctx.last_close:,.2f} "
                f"(allowed ±{ctx.price_band_pct:g}%)"
            )
    value = order.quantity * price if price else None
    if value is not None and ctx.budget and ctx.max_position_pct:
        cap = ctx.budget * ctx.max_position_pct / 100
        if value > cap * 1.05:  # 5% slack for whole-share rounding
            problems.append(f"order value {value:,.0f} is above the position cap {cap:,.0f}")
    if value is not None and ctx.funds is not None and value + ctx.committed > ctx.funds:
        problems.append(
            f"needs {value + ctx.committed:,.0f} (this order plus open buys) "
            f"but only {ctx.funds:,.0f} is free"
        )
    if ctx.max_orders_per_day is not None and ctx.orders_today >= ctx.max_orders_per_day:
        problems.append(f"already {ctx.orders_today} new orders today (limit {ctx.max_orders_per_day})")
    if order.side is OrderSide.SELL and ctx.opening and order.market is Market.IN:
        problems.append("short selling is not possible in Indian delivery equity")
    return problems
