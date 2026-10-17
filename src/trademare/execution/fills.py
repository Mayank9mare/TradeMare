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
