"""Signal research: do the analyst scores predict forward returns?

Independent of trading rules (entries, stops, sizing), this measures the
raw predictive power of each score with:

- Information coefficient (IC): Spearman rank correlation between a score
  and the next ``horizon``-session return across the universe, per date.
  Mean IC around 0.02-0.05 with a consistent sign is typical of a usable
  equity signal; ~0 means no edge.
- Quintile spread: forward return of the top-scored fifth minus the bottom
  fifth, relative to the universe average.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from trademare.strategy.scanner import ScanContext

SCORES = ("composite", "technical", "fundamental")


def forward_returns(ctx: ScanContext, ts: pd.Timestamp, horizon: int) -> dict[str, float]:
    out = {}
    for symbol, bars in ctx.bars.items():
        i = bars.index.searchsorted(ts)
        if i >= len(bars) or bars.index[i] != ts or i + horizon >= len(bars):
            continue
        out[symbol] = float(bars["close"].iloc[i + horizon] / bars["close"].iloc[i] - 1)
    return out


def signal_study(ctx: ScanContext, start: date, end: date, horizon: int = 20, step: int = 5) -> dict:
    sessions = ctx.sessions(start, end)
    rows = []
    for ts in sessions[: max(0, len(sessions) - horizon) : step]:
        scan = ctx.evaluate(ts.date())
        fwd = forward_returns(ctx, ts, horizon)
        for s in scan.scores:
            if not s.liquid or s.symbol not in fwd:
                continue
            tech, fund = s.analysts.get("technical"), s.analysts.get("fundamental")
            rows.append(
                {
                    "date": ts,
                    "symbol": s.symbol,
                    "composite": s.composite,
                    "technical": tech.score if tech else np.nan,
                    "fundamental": fund.score if fund else np.nan,
                    "fwd": fwd[s.symbol],
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        return {"dates": 0}
    # Excess return vs the cross-sectional average removes market direction.
    df["excess"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")

    result: dict = {"dates": int(df["date"].nunique()), "horizon": horizon, "observations": len(df)}
    for score in SCORES:
        sub = df.dropna(subset=[score])
        if sub.empty:
            result[score] = {
                "mean_ic": None, "ic_t_stat": None, "ic_positive_share": None, "quintile_excess": {},
                "top_minus_bottom": None, "ic_by_year": {}, "coverage": 0.0,
            }  # fmt: skip
            continue
        ics = (
            sub.groupby("date")
            .apply(
                # Spearman = Pearson on ranks (avoids a scipy dependency).
                lambda g, sc=score: g[sc].rank().corr(g["fwd"].rank()) if len(g) >= 20 else np.nan,
                include_groups=False,
            )
            .dropna()
        )
        sub = sub.assign(q=sub.groupby("date")[score].transform(_quintile))
        quintiles = sub.groupby("q")["excess"].mean()
        by_year = ics.groupby(ics.index.year).mean()
        result[score] = {
            "mean_ic": float(ics.mean()) if len(ics) else None,
            "ic_t_stat": float(ics.mean() / ics.std() * math.sqrt(len(ics)))
            if len(ics) > 2 and ics.std() > 0
            else None,
            "ic_positive_share": float((ics > 0).mean()) if len(ics) else None,
            "quintile_excess": {int(k): float(v) for k, v in quintiles.items()},
            "top_minus_bottom": float(quintiles.iloc[-1] - quintiles.iloc[0])
            if len(quintiles) >= 2
            else None,
            "ic_by_year": {int(y): float(v) for y, v in by_year.items()},
            "coverage": float(len(sub) / len(df)),
        }
    return result


def _t_stat(ics: pd.Series) -> float | None:
    """t-statistic of the mean IC (how many standard errors it is from zero)."""
    if len(ics) <= 2 or ics.std() == 0:
        return None
    return float(ics.mean() / ics.std() * math.sqrt(len(ics)))


def _quintile(s: pd.Series) -> pd.Series:
    """1 (lowest score) .. 5 (highest), robust to ties and small groups."""
    ranks = s.rank(method="first", pct=True)
    return np.ceil(ranks * 5).clip(1, 5)


def format_study(market: str, study: dict) -> str:
    if not study.get("dates"):
        return f"[{market}] not enough data"
    lines = [
        f"[{market}] signal study: {study['dates']} dates, {study['observations']:,} observations, "
        f"{study['horizon']}-session forward returns",
        f"  {'score':<12}{'mean IC':>9}{'t-stat':>8}{'IC>0':>7}{'Q5-Q1':>8}"
        "   quintile excess (Q1..Q5)   IC by year",
    ]
    for score in SCORES:
        r = study[score]
        q = " ".join(f"{r['quintile_excess'].get(i, 0) * 100:+5.2f}" for i in range(1, 6))
        years = " ".join(f"{y}:{v:+.3f}" for y, v in r["ic_by_year"].items())
        lines.append(
            f"  {score:<12}{_f(r['mean_ic'], '+.3f'):>9}{_f(r['ic_t_stat'], '+.1f'):>8}"
            f"{_f(r['ic_positive_share'], '.0%'):>7}{_f(r['top_minus_bottom'], '+.2%'):>8}   {q}   {years}"
        )
    return "\n".join(lines)


def _f(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)
