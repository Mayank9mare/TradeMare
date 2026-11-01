"""The intraday strategies of docs/plans/2026-11-01-intraday-plan.md as exact rules.

Each strategy maps the features of one stock (``features.build``) to a frame aligned with the
candles:

- ``side``: +1 a buy signal, -1 a sell (short) signal, 0 nothing, on that candle's close;
- ``trigger``: the stop-entry price (buy above / sell below);
- ``stop`` and ``target`` (NaN = no target);
- ``exit_long`` / ``exit_short``: the strategy's own exit condition holds on that close.

Time windows, order validity, one trade per stock per day and the 15:15 square-off are applied by
the simulator, the same way in the study and in live paper trading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TOUCH = 0.0015  # "within 0.15%" of a level
FIB = (0.382, 0.5, 0.618, 0.786)


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    book: str
    summary: str
    rule: Callable[[pd.DataFrame], pd.DataFrame] = field(repr=False)


def _frame(f: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=f.index)
    out["side"] = 0
    out["trigger"] = np.nan
    out["stop"] = np.nan
    out["target"] = np.nan
    out["exit_long"] = False
    out["exit_short"] = False
    return out


def _set(out: pd.DataFrame, mask: pd.Series, side: int, trigger, stop, target=np.nan) -> None:
    mask = mask.fillna(False).astype(bool) & (out["side"] == 0)
    out.loc[mask, "side"] = side
    for col, val in (("trigger", trigger), ("stop", stop), ("target", target)):
        out.loc[mask, col] = val[mask] if isinstance(val, pd.Series) else val


def _recent(cond: pd.Series, f: pd.DataFrame, n: int) -> pd.Series:
    """``cond`` held on this candle or one of the previous n-1 candles of the same day."""
    return cond.astype(float).groupby(f["day"]).transform(lambda s: s.rolling(n, min_periods=1).max()) == 1


def _r_target(trigger: pd.Series, stop: pd.Series, r: float) -> pd.Series:
    return trigger + r * (trigger - stop)


# ---------------------------------------------------------------- 2.1 moving average + Fibonacci
def _ma_fib(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    hh = f["high"].rolling(60, min_periods=60).max()
    ll = f["low"].rolling(60, min_periods=60).min()
    win_h = np.lib.stride_tricks.sliding_window_view(np.r_[np.full(59, -np.inf), f["high"].to_numpy()], 60)
    win_l = np.lib.stride_tricks.sliding_window_view(np.r_[np.full(59, np.inf), f["low"].to_numpy()], 60)
    age_h = pd.Series(59 - win_h.argmax(axis=1), index=f.index)
    age_l = pd.Series(59 - win_l.argmin(axis=1), index=f.index)
    big = hh / ll - 1 >= 0.01
    span = hh - ll
    sma = f["sma200"]

    # Long: rising average, low before high, pullback into 38.2-61.8% of the rise, green candle.
    lvl = {r: hh - r * span for r in FIB}
    stop = pd.Series(np.nan, index=f.index)
    for touched, below in ((0.382, 0.5), (0.5, 0.618), (0.618, 0.786)):  # deepest touched wins
        stop = stop.mask(f["low"] <= lvl[touched], lvl[below])
    long = (f["close"] > sma) & (sma > sma.shift(10)) & (age_l > age_h) & (age_h >= 1) & big & f["green"]
    long &= (f["low"] <= lvl[0.382]) & (f["low"] > lvl[0.786])  # into the zone, not past 78.6%
    trig = f["high"]
    _set(out, long, 1, trig, stop, np.fmax(hh, _r_target(trig, stop, 2)))

    # Short: falling average, high before low, bounce into 38.2-61.8% of the fall, red candle.
    up = {r: ll + r * span for r in FIB}
    sstop = pd.Series(np.nan, index=f.index)
    for touched, above in ((0.382, 0.5), (0.5, 0.618), (0.618, 0.786)):
        sstop = sstop.mask(f["high"] >= up[touched], up[above])
    short = (f["close"] < sma) & (sma < sma.shift(10)) & (age_h > age_l) & (age_l >= 1) & big & f["red"]
    short &= (f["high"] >= up[0.382]) & (f["high"] < up[0.786])
    strig = f["low"]
    _set(out, short, -1, strig, sstop, np.fmin(ll, _r_target(strig, sstop, 2)))
    return out


# ---------------------------------------------------------------- 2.2 Supertrend + pivots
def _supertrend_pivot(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    up = f["st_up"].astype(bool)
    has = f["st_line"].notna()
    _set(out, (f["close"] > f["R1"]) & up & has & f["green"], 1, f["close"], f["st_line"])
    _set(out, (f["close"] < f["S1"]) & ~up & has & f["red"], -1, f["close"], f["st_line"])
    out["exit_long"] = has & ~up
    out["exit_short"] = has & up
    return out


# ---------------------------------------------------------------- 2.3 VWAP bands
def _vwap_bands(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    same_day = f["day"] == f["day"].shift()
    was_low = same_day & f["red"].shift(fill_value=False) & (f["close"].shift() < f["vwap_dn2"].shift())
    was_high = same_day & f["green"].shift(fill_value=False) & (f["close"].shift() > f["vwap_up2"].shift())
    _set(out, was_low & f["green"] & (f["vwap"] > f["high"]), 1, f["high"], f["low"], f["vwap"])
    _set(out, was_high & f["red"] & (f["vwap"] < f["low"]), -1, f["low"], f["high"], f["vwap"])
    return out


# ---------------------------------------------------------------- 2.4 RSI + volume oscillator
def _rsi_volume(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    oversold = _recent((f["rsi"] <= 30) & (f["vol_osc"] <= -0.30), f, 3)
    overbought = _recent((f["rsi"] >= 70) & (f["vol_osc"] >= 0.30), f, 3)
    low10 = f["low"].rolling(10, min_periods=10).min()
    high10 = f["high"].rolling(10, min_periods=10).max()
    _set(out, (f["close"] > f["sma200"]) & oversold & f["green"], 1, f["high"], low10)
    _set(out, (f["close"] < f["sma200"]) & overbought & f["red"], -1, f["low"], high10)
    out["exit_long"] = f["rsi"] >= 70
    out["exit_short"] = f["rsi"] <= 30
    return out


# ---------------------------------------------------------------- 2.5 pivot pullback
def _next_level(f: pd.DataFrame, price: pd.Series, min_gap: pd.Series, above: bool) -> pd.Series:
    """The nearest previous-day pivot level beyond ``price`` by at least ``min_gap``."""
    levels = f[["S2", "S1", "P", "R1", "R2"]].to_numpy()
    p = price.to_numpy()[:, None]
    gap = min_gap.to_numpy()[:, None]
    if above:
        cand = np.where(levels >= p + gap, levels, np.inf)
        best = cand.min(axis=1)
        return pd.Series(np.where(np.isinf(best), np.nan, best), index=f.index)
    cand = np.where(levels <= p - gap, levels, -np.inf)
    best = cand.max(axis=1)
    return pd.Series(np.where(np.isinf(best), np.nan, best), index=f.index)


def _pullback_after_cross(f: pd.DataFrame, level: pd.Series, up: bool) -> pd.Series:
    """Candles 2-12 after a close crossed ``level`` (same day) whose pullback retested it."""
    c = f["close"]
    cross = (c > level) & (c.shift() <= level) if up else (c < level) & (c.shift() >= level)
    cross &= f["day"] == f["day"].shift()
    group = cross.astype(int).groupby(f["day"]).cumsum()
    key = [f["day"], group]
    age = f.groupby(key).cumcount()
    if up:
        touch = (f["low"] <= level * (1 + TOUCH)).astype(int)
        broke = (c < level * (1 - TOUCH)).astype(int)
    else:
        touch = (f["high"] >= level * (1 - TOUCH)).astype(int)
        broke = (c > level * (1 + TOUCH)).astype(int)
    after = age >= 1
    touched = (touch * after).groupby(key).cumsum() > 0
    broken = (broke * after).groupby(key).cumsum() > 0
    return (group > 0) & age.between(2, 12) & touched & ~broken


def _pivot_pullback(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    long = pd.Series(False, index=f.index)
    for name in ("S1", "P", "R1", "R2"):
        long |= _pullback_after_cross(f, f[name], True)
    short = pd.Series(False, index=f.index)
    for name in ("S2", "S1", "P", "R1"):
        short |= _pullback_after_cross(f, f[name], False)
    risk_l = f["high"] - f["low"]
    target_l = _next_level(f, f["high"], risk_l, True)
    _set(out, long & f["green"] & target_l.notna(), 1, f["high"], f["low"], target_l)
    target_s = _next_level(f, f["low"], risk_l, False)
    _set(out, short & f["red"] & target_s.notna(), -1, f["low"], f["high"], target_s)
    return out


# ---------------------------------------------------------------- 2.6 double RSI
def _double_rsi(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    r = f["rsi"]
    _set(out, _recent(r <= 30, f, 3) & (f["rsi_hour"] > 50) & f["green"], 1, f["high"], f["low"])
    _set(out, _recent(r >= 70, f, 3) & (f["rsi_hour"] < 50) & f["red"], -1, f["low"], f["high"])
    out["exit_long"] = (r < 70) & (r.shift() >= 70)
    out["exit_short"] = (r > 30) & (r.shift() <= 30)
    return out


# ---------------------------------------------------------------- 2.7 CPR
def _cpr(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    c, prev = f["close"], f["close"].shift()
    same_day = f["day"] == f["day"].shift()
    narrow, wide = f["cpr_rank"] <= 0.30, f["cpr_rank"] >= 0.70
    levels = ("cpr_low", "cpr_high", "R1", "S1")
    up_break = pd.Series(False, index=f.index)
    down_break = pd.Series(False, index=f.index)
    for name in levels:
        lv = f[name]
        up_break |= (c > lv) & (prev <= lv)
        down_break |= (c < lv) & (prev >= lv)
    near = {name: (f["low"] - f[name]).abs() <= f[name] * TOUCH for name in ("S1", "cpr_low")}
    near_r = {name: (f["high"] - f[name]).abs() <= f[name] * TOUCH for name in ("R1", "cpr_high")}
    long = same_day & ((narrow & up_break) | (wide & (near["S1"] | near["cpr_low"]))) & f["green"]
    short = same_day & ((narrow & down_break) | (wide & (near_r["R1"] | near_r["cpr_high"]))) & f["red"]
    tl = _next_level_cpr(f, f["high"], True)
    ts = _next_level_cpr(f, f["low"], False)
    _set(out, long & tl.notna(), 1, f["high"], f["low"], tl)
    _set(out, short & ts.notna(), -1, f["low"], f["high"], ts)
    return out


def _next_level_cpr(f: pd.DataFrame, price: pd.Series, above: bool) -> pd.Series:
    levels = f[["S2", "S1", "cpr_low", "P", "cpr_high", "R1", "R2"]].to_numpy()
    p = price.to_numpy()[:, None]
    if above:
        best = np.where(levels > p, levels, np.inf).min(axis=1)
    else:
        best = np.where(levels < p, levels, -np.inf).max(axis=1)
    return pd.Series(np.where(np.isinf(best), np.nan, best), index=f.index)


# ---------------------------------------------------------------- opening range breakout
def _orb(f: pd.DataFrame) -> pd.DataFrame:
    out = _frame(f)
    after = f["bar_of_day"] >= 3
    _set(
        out,
        after & (f["close"] > f["or_high"]),
        1,
        f["high"],
        f["or_low"],
        _r_target(f["high"], f["or_low"], 2),
    )
    _set(
        out,
        after & (f["close"] < f["or_low"]),
        -1,
        f["low"],
        f["or_high"],
        _r_target(f["low"], f["or_high"], 2),
    )
    return out


STRATEGIES: dict[str, Strategy] = {
    s.id: s
    for s in [
        Strategy("ma_fib", "Moving average + Fibonacci", "2.1",
                 "With the 200-candle average rising, buy a green candle after a pullback into the 38-62% "
                 "retracement; stop at the next Fibonacci level; target the swing high or 2R.", _ma_fib),
        Strategy("supertrend_pivot", "Supertrend + pivots", "2.2",
                 "Price above yesterday's R1 with Supertrend(7, 3) up: buy above a green candle's close; "
                 "exit on a close below the Supertrend.", _supertrend_pivot),
        Strategy("vwap_bands", "VWAP band reversal", "2.3",
                 "A red candle closes below VWAP minus 2 standard deviations, then a green candle: buy "
                 "above its high, target VWAP.", _vwap_bands),
        Strategy("rsi_volume", "RSI + volume oscillator", "2.4",
                 "In an uptrend, RSI at or below 30 with the volume oscillator at or below -30%, then a "
                 "green candle: buy above its high; exit when RSI reaches 70.", _rsi_volume),
        Strategy("pivot_pullback", "Pivot pullback", "2.5",
                 "After a close above a pivot level, a pullback that retests it, then a green candle: buy "
                 "above its high, target the next pivot at least 1R away.", _pivot_pullback),
        Strategy("double_rsi", "Double RSI", "2.6",
                 "5-minute RSI at or below 30 while the hourly RSI is above 50, then a green candle: buy "
                 "above its high; exit when RSI falls back below 70.", _double_rsi),
        Strategy("cpr", "Central pivot range", "2.7",
                 "Narrow CPR day: buy breakouts through the CPR, R1 or S1; wide CPR day: buy bounces off "
                 "S1 or the CPR. Target the next level.", _cpr),
        Strategy("orb", "Opening range breakout", "baseline",
                 "After 09:30, a close above the first 15 minutes' high: buy above that candle; stop at the "
                 "opening range low; target 2R.", _orb),
    ]
}  # fmt: skip


def evaluate(strategy: Strategy, features: pd.DataFrame) -> pd.DataFrame:
    """The strategy's frame with insane signals removed (stop on the wrong side of the trigger)."""
    out = strategy.rule(features)
    side = out["side"]
    risk = (out["trigger"] - out["stop"]) * side
    bad = (side != 0) & ~(risk > 0)
    bad |= (side != 0) & out["target"].notna() & ((out["target"] - out["trigger"]) * side <= 0)
    out.loc[bad, "side"] = 0
    out["exit_long"] = out["exit_long"].fillna(False).astype(bool)
    out["exit_short"] = out["exit_short"].fillna(False).astype(bool)
    return out
