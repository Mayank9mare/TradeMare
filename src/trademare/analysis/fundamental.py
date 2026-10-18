"""Fundamental analyst: sector-relative quality, health, growth and value.

Each metric is turned into a percentile among sector peers (falling back to
the whole market when a sector is too small), mapped to [-1, 1], and averaged
into four factor scores. Hard red flags (losses, negative equity) cap the
result. Only data public on ``asof`` is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_PEERS = 5
STALE_DAYS = 270  # latest numbers older than this reduce confidence

# Banks/insurers/NBFCs: leverage and liquidity ratios are not comparable.
FINANCIAL_SECTORS = {"Financial Services", "Financials"}

# metric -> (factor, higher_is_better, display format)
METRICS: dict[str, tuple[str, bool, str]] = {
    "roe": ("quality", True, "pct"),
    "roa": ("quality", True, "pct"),
    "operating_margin": ("quality", True, "pct"),
    "net_margin": ("quality", True, "pct"),
    "debt_to_equity": ("health", False, "ratio"),
    "current_ratio": ("health", True, "ratio"),
    "interest_coverage": ("health", True, "times"),
    "revenue_growth": ("growth", True, "pct"),
    "earnings_growth": ("growth", True, "pct"),
    "earnings_yield": ("value", True, "pct"),
    "fcf_yield": ("value", True, "pct"),
    "book_yield": ("value", True, "pct"),
}
NON_FINANCIAL_ONLY = {"debt_to_equity", "current_ratio", "interest_coverage", "operating_margin"}
FACTOR_WEIGHTS = {"quality": 0.30, "health": 0.20, "growth": 0.25, "value": 0.25}
LABELS = {
    "roe": "ROE",
    "roa": "ROA",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "debt_to_equity": "Debt/equity",
    "current_ratio": "Current ratio",
    "interest_coverage": "Interest cover",
    "revenue_growth": "Revenue growth",
    "earnings_growth": "Earnings growth",
    "earnings_yield": "Earnings yield",
    "fcf_yield": "FCF yield",
    "book_yield": "Book/price",
}


@dataclass
class FundamentalScore:
    score: float
    confidence: float
    factors: dict[str, float | None]
    reasons: list[str] = field(default_factory=list)


def metric_table(ratios: dict[str, dict[str, float | None]]) -> pd.DataFrame:
    """Per-symbol metrics frame (rows = symbols) from ``key_ratios`` output."""
    df = pd.DataFrame.from_dict(ratios, orient="index").astype("float64")
    pe = df.get("pe")
    df["earnings_yield"] = 1 / pe if pe is not None else np.nan
    # Loss-makers have no P/E; give them a negative earnings yield so they rank last.
    losses = _earnings(df) <= 0
    df.loc[losses, "earnings_yield"] = -1.0
    ptb = df.get("price_to_book")
    df["book_yield"] = 1 / ptb if ptb is not None else np.nan
    return df


def _earnings(df: pd.DataFrame) -> pd.Series:
    """Recurring earnings where known, else reported net income."""
    reported = df["net_income_ttm"]
    return df["earnings_ttm"].fillna(reported) if "earnings_ttm" in df else reported


def _fmt(value: float, kind: str) -> str:
    return {"pct": f"{value:.0%}", "times": f"{value:.1f}x"}.get(kind, f"{value:.2f}")


def score_universe(
    ratios: dict[str, dict[str, float | None]],
    sectors: dict[str, str],
) -> dict[str, FundamentalScore]:
    """Score every symbol relative to its sector peers."""
    if not ratios:
        return {}
    df = metric_table(ratios)
    df["sector"] = pd.Series(sectors).reindex(df.index).fillna("Unknown")
    financial = df["sector"].isin(FINANCIAL_SECTORS)
    for m in NON_FINANCIAL_ONLY:
        df.loc[financial, m] = np.nan

    # Percentile rank within sector when the sector has enough peers with data,
    # otherwise within the whole market. Also keep "#k of n" for the reasons.
    ranks = pd.DataFrame(index=df.index, columns=list(METRICS), dtype="float64")
    places: dict[str, pd.Series] = {}
    for metric, (_, higher_better, _) in METRICS.items():
        col = df[metric]
        by_sector = col.groupby(df["sector"])
        use_sector = by_sector.transform("count") >= MIN_PEERS
        ranks[metric] = by_sector.rank(pct=True, ascending=higher_better).where(
            use_sector, col.rank(pct=True, ascending=higher_better)
        )
        position = by_sector.rank(ascending=not higher_better, method="min").where(
            use_sector, col.rank(ascending=not higher_better, method="min")
        )
        group_size = by_sector.transform("count").where(use_sector, col.count())
        group_name = df["sector"].where(use_sector, "market")
        places[metric] = pd.Series(
            [
                f"#{int(p)} of {int(n)} {'in the market' if g == 'market' else g + ' peers'}"
                if pd.notna(p)
                else ""
                for p, n, g in zip(position, group_size, group_name, strict=True)
            ],
            index=df.index,
        )
    scores = ranks * 2 - 1  # [0,1] percentile -> [-1, 1]

    out: dict[str, FundamentalScore] = {}
    for symbol in df.index:
        row, srow = df.loc[symbol], scores.loc[symbol]
        factors: dict[str, float | None] = {}
        for factor in FACTOR_WEIGHTS:
            vals = [srow[m] for m, (f, _, _) in METRICS.items() if f == factor and pd.notna(srow[m])]
            factors[factor] = float(np.mean(vals)) if vals else None
        available = {f: v for f, v in factors.items() if v is not None}
        if not available:
            continue
        total_w = sum(FACTOR_WEIGHTS[f] for f in available)
        score = sum(FACTOR_WEIGHTS[f] * v for f, v in available.items()) / total_w

        reasons = _reasons(row, srow, {m: places[m][symbol] for m in METRICS})
        earnings = row.get("earnings_ttm")
        if pd.isna(earnings):
            earnings = row.get("net_income_ttm")
        if pd.notna(earnings) and earnings <= 0:
            score = min(score, -0.2)
            reasons.insert(0, "Red flag: loss-making over the last twelve months")
        if pd.notna(row.get("total_equity")) and row["total_equity"] <= 0:
            if pd.notna(earnings) and earnings > 0:
                # Profitable firms with heavy buybacks (e.g. MCD, HD) often have negative equity.
                reasons.append("Negative book equity (likely buybacks) — ROE and D/E not meaningful")
            else:
                score = min(score, -0.3)
                reasons.insert(0, "Red flag: negative book equity with losses")

        coverage = sum(pd.notna(srow[m]) for m in METRICS) / sum(
            1 for m in METRICS if not (financial[symbol] and m in NON_FINANCIAL_ONLY)
        )
        confidence = 0.3 + 0.7 * coverage
        age = row.get("data_age_days")
        if pd.notna(age) and age > STALE_DAYS:
            confidence *= 0.6
            reasons.append(f"Stale data: latest results {int(age)} days old")
        out[symbol] = FundamentalScore(
            score=float(np.clip(score, -1, 1)),
            confidence=float(np.clip(confidence, 0, 1)),
            factors=factors,
            reasons=reasons,
        )
    return out


def _reasons(row: pd.Series, srow: pd.Series, places: dict[str, str], top: int = 2) -> list[str]:
    """The strongest and weakest metrics vs peers, in words."""
    ranked = srow.dropna().sort_values()
    reasons = []
    for metric in ranked.index[::-1][:top]:
        if ranked[metric] >= 0.5:
            reasons.append(f"{LABELS[metric]} {_fmt(row[metric], METRICS[metric][2])} ({places[metric]})")
    for metric in ranked.index[:top]:
        if ranked[metric] <= -0.5:
            reasons.append(f"{LABELS[metric]} {_fmt(row[metric], METRICS[metric][2])} ({places[metric]})")
    return reasons
