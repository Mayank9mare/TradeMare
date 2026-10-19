# ruff: noqa: RUF001  (times sign in user-facing text)
"""Jinja filters for money, percentages and ratios."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
    shown = round(value * 100, digits) + 0.0  # + 0.0 turns -0.0 into 0.0, so tiny losses don't print "-0.0%"
    return f"{shown:+.{digits}f}%" if signed else f"{shown:.{digits}f}%"


def num(value: float | None, digits: int = 1) -> str:
    return DASH if _missing(value) else f"{value:,.{digits}f}"


def tone(value: float | None) -> str:
    """CSS class for gains/losses. Values that display as zero (|x| < 0.05%) stay neutral."""
    if _missing(value) or abs(value) < 5e-4:
        return ""
    return "up" if value > 0 else "down"


def vs(value: float | None, other: float | None, higher_better: bool = True) -> str:
    """CSS class comparing a result with its benchmark: "up" if better, "down" if worse."""
    if _missing(value) or _missing(other) or abs(value - other) < 5e-4:
        return ""
    return "up" if (value > other) == higher_better else "down"


def metric(value: float | None, kind: str, market: Market | str) -> str:
    """Format an evidence metric by its kind (see strategy/evidence.py)."""
    if _missing(value):
        return DASH
    match kind:
        case "price":
            return money(value, market, compact=False)
        case "money":
            return money(value, market)
        case "pct":
            return pct(value, 1, signed=True)
        case "times":
            return f"{value:.1f}x"
        case "int":
            return f"{value:,.0f}"
        case _:
            return f"{value:,.2f}"


MARKET_TZ = {Market.IN: ("Asia/Kolkata", "IST"), Market.US: ("America/New_York", "New York")}


def local_time(value: datetime | None, market: Market | str) -> str:
    """A UTC timestamp in the market's own time, e.g. 'Mon 19 Oct, 15:30 IST'."""
    if value is None or isinstance(value, Undefined):
        return DASH
    tz, label = MARKET_TZ[Market(market)]
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return f"{aware.astimezone(ZoneInfo(tz)):%a %d %b, %H:%M} {label}"


_RUN_NAMES = {
    "mom_12_1": "momentum",
    "earnings_yield": "earnings yield",
    "gross_profitability": "gross profitability",
    "high_52w": "52-week high",
    "low_vol": "low volatility",
    "vol_target": "vol targeting",
}
_RUN_SKIP = {"budget", "max_open_positions", "atr_multiple", "trailing", "method", "time_stop_days"}


def run_label(overrides: list[str] | None) -> str:
    """Short description of a backtest from its overrides, e.g. 'mom_12_1 + earnings_yield · vol_target'."""
    parts: list[str] = []
    for item in overrides or []:
        key, _, value = item.partition("=")
        leaf = key.rsplit(".", 1)[-1]
        if leaf == "factors":
            names = [_RUN_NAMES.get(v.strip(), v.strip()) for v in value.strip("[] ").split(",") if v.strip()]
            parts.insert(0, " + ".join(names))
        elif leaf == "exclude_high_vol_pct" and value not in ("0", "0.0", "null"):
            parts.append(f"skip top {float(value):.0%} volatile")
        elif leaf == "strategy_mode" or leaf in _RUN_SKIP:
            continue
        elif value.lower() == "true":
            parts.append(_RUN_NAMES.get(leaf, leaf.replace("_", " ")))
        elif value.lower() not in ("false", "null"):
            parts.append(f"{leaf} {value}")
    return " · ".join(parts) or "settings as configured"


UNIVERSE_NAMES = {"nifty500": "Nifty 500", "nifty200": "Nifty 200", "nifty50": "Nifty 50", "sp500": "S&P 500"}
ANALYST_NAMES = {
    "technical": "Price trend",
    "fundamental": "Company quality",
    "factor": "Factor ranks",
    "sentiment": "News sentiment",
    "forensic": "Accounting checks",
}


def universe_name(key: str | None) -> str:
    return UNIVERSE_NAMES.get(str(key), str(key))


def factor_name(key: str) -> str:
    """Display name for a factor or analyst key, e.g. mom_12_1 -> 12-1 month momentum."""
    from trademare.analysis.factors import FACTORS

    if key in FACTORS:
        return FACTORS[key].label
    return ANALYST_NAMES.get(key, key.replace("_", " ").capitalize())


def stop_rule(cfg) -> str:
    """Plain description of a market's stop-loss and target settings."""
    sl, tp = cfg.stop_loss, cfg.take_profit
    method = str(getattr(sl.method, "value", sl.method))
    if method == "atr":
        stop = f"stop-loss {sl.atr_multiple:g}× the average daily range (ATR) below entry"
    elif method == "percent":
        stop = f"stop-loss {sl.percent:g}% below entry"
    else:
        stop = f"stop-loss by {method}"
    if sl.trailing:
        stop += ", trailing up as the price rises"
    tp_method = str(getattr(tp.method, "value", tp.method))
    if tp_method == "none":
        target = "no profit target (exits come from the strategy)"
    elif tp_method == "r_multiple":
        target = f"profit target at {tp.value:g}× the amount risked"
    else:
        target = f"profit target {tp.value:g}% above entry"
    return f"{stop}; {target}"
