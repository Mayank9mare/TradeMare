"""Daily-bar fill rules shared by the backtester and the paper broker.

Conventions (conservative where a daily bar is ambiguous):
- Limit orders fill at the open if it is through the limit, else at the
  limit if the day's range reaches it.
- Stop orders trigger at the open if it gaps through (filled at the open),
  else at the stop price if the range reaches it.
- Market orders fill at the open.
- Slippage is applied against us on market and stop fills, not on limits.
"""

from __future__ import annotations

from dataclasses import dataclass

from trademare.core.models import OrderSide


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


def slipped(price: float, side: OrderSide, bps: float) -> float:
    adj = bps / 10_000
    return price * (1 + adj) if side is OrderSide.BUY else price * (1 - adj)


def limit_fill(side: OrderSide, limit: float, bar: Bar) -> float | None:
    if side is OrderSide.BUY:
        return bar.open if bar.open <= limit else (limit if bar.low <= limit else None)
    return bar.open if bar.open >= limit else (limit if bar.high >= limit else None)


def stop_fill(
    side: OrderSide, trigger: float, bar: Bar, open_price: float | None = None
) -> tuple[float, bool] | None:
    """Fill price and whether it gapped. ``side`` is the stop order's side (SELL protects a long).

    ``open_price`` overrides the bar's open for positions entered intraday at a
    limit, which could not have been stopped at that day's open.
    """
    o = bar.open if open_price is None else open_price
    if side is OrderSide.SELL:
        if o <= trigger:
            return o, True
        return (trigger, False) if bar.low <= trigger else None
    if o >= trigger:
        return o, True
    return (trigger, False) if bar.high >= trigger else None


def stop_limit_price(side: OrderSide, trigger: float, buffer_pct: float) -> float:
    """Limit price of a stop that brokers only accept as a limit order (priced through the trigger)."""
    buffer = buffer_pct / 100
    return trigger * (1 - buffer) if side is OrderSide.SELL else trigger * (1 + buffer)


def gapped_past_limit(side: OrderSide, trigger: float, price: float, buffer_pct: float) -> bool:
    """True when ``price`` is beyond the stop's limit price, so the resting limit order cannot fill.

    A SELL stop's limit sits below the trigger; an open below that limit leaves the order
    unfilled ("stuck") - the position must then be exited with a market order.
    """
    limit = stop_limit_price(side, trigger, buffer_pct)
    return price < limit if side is OrderSide.SELL else price > limit


def target_fill(
    side: OrderSide, target: float, bar: Bar, open_price: float | None = None
) -> tuple[float, bool] | None:
    """Profit-taking limit. ``side`` is the exit side (SELL for a long)."""
    o = bar.open if open_price is None else open_price
    if side is OrderSide.SELL:
        if o >= target:
            return o, True
        return (target, False) if bar.high >= target else None
    if o <= target:
        return o, True
    return (target, False) if bar.low <= target else None
