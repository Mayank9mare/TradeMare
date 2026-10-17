"""Technical analyst: trend, momentum, entry setups and volume, scored in [-1, 1].

``technical_features`` builds every indicator for a symbol's whole history in
one vectorised pass; ``score_features`` turns one row (one date) into a score
with human-readable reasons. Positive scores favour longs, negative favour
shorts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from trademare.analysis import indicators as ind

MIN_BARS = 60  # below this there is too little history to say anything
BREAKOUT_LOOKBACK = 55
BREAKOUT_VOLUME = 1.5  # x the 20-day average volume

WEIGHTS = {"trend": 0.35, "momentum": 0.30, "setup": 0.25, "volume": 0.10}


def technical_features(bars: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    close, volume = bars["close"], bars["volume"]
    f = pd.DataFrame(index=bars.index)
    f["close"] = close
    for n in (20, 50, 200):
        f[f"sma{n}"] = ind.sma(close, n)
    f["sma200_slope"] = f["sma200"] / f["sma200"].shift(20) - 1
    f["rsi"] = ind.rsi(close)
    f["atr"] = ind.atr(bars)
    f["atr_pct"] = f["atr"] / close
    f["adx"] = ind.adx(bars)
    f["macd_hist"] = ind.macd(close)["hist"]
    f["ret_1m"] = ind.pct_change(close, 21)
    f["ret_3m"] = ind.pct_change(close, 63)
    f["ret_6m"] = ind.pct_change(close, 126)
    if benchmark is not None and not benchmark.empty:
        bench = benchmark["close"].reindex(close.index).ffill()
        f["rel_6m"] = f["ret_6m"] - ind.pct_change(bench, 126)
    else:
        f["rel_6m"] = np.nan
    # Breakout levels exclude today so a breakout is "today's close beyond the prior range".
    f["high_prior"] = bars["high"].shift(1).rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).max()
    f["low_prior"] = bars["low"].shift(1).rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).min()
    f["vol_ratio"] = volume / volume.shift(1).rolling(20, min_periods=20).mean()
    direction = np.sign(close.diff())
    up_vol = (volume * (direction > 0)).rolling(20).sum()
    down_vol = (volume * (direction < 0)).rolling(20).sum()
    f["updown_vol"] = up_vol / down_vol.replace(0, np.nan)
    f["traded_value_20"] = (close * volume).rolling(20).mean()
    f["traded_value_126"] = (close * volume).rolling(126, min_periods=60).mean()
    # Longer-horizon factor inputs (see analysis/factors.py).
    f["ret_12_1"] = close.shift(21) / close.shift(252) - 1  # 12-month return skipping the last month
    f["high_52w_ratio"] = close / bars["high"].rolling(252, min_periods=252).max()
    f["vol_252"] = np.log(close).diff().rolling(252, min_periods=200).std() * np.sqrt(252)
    f["dist_sma20"] = close / f["sma20"] - 1
    f["dist_sma50"] = close / f["sma50"] - 1
    f["bars"] = np.arange(1, len(f) + 1)
    return f


@dataclass
class TechnicalScore:
    score: float
    confidence: float
    components: dict[str, float]
    reasons: list[str] = field(default_factory=list)


def _ok(x: object) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_features(row: pd.Series) -> TechnicalScore | None:
    """Score a single row of ``technical_features`` output."""
    if row["bars"] < MIN_BARS or not _ok(row["sma50"]):
        return None
    reasons: list[str] = []
    close = row["close"]

    # Trend: price vs SMA50, SMA50 vs SMA200, slope of SMA200.
    trend_parts = [1.0 if close > row["sma50"] else -1.0]
    if _ok(row["sma200"]):
        trend_parts.append(1.0 if row["sma50"] > row["sma200"] else -1.0)
        trend_parts.append(_clip(row["sma200_slope"] / 0.03) if _ok(row["sma200_slope"]) else 0.0)
        if close > row["sma50"] > row["sma200"]:
            reasons.append("Uptrend: price > SMA50 > SMA200")
        elif close < row["sma50"] < row["sma200"]:
            reasons.append("Downtrend: price < SMA50 < SMA200")
        else:
            reasons.append("Mixed trend: price and moving averages not aligned")
    trend = sum(trend_parts) / len(trend_parts)

    # Momentum: 6-month return relative to the index, plus absolute 3-month return.
    rel = row["rel_6m"] if _ok(row["rel_6m"]) else row["ret_6m"]
    momentum_parts = []
    if _ok(rel):
        momentum_parts.append(math.tanh(rel / 0.15))
        label = "vs index" if _ok(row["rel_6m"]) else "absolute"
        if abs(rel) >= 0.1:
            reasons.append(f"6-month return {rel:+.0%} {label}")
    if _ok(row["ret_3m"]):
        momentum_parts.append(math.tanh(row["ret_3m"] / 0.10))
    momentum = sum(momentum_parts) / len(momentum_parts) if momentum_parts else 0.0

    # Setups: breakouts/breakdowns, pullbacks/bounces, and overextension.
    setup = 0.0
    vol_ratio = row["vol_ratio"] if _ok(row["vol_ratio"]) else 0.0
    rsi = row["rsi"] if _ok(row["rsi"]) else 50.0
    if _ok(row["high_prior"]) and close > row["high_prior"] and vol_ratio >= BREAKOUT_VOLUME:
        setup += 1.0
        reasons.append(f"Breakout above {BREAKOUT_LOOKBACK}-day high on {vol_ratio:.1f}x volume")
    elif _ok(row["low_prior"]) and close < row["low_prior"] and vol_ratio >= BREAKOUT_VOLUME:
        setup -= 1.0
        reasons.append(f"Breakdown below {BREAKOUT_LOOKBACK}-day low on {vol_ratio:.1f}x volume")
    if trend > 0.3 and _ok(row["dist_sma20"]) and abs(row["dist_sma20"]) <= 0.02 and 40 <= rsi <= 55:
        setup += 0.7
        reasons.append(f"Pullback to SMA20 in uptrend (RSI {rsi:.0f})")
    elif trend < -0.3 and _ok(row["dist_sma20"]) and abs(row["dist_sma20"]) <= 0.02 and 45 <= rsi <= 60:
        setup -= 0.7
        reasons.append(f"Bounce to SMA20 in downtrend (RSI {rsi:.0f})")
    if row["dist_sma50"] > 0.15 or rsi > 75:
        setup -= 0.5
        reasons.append(f"Extended: {row['dist_sma50']:+.0%} from SMA50, RSI {rsi:.0f} — poor long entry")
    elif row["dist_sma50"] < -0.15 or rsi < 25:
        setup += 0.5
        reasons.append(f"Oversold: {row['dist_sma50']:+.0%} from SMA50, RSI {rsi:.0f} — poor short entry")
    setup = _clip(setup)

    # Volume: accumulation vs distribution over 20 days.
    volume = 0.0
    if _ok(row["updown_vol"]) and row["updown_vol"] > 0:
        volume = _clip(math.log(row["updown_vol"]) / math.log(2))
        if abs(volume) >= 0.5:
            kind = "Accumulation" if volume > 0 else "Distribution"
            reasons.append(f"{kind}: up/down volume ratio {row['updown_vol']:.2f}")

    components = {"trend": trend, "momentum": momentum, "setup": setup, "volume": volume}
    score = _clip(sum(WEIGHTS[k] * v for k, v in components.items()))

    adx = row["adx"] if _ok(row["adx"]) else 15.0
    confidence = 0.5 + 0.3 * _clip((adx - 15) / 20, 0, 1) + (0.2 if _ok(row["sma200"]) else 0.0)
    if adx >= 25:
        reasons.append(f"Strong trend (ADX {adx:.0f})")
    return TechnicalScore(score=score, confidence=confidence, components=components, reasons=reasons)
