"""Entry, stop-loss and target prices from the configured methods."""

from __future__ import annotations

import math

import pandas as pd
from pydantic import BaseModel

from trademare.analysis.indicators import atr
from trademare.config.schema import StopLossConfig, StopLossMethod, TakeProfitConfig, TakeProfitMethod
from trademare.core.models import Direction, Market

TICK_SIZE = {Market.IN: 0.05, Market.US: 0.01}
MIN_STOP_DISTANCE = 0.005  # stops closer than 0.5% are noise


class Levels(BaseModel):
    entry: float
    stop: float
    target: float | None
    risk_per_share: float
    stop_method: str


def _round(price: float, tick: float, mode: str) -> float:
    fn = {"down": math.floor, "up": math.ceil, "nearest": round}[mode]
    return round(fn(price / tick) * tick, 6)


def compute_levels(
    market: Market,
    direction: Direction,
    bars: pd.DataFrame,
    stop_cfg: StopLossConfig,
    tp_cfg: TakeProfitConfig,
) -> Levels | None:
    """Levels for an entry at the last close. ``bars`` must end at the signal date.

    Returns None when the stop cannot be placed sensibly (e.g. too close).
    """
    if bars.empty:
        return None
    tick = TICK_SIZE[market]
    entry = _round(float(bars["close"].iloc[-1]), tick, "nearest")
    sign = 1 if direction is Direction.LONG else -1

    match stop_cfg.method:
        case StopLossMethod.ATR:
            atr_now = atr(bars, stop_cfg.atr_period).iloc[-1]
            if pd.isna(atr_now):
                return None
            distance = stop_cfg.atr_multiple * float(atr_now)
        case StopLossMethod.PERCENT:
            distance = entry * stop_cfg.percent / 100
        case StopLossMethod.SWING_LOW:
            window = bars.iloc[-stop_cfg.swing_lookback :]
            extreme = window["low"].min() if sign > 0 else window["high"].max()
            distance = sign * (entry - float(extreme))
        case StopLossMethod.FIXED_AMOUNT:
            distance = float(stop_cfg.fixed_amount)

    if distance < entry * MIN_STOP_DISTANCE:
        return None
    # Round stops away from entry so risk is never understated.
    stop = _round(entry - sign * distance, tick, "down" if sign > 0 else "up")
    if stop <= 0:
        return None
    risk = abs(entry - stop)

    match tp_cfg.method:
        case TakeProfitMethod.R_MULTIPLE:
            target = entry + sign * tp_cfg.value * risk
        case TakeProfitMethod.PERCENT:
            target = entry * (1 + sign * tp_cfg.value / 100)
        case TakeProfitMethod.NONE:
            target = None
    if target is not None:
        target = _round(target, tick, "nearest")
        if target <= 0:
            target = None

    return Levels(
        entry=entry, stop=stop, target=target, risk_per_share=round(risk, 6), stop_method=stop_cfg.method
    )
