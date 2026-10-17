"""Market regime: is the broad market supportive of longs (risk-on) or shorts?"""

from __future__ import annotations

from enum import StrEnum

import pandas as pd
from pydantic import BaseModel


class RegimeLabel(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class Regime(BaseModel):
    label: RegimeLabel
    score: float  # -1 (bearish) .. +1 (bullish)
    index_trend: float | None
    breadth: float | None  # share of universe above its SMA200
    reasons: list[str]


THRESHOLD = 0.3


def compute_regime(index_row: pd.Series | None, breadth: float | None) -> Regime:
    """``index_row`` is the benchmark's ``technical_features`` row at the scan date."""
    reasons: list[str] = []
    parts: list[tuple[float, float]] = []  # (weight, value)
    index_trend = None
    if index_row is not None and pd.notna(index_row.get("sma200")):
        above_200 = index_row["close"] > index_row["sma200"]
        golden = index_row["sma50"] > index_row["sma200"]
        index_trend = (1.0 if above_200 else -1.0) * 0.6 + (1.0 if golden else -1.0) * 0.4
        parts.append((0.6, index_trend))
        reasons.append(
            f"Index {'above' if above_200 else 'below'} its 200-day average "
            f"({index_row['close'] / index_row['sma200'] - 1:+.1%}), "
            f"SMA50 {'above' if golden else 'below'} SMA200"
        )
    if breadth is not None:
        parts.append((0.4, breadth * 2 - 1))
        reasons.append(f"{breadth:.0%} of stocks above their 200-day average")
    if not parts:
        return Regime(
            label=RegimeLabel.NEUTRAL, score=0.0, index_trend=None, breadth=None, reasons=["No data"]
        )
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    label = (
        RegimeLabel.RISK_ON
        if score >= THRESHOLD
        else RegimeLabel.RISK_OFF
        if score <= -THRESHOLD
        else RegimeLabel.NEUTRAL
    )
    return Regime(
        label=label, score=round(score, 3), index_trend=index_trend, breadth=breadth, reasons=reasons
    )
