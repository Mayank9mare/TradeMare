"""Vectorised technical indicators over a full bar history.

All functions are causal: the value on day t uses only bars up to and
including t, so slicing the output at any date is safe for backtests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), n)
    loss = _wilder(-delta.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    flat = (loss == 0) & (gain == 0)
    return out.where(loss != 0, 100.0).where(~flat, 50.0).where(gain.notna())


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift()
    return pd.concat(
        [bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(bars), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def adx(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = _wilder(true_range(bars), n)
    plus_di = 100 * _wilder(plus_dm, n) / tr
    minus_di = 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx, n)


def pct_change(series: pd.Series, n: int) -> pd.Series:
    return series / series.shift(n) - 1


def supertrend(bars: pd.DataFrame, n: int = 10, multiple: float = 3.0) -> pd.DataFrame:
    """Supertrend line and direction (``up`` True when the line is below price), as TradingView.

    Bands sit ``multiple`` ATRs either side of the bar midpoint; the lower band only rises and the
    upper band only falls while the trend holds, and the trend flips when a close crosses the band.
    """
    mid = ((bars["high"] + bars["low"]) / 2).to_numpy()
    a = atr(bars, n).to_numpy()
    close = bars["close"].to_numpy()
    upper_basic, lower_basic = mid + multiple * a, mid - multiple * a
    m = len(close)
    upper, lower = np.full(m, np.nan), np.full(m, np.nan)
    up = np.zeros(m, dtype=bool)
    line = np.full(m, np.nan)
    started = False
    for i in range(m):
        if np.isnan(a[i]):
            continue
        if not started:
            upper[i], lower[i], up[i], started = upper_basic[i], lower_basic[i], True, True
        else:
            lower[i] = (
                lower_basic[i]
                if lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]
                else lower[i - 1]
            )
            upper[i] = (
                upper_basic[i]
                if upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]
                else upper[i - 1]
            )
            if up[i - 1]:
                up[i] = not close[i] < lower[i]
            else:
                up[i] = close[i] > upper[i]
        line[i] = lower[i] if up[i] else upper[i]
    out = pd.DataFrame({"line": line, "up": up}, index=bars.index)
    out.loc[np.isnan(line), "up"] = False
    return out


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands (population standard deviation, as charting tools) and band width."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": (upper - lower) / mid})


def _midpoint(bars: pd.DataFrame, n: int) -> pd.Series:
    return (bars["high"].rolling(n, min_periods=n).max() + bars["low"].rolling(n, min_periods=n).min()) / 2


def ichimoku(bars: pd.DataFrame, conv: int = 9, base: int = 26, span_b: int = 52) -> pd.DataFrame:
    """Ichimoku lines as drawn on a chart on each day: the cloud spans are the values computed
    ``base`` sessions earlier (they are plotted that far ahead), so every column is causal."""
    conversion, baseline = _midpoint(bars, conv), _midpoint(bars, base)
    span_a = ((conversion + baseline) / 2).shift(base)
    span_b_line = _midpoint(bars, span_b).shift(base)
    return pd.DataFrame(
        {
            "conversion": conversion,
            "base": baseline,
            "span_a": span_a,
            "span_b": span_b_line,
            "cloud_top": pd.concat([span_a, span_b_line], axis=1).max(axis=1, skipna=False),
        }
    )


def williams_r(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    """Williams %R in [-100, 0]: 0 = close at the n-day high, -100 = at the n-day low."""
    hh = bars["high"].rolling(n, min_periods=n).max()
    ll = bars["low"].rolling(n, min_periods=n).min()
    return -100 * (hh - bars["close"]) / (hh - ll).replace(0, np.nan)
