"""Sanity checks for daily OHLCV bars."""

from __future__ import annotations

import logging

import pandas as pd

from trademare.core.interfaces import BAR_COLUMNS

log = logging.getLogger(__name__)


def clean_bars(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """Return bars with a sorted unique DatetimeIndex and only valid rows.

    Drops rows with missing or non-positive prices, negative volume, or
    impossible ranges (high below low). Fixes highs/lows that do not bracket
    open/close, which some feeds produce after adjustments.
    """
    if df.empty:
        return empty_bars()
    out = df.reindex(columns=list(BAR_COLUMNS)).astype("float64")
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index)).tz_localize(None).normalize()
    out.index.name = "date"
    out.columns.name = None
    out = out[~out.index.duplicated(keep="last")].sort_index()

    prices = out[["open", "high", "low", "close"]]
    bad = prices.isna().any(axis=1) | (prices <= 0).any(axis=1) | (out["high"] < out["low"])
    bad |= out["volume"].fillna(0) < 0
    if bad.any():
        log.warning("%s: dropping %d invalid bars", symbol, int(bad.sum()))
        out = out[~bad]

    out["high"] = out[["open", "high", "close"]].max(axis=1)
    out["low"] = out[["open", "low", "close"]].min(axis=1)
    out["volume"] = out["volume"].fillna(0)
    return out


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS})
    df.index = pd.DatetimeIndex([], name="date")
    return df
