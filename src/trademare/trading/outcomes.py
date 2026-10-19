"""One plain sentence for what happened to an approved order, used everywhere it is shown.

The dashboard, the daily digest and Telegram all describe an order the same way, so an order never
looks "waiting" on one page and "gone" on another. For a paper limit order that ended unfilled, the
reason is read from that session's prices (e.g. "lowest price 1,411.00 stayed above your limit").
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from trademare.core.models import Market

CURRENCY = {Market.IN: "₹", Market.US: "$"}


def money(value: float | None, market: Market | str) -> str:
    if value is None:
        return "?"
    return f"{CURRENCY.get(Market(market), '')}{value:,.2f}"


def _day(d: date | None) -> str:
    return d.strftime("%a %d %b") if d else "?"


def unfilled_reason(side: str, limit: float | None, bar: pd.Series | None, market: Market | str) -> str:
    """Why a limit order did not fill on the session of ``bar`` (a row with open/high/low/close)."""
    if bar is None:
        return "no prices for that session"
    if limit is None:
        return "it was not filled"
    yours = money(limit, market)
    if side == "BUY":
        return f"the lowest price, {money(float(bar['low']), market)}, stayed above your limit {yours}"
    return f"the highest price, {money(float(bar['high']), market)}, stayed below your limit {yours}"


def session_bar(store, market: Market, symbol: str, day: date | None) -> pd.Series | None:
    if day is None:
        return None
    bars = store.read_bars(market, symbol)
    ts = pd.Timestamp(day)
    if bars is None or ts not in bars.index:
        return None
    return bars.loc[ts]


def describe(order, store=None) -> tuple[str, str]:
    """(short status, sentence) for an entry or exit order row."""
    market = Market(order.market)
    verb = "Buy" if order.side == "BUY" else "Sell"
    what = f"{verb} {order.quantity} {order.symbol}"
    status = order.status
    if status == "OPEN":
        if order.order_type == "MARKET" or order.limit_price is None:
            return "waiting", f"{what} at the next open, at market."
        until = f" (valid until {_day(order.valid_until)})" if order.valid_until else ""
        bound = "or less" if order.side == "BUY" else "or more"
        return "waiting", f"{what} at {money(order.limit_price, market)} {bound} at the next open{until}."
    if status in ("FILLED", "PARTIALLY_FILLED"):
        price = money(order.average_fill_price, market)
        return "filled", f"{what}: filled at {price} on {_day(order.filled_on)}."
    if status == "EXPIRED":
        bar = session_bar(store, market, order.symbol, order.valid_until) if store is not None else None
        why = unfilled_reason(order.side, order.limit_price, bar, market) if store is not None else ""
        tail = f": {why}" if why else ""
        return "not filled", f"{what} expired on {_day(order.valid_until)} without filling{tail}."
    if status == "CANCELLED":
        return "cancelled", f"{what} was cancelled."
    if status == "REJECTED":
        return "rejected", f"{what} was rejected by the broker."
    return status.lower(), f"{what}: {status.lower()}."
