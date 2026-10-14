"""Jinja filters for money, percentages and ratios."""

from __future__ import annotations

from trademare.core.models import Market

DASH = "—"


def money(value: float | None, market: Market | str, compact: bool = True) -> str:
    if value is None:
        return DASH
    market = Market(market)
    sign = "-" if value < 0 else ""
    v = abs(value)
    if market is Market.IN:
        if compact and v >= 1e7:
            return f"{sign}₹{v / 1e7:,.0f} Cr" if v >= 1e9 else f"{sign}₹{v / 1e7:,.1f} Cr"
        if compact and v >= 1e5:
            return f"{sign}₹{v / 1e5:,.1f} L"
        return f"{sign}₹{v:,.2f}"
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if compact and v >= size:
            return f"{sign}${v / size:,.1f}{suffix}"
    return f"{sign}${v:,.2f}"


def pct(value: float | None, digits: int = 1, signed: bool = False) -> str:
    if value is None:
        return DASH
    return f"{value * 100:+.{digits}f}%" if signed else f"{value * 100:.{digits}f}%"


def num(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else f"{value:,.{digits}f}"


def tone(value: float | None) -> str:
    """CSS class for gains/losses."""
    if value is None or value == 0:
        return ""
    return "up" if value > 0 else "down"
