"""Per-candle features for intraday strategies (5-minute candles, IST-naive timestamps = candle start).

Everything is causal: a value on a candle uses that candle's close and earlier data only. Daily
levels (pivots, CPR) come from the previous session's high, low and close, taken from the candles.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from trademare.analysis import indicators as ind

OPEN = time(9, 15)


def _session_levels(c: pd.DataFrame) -> pd.DataFrame:
    """Previous-session pivots and CPR for each candle, and today's CPR width rank (last 60 days)."""
    day = c.index.normalize()
    daily = c.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = daily.shift(1)
    h, lo, cl = prev["high"], prev["low"], prev["close"]
    p = (h + lo + cl) / 3
    bc = (h + lo) / 2
    tc = 2 * p - bc
    lv = pd.DataFrame(
        {
            "P": p,
            "R1": 2 * p - lo,
            "S1": 2 * p - h,
            "R2": p + (h - lo),
            "S2": p - (h - lo),
            "cpr_low": np.minimum(bc, tc),
            "cpr_high": np.maximum(bc, tc),
        }
    )
    width = (tc - bc).abs() / p
    # Percentile of today's width among the previous 60 sessions (needs at least 20 of them).
    lv["cpr_rank"] = [
        np.nan if i < 21 else float((width.iloc[max(0, i - 60) : i] < width.iloc[i]).mean())
        for i in range(len(width))
    ]
    return lv.reindex(day).set_axis(c.index)


def _vwap(c: pd.DataFrame) -> pd.DataFrame:
    day = c.index.normalize()
    tp = (c["high"] + c["low"] + c["close"]) / 3
    vol = c["volume"].clip(lower=0)
    cv = vol.groupby(day).cumsum().replace(0, np.nan)
    vwap = (tp * vol).groupby(day).cumsum() / cv
    var = (tp * tp * vol).groupby(day).cumsum() / cv - vwap * vwap
    sd = np.sqrt(var.clip(lower=0))
    return pd.DataFrame({"vwap": vwap, "vwap_up2": vwap + 2 * sd, "vwap_dn2": vwap - 2 * sd})


def _hourly_rsi(c: pd.DataFrame, minutes: int) -> pd.Series:
    """RSI(14) of hourly closes (hours from 09:15), known on a candle once its hour has closed."""
    day = c.index.normalize()
    since_open = (c.index - day - pd.Timedelta(hours=9, minutes=15)).total_seconds() // 60
    bucket = np.asarray(since_open // 60, dtype=int)
    groups = pd.Series(0, index=c.index).groupby([day, bucket])
    ordinal = groups.ngroup().to_numpy()  # hours numbered in time order
    hourly_close = c["close"].groupby([day, bucket]).last()
    rsi = ind.rsi(hourly_close.reset_index(drop=True)).to_numpy()
    end = np.asarray(since_open + minutes)
    # An hour is complete when the clock says so (on the hour, or at the 15:30 close), never just
    # because it is the last candle we have: live, that candle's hour may still be running.
    closes_hour = (end % 60 == 0) | (end >= 375)
    latest = np.where(closes_hour, ordinal, ordinal - 1)  # the latest completed hour
    out = np.where(latest >= 0, rsi[np.clip(latest, 0, None)], np.nan)
    return pd.Series(out, index=c.index)


def build(c: pd.DataFrame, minutes: int = 5) -> pd.DataFrame:
    """All features for every candle of ``c`` (open/high/low/close/volume)."""
    f = pd.DataFrame(index=c.index)
    f[["open", "high", "low", "close", "volume"]] = c[["open", "high", "low", "close", "volume"]]
    day = c.index.normalize()
    f["day"] = day
    f["start"] = c.index.time
    f["green"] = c["close"] > c["open"]
    f["red"] = c["close"] < c["open"]
    f["sma200"] = ind.sma(c["close"], 200)
    f["rsi"] = ind.rsi(c["close"])
    st = ind.supertrend(c, 7, 3.0)
    f["st_line"], f["st_up"] = st["line"], st["up"]
    ev5, ev10 = ind.ema(c["volume"], 5), ind.ema(c["volume"], 10)
    f["vol_osc"] = (ev5 - ev10) / ev10.replace(0, np.nan)
    f = f.join(_vwap(c)).join(_session_levels(c))
    f["rsi_hour"] = _hourly_rsi(c, minutes)
    first3 = c.groupby(day).head(3)
    or_hi = first3.groupby(first3.index.normalize())["high"].max()
    or_lo = first3.groupby(first3.index.normalize())["low"].min()
    f["or_high"], f["or_low"] = or_hi.reindex(day).to_numpy(), or_lo.reindex(day).to_numpy()
    f["bar_of_day"] = c.groupby(day).cumcount()
    return f
