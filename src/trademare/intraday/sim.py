"""Trade simulation on 5-minute candles, shared by the study and live paper trading.

Rules (docs/plans/2026-11-01-intraday-plan.md):

- a signal on a candle's close (closing between ``entry_start`` and ``entry_end``) places a stop
  entry at ``trigger``, valid for the next ``stop_valid_candles`` candles of the same day; a candle
  that gaps through the trigger fills at its open;
- one order or position per stock per strategy at a time, and at most one trade per day;
- on the entry candle only the stop is checked (a fill and a stop in one candle is a loss);
- afterwards, on each candle: the strategy's exit from the previous close (at this open), the stop
  (at the open if it gapped past), the target (likewise), and the square-off at the ``squareoff``
  candle's open; after the close the strategy's own exit condition is checked for the next open;
- stop and open exits pay ``slippage_bps``; costs are Zerodha intraday charges on a
  ``cost_notional`` position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from trademare.intraday.costs import round_trip_pct


@dataclass
class Trade:
    strategy: str
    symbol: str
    side: int  # +1 long, -1 short
    day: str
    signal_time: str
    entry_time: str
    entry: float
    stop: float
    target: float | None
    exit_time: str
    exit: float
    reason: str  # stop | target | rule | squareoff | end (data ended)
    candles: int
    ret: float  # net of costs, fraction of the entry value
    r: float  # gross gain / initial risk


@dataclass(frozen=True)
class Rules:
    entry_start: time = time(9, 20)
    entry_end: time = time(14, 45)
    squareoff: time = time(15, 15)
    stop_valid_candles: int = 3
    slippage_bps: float = 2.0
    cost_notional: float = 50_000.0
    minutes: int = 5

    @classmethod
    def from_config(cls, cfg) -> Rules:
        return cls(
            cfg.entry_start, cfg.entry_end, cfg.squareoff, cfg.stop_valid_candles,
            cfg.slippage_bps, cfg.cost_notional, cfg.minutes,
        )  # fmt: skip


DEFAULT_RULES = Rules()


def _close_time(ts: pd.Timestamp, minutes: int) -> time:
    return (ts + pd.Timedelta(minutes=minutes)).time()


def simulate(
    strategy: str, symbol: str, features: pd.DataFrame, frame: pd.DataFrame, rules: Rules | None = None
) -> list[Trade]:
    rules = rules or DEFAULT_RULES
    idx = features.index
    o, h, lo, c = (features[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    day = features["day"].to_numpy()
    side_arr = frame["side"].to_numpy()
    trig, stop0, tgt = (frame[k].to_numpy(dtype=float) for k in ("trigger", "stop", "target"))
    ex_long, ex_short = frame["exit_long"].to_numpy(), frame["exit_short"].to_numpy()
    starts = np.array([t.time() for t in idx])
    closes = np.array([_close_time(t, rules.minutes) for t in idx])
    slip = rules.slippage_bps / 10_000
    trades: list[Trade] = []
    traded_day = None
    n = len(idx)
    i = 0
    while i < n:
        side = side_arr[i]
        if side == 0 or day[i] == traded_day or not (rules.entry_start <= closes[i] <= rules.entry_end):
            i += 1
            continue
        # A stop entry for the next few candles of the same day.
        j, filled = i + 1, None
        last = min(n - 1, i + rules.stop_valid_candles)
        while j <= last and day[j] == day[i] and starts[j] < rules.squareoff:
            if side > 0 and h[j] >= trig[i]:
                filled = max(o[j], trig[i])
                break
            if side < 0 and lo[j] <= trig[i]:
                filled = min(o[j], trig[i])
                break
            j += 1
        if filled is None:
            i += 1  # the order lapsed; later signals today may still trade
            continue
        stop, target = stop0[i], tgt[i]
        if (filled - stop) * side <= 0:  # gapped through the whole risk
            i = j
            continue
        risk = abs(filled - stop)
        exit_price, reason, k = None, "end", j
        if (side > 0 and lo[j] <= stop) or (side < 0 and h[j] >= stop):
            exit_price, reason = stop * (1 - side * slip), "stop"
        else:
            pending = bool(ex_long[j] if side > 0 else ex_short[j])
            k = j + 1
            while k < n and day[k] == day[j]:
                if pending:
                    exit_price, reason = o[k] * (1 - side * slip), "rule"
                    break
                if starts[k] >= rules.squareoff:
                    exit_price, reason = o[k] * (1 - side * slip), "squareoff"
                    break
                if (side > 0 and o[k] <= stop) or (side < 0 and o[k] >= stop):
                    exit_price, reason = o[k] * (1 - side * slip), "stop"
                    break
                if (side > 0 and lo[k] <= stop) or (side < 0 and h[k] >= stop):
                    exit_price, reason = stop * (1 - side * slip), "stop"
                    break
                if not math.isnan(target) and (
                    (side > 0 and h[k] >= target) or (side < 0 and lo[k] <= target)
                ):
                    gapped = (side > 0 and o[k] >= target) or (side < 0 and o[k] <= target)
                    exit_price, reason = (o[k] if gapped else target), "target"
                    break
                pending = bool(ex_long[k] if side > 0 else ex_short[k])
                k += 1
            else:
                k -= 1  # the day's candles ran out: close at the last one
                exit_price, reason = c[k], "end"
        gross = side * (exit_price / filled - 1)
        ret = gross - round_trip_pct(filled, exit_price, side > 0, rules.cost_notional)
        trades.append(
            Trade(
                strategy=strategy,
                symbol=symbol,
                side=int(side),
                day=str(pd.Timestamp(day[i]).date()),
                signal_time=str(idx[i]),
                entry_time=str(idx[j]),
                entry=round(float(filled), 2),
                stop=round(float(stop), 2),
                target=None if math.isnan(target) else round(float(target), 2),
                exit_time=str(idx[k]),
                exit=round(float(exit_price), 2),
                reason=reason,
                candles=int(k - j),
                ret=round(float(ret), 6),
                r=round(float(side * (exit_price - filled) / risk), 3),
            )
        )
        traded_day = day[i]
        i = k + 1
    return trades
