"""Jinja filters for money, percentages and ratios."""

from __future__ import annotations

import math

from jinja2 import Undefined

from trademare.core.models import Market

DASH = "—"


def _missing(value: object) -> bool:
    """None, NaN, or a key absent from a template's data (Jinja Undefined) all render as a dash."""
    if value is None or isinstance(value, Undefined):
        return True
    return isinstance(value, float) and math.isnan(value)


def money(value: float | None, market: Market | str, compact: bool = True) -> str:
    if _missing(value):
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
    if _missing(value):
        return DASH
    return f"{value * 100:+.{digits}f}%" if signed else f"{value * 100:.{digits}f}%"


def num(value: float | None, digits: int = 1) -> str:
    return DASH if _missing(value) else f"{value:,.{digits}f}"


def tone(value: float | None) -> str:
    """CSS class for gains/losses."""
    if _missing(value) or value == 0:
        return ""
    return "up" if value > 0 else "down"
