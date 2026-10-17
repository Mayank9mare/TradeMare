"""Evidence attached to each signal: the technical and fundamental facts behind it.

Captured when the signal is created (as of the scan date) and stored with it,
so the explanation shows what the engine saw, not today's numbers. Both
analyses are recorded for every signal, whatever the strategy mode.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from trademare.analysis.fundamental import FundamentalScore
from trademare.analysis.technical import score_features
from trademare.config.schema import MarketConfig
from trademare.strategy.scanner import ScanContext

# Technical metrics shown in the explanation: key -> (label, kind)
TECH_METRICS: dict[str, tuple[str, str]] = {
    "close": ("Close", "price"),
    "sma50": ("50-day average", "price"),
    "sma200": ("200-day average", "price"),
    "dist_sma50": ("Distance from 50-day avg", "pct"),
    "rsi": ("RSI (14)", "num"),
    "adx": ("ADX trend strength", "num"),
    "atr_pct": ("ATR (daily range)", "pct"),
    "ret_1m": ("1-month return", "pct"),
    "ret_3m": ("3-month return", "pct"),
    "ret_6m": ("6-month return", "pct"),
    "ret_12_1": ("12-1 month momentum", "pct"),
    "rel_6m": ("6-month return vs index", "pct"),
    "from_52w_high": ("From 52-week high", "pct"),
    "vol_ratio": ("Volume vs 20-day avg", "times"),
    "traded_value_20": ("Avg daily traded value", "money"),
}

FUND_METRICS: dict[str, tuple[str, str]] = {
    "market_cap": ("Market cap", "money"),
    "pe": ("P/E", "num"),
    "price_to_book": ("Price / book", "num"),
    "earnings_growth": ("Earnings growth (YoY)", "pct"),
    "revenue_growth": ("Revenue growth (YoY)", "pct"),
    "operating_margin": ("Operating margin", "pct"),
    "net_margin": ("Net margin", "pct"),
    "roe": ("Return on equity", "pct"),
    "debt_to_equity": ("Debt / equity", "num"),
    "interest_coverage": ("Interest cover", "num"),
    "fcf_yield": ("Free-cash-flow yield", "pct"),
    "gross_profitability": ("Gross profit / assets", "pct"),
    "data_age_days": ("Latest results age (days)", "int"),
}


def _clean(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _tech_metrics(row: dict) -> dict[str, float | None]:
    metrics = {k: _clean(row.get(k)) for k in TECH_METRICS}
    ratio = _clean(row.get("high_52w_ratio"))
    metrics["from_52w_high"] = None if ratio is None else ratio - 1  # 0.73 of the high -> -27%
    return metrics


def technical_evidence(ctx: ScanContext, symbol: str, asof: date) -> dict[str, Any] | None:
    row = ctx.feature_row(symbol, pd.Timestamp(asof))
    if row is None:
        return None
    t = score_features(row)
    return {
        "score": None if t is None else round(t.score, 4),
        "confidence": None if t is None else round(t.confidence, 4),
        "components": {} if t is None else {k: round(v, 4) for k, v in t.components.items()},
        "reasons": [] if t is None else t.reasons,
        "metrics": _tech_metrics(row),
    }


def fundamental_evidence(
    ctx: ScanContext, symbol: str, asof: date, scores: dict[str, FundamentalScore] | None
) -> dict[str, Any] | None:
    row = ctx.feature_row(symbol, pd.Timestamp(asof))
    ratios = ctx.ratios(symbol, asof, float(row["close"])) if row is not None else None
    score = (scores or {}).get(symbol)
    if ratios is None and score is None:
        return None
    return {
        "score": None if score is None else round(score.score, 4),
        "confidence": None if score is None else round(score.confidence, 4),
        "factors": {}
        if score is None
        else {k: (None if v is None else round(v, 4)) for k, v in score.factors.items()},
        "reasons": [] if score is None else score.reasons,
        "metrics": {k: _clean((ratios or {}).get(k)) for k in FUND_METRICS},
        "source": ctx.cfg.fundamentals_source,
    }


def risk_evidence(
    entry: float, stop: float | None, target: float | None, quantity: int, capital: float, cfg: MarketConfig
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "position_value": round(entry * quantity, 2),
        "position_pct": round(entry * quantity / capital, 4) if capital else None,
        "stop_method": cfg.stop_loss.method.value,
        "risk_per_trade_pct": cfg.risk_per_trade_pct,
    }
    if stop is not None:
        per_share = abs(entry - stop)
        out.update(
            risk_per_share=round(per_share, 4),
            stop_distance=round(per_share / entry, 4) if entry else None,
            risk_amount=round(per_share * quantity, 2),
            risk_pct=round(per_share * quantity / capital, 4) if capital else None,
        )
        if target is not None and per_share:
            out["reward_risk"] = round(abs(target - entry) / per_share, 2)
    return out


def build_evidence(
    ctx: ScanContext,
    symbol: str,
    asof: date,
    fundamental_scores: dict[str, FundamentalScore] | None,
    risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "asof": asof.isoformat(),
        "technical": technical_evidence(ctx, symbol, asof),
        "fundamental": fundamental_evidence(ctx, symbol, asof, fundamental_scores),
        "risk": risk,
    }
