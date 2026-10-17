"""Cross-sectional equity factors (strategy v2).

Each factor maps a symbol to an *oriented* value — higher is always expected
to be better — so factors can be ranked and averaged uniformly. See
docs/research/2026-10-17-strategy-v2-plan.md for definitions and sources.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from trademare.analysis.technical import score_features

if TYPE_CHECKING:
    from trademare.strategy.scanner import ScanContext


@dataclass(frozen=True)
class Factor:
    label: str
    needs_fundamentals: bool
    value: Callable[[dict, dict | None], float | None]  # (feature row, ratios) -> oriented value
    display: Callable[[float], str]


def _feature(key: str, sign: float = 1.0) -> Callable[[dict, dict | None], float | None]:
    def get(row: dict, _ratios: dict | None) -> float | None:
        v = row.get(key)
        return None if v is None or np.isnan(v) else sign * float(v)

    return get


def _ratio(key: str) -> Callable[[dict, dict | None], float | None]:
    def get(_row: dict, ratios: dict | None) -> float | None:
        v = None if ratios is None else ratios.get(key)
        return None if v is None else float(v)

    return get


def _earnings_yield(_row: dict, ratios: dict | None) -> float | None:
    if ratios is None or not ratios.get("market_cap") or ratios.get("net_income_ttm") is None:
        return None
    return ratios["net_income_ttm"] / ratios["market_cap"]


def _v1_technical(row: dict, _ratios: dict | None) -> float | None:
    s = score_features(row)
    return None if s is None else s.score


FACTORS: dict[str, Factor] = {
    "mom_12_1": Factor("12-1 month momentum", False, _feature("ret_12_1"), lambda v: f"{v:+.0%}"),
    "high_52w": Factor(
        "Price vs 52-week high", False, _feature("high_52w_ratio"), lambda v: f"{v:.0%} of high"
    ),
    "low_vol": Factor("Low volatility", False, _feature("vol_252", -1.0), lambda v: f"{-v:.0%} annual vol"),
    "gross_profitability": Factor(
        "Gross profit / assets", True, _ratio("gross_profitability"), lambda v: f"{v:.0%}"
    ),
    "earnings_yield": Factor("Earnings yield", True, _earnings_yield, lambda v: f"{v:.1%}"),
    "v1_technical": Factor("v1 technical score", False, _v1_technical, lambda v: f"{v:+.2f}"),
}


def factor_values(ctx: ScanContext, asof: date, names: list[str]) -> pd.DataFrame:
    """Oriented raw factor values for every symbol trading on ``asof``.

    Columns: one per factor, plus ``liquid``, ``sector`` and ``close``.
    """
    unknown = set(names) - set(FACTORS)
    if unknown:
        raise ValueError(f"unknown factors {sorted(unknown)} (known: {sorted(FACTORS)})")
    ts = pd.Timestamp(asof)
    needs_fund = any(FACTORS[n].needs_fundamentals for n in names)
    records = {}
    for symbol in ctx.symbols:
        row = ctx.feature_row(symbol, ts)
        if row is None:
            continue
        ratios = ctx.ratios(symbol, asof, float(row["close"])) if needs_fund else None
        rec: dict[str, object] = {n: FACTORS[n].value(row, ratios) for n in names}
        tv = row.get("traded_value_20")
        rec["liquid"] = tv is not None and not np.isnan(tv) and tv >= ctx.cfg.min_avg_traded_value
        rec["sector"] = ctx.sectors.get(symbol)
        rec["close"] = float(row["close"])
        rec["traded_value_126"] = row.get("traded_value_126")
        records[symbol] = rec
    df = pd.DataFrame.from_dict(records, orient="index")
    top_n = ctx.cfg.universe_top_n
    if top_n and not df.empty:
        # Point-in-time size filter: only names that were among the most traded *on this date*.
        size_rank = pd.to_numeric(df["traded_value_126"], errors="coerce").rank(ascending=False)
        df["liquid"] = df["liquid"].astype(bool) & (size_rank <= top_n)
    for n in names:
        if n in df:
            df[n] = pd.to_numeric(df[n], errors="coerce")
    return df


def combine_ranks(values: pd.DataFrame, names: list[str]) -> pd.Series:
    """Average cross-sectional percentile rank of the factors, mapped to [-1, 1].

    Symbols missing a factor are ranked on the factors they have.
    """
    ranks = pd.DataFrame({n: values[n].rank(pct=True) for n in names if n in values})
    if ranks.empty:
        return pd.Series(dtype="float64")
    return (ranks.mean(axis=1, skipna=True) * 2 - 1).dropna()
