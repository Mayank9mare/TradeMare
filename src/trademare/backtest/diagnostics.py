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

from trademare.analysis.factors import factor_values
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
    return ic_report(pd.DataFrame(rows), list(SCORES), horizon)


def factor_study(
    ctx: ScanContext, start: date, end: date, names: list[str], horizon: int = 63, step: int = 21
) -> dict:
    """IC of each (oriented) factor vs forward returns over liquid symbols."""
    sessions = ctx.sessions(start, end)
    frames = []
    for ts in sessions[: max(0, len(sessions) - horizon) : step]:
        values = factor_values(ctx, ts.date(), names)
        fwd = pd.Series(forward_returns(ctx, ts, horizon), dtype="float64")
        values = values[values["liquid"].astype(bool)].join(fwd.rename("fwd"), how="inner")
        frames.append(values[[*names, "fwd"]].assign(date=ts))
    df = pd.concat(frames) if frames else pd.DataFrame()
    return ic_report(df, names, horizon)


def ic_report(df: pd.DataFrame, columns: list[str], horizon: int) -> dict:
    """Per-date rank IC, quintile spreads and yearly IC for each column vs ``fwd``."""
    if df.empty:
        return {"dates": 0}
    # Excess return vs the cross-sectional average removes market direction.
    df = df.assign(excess=df["fwd"] - df.groupby("date")["fwd"].transform("mean"))
    result: dict = {"dates": int(df["date"].nunique()), "horizon": horizon, "observations": len(df)}
    result["columns"] = columns
    for col in columns:
        sub = df.dropna(subset=[col])
        if sub.empty:
            result[col] = {
                "mean_ic": None, "ic_t_stat": None, "ic_positive_share": None, "quintile_excess": {},
                "top_minus_bottom": None, "ic_by_year": {}, "coverage": 0.0,
            }  # fmt: skip
            continue
        # Spearman = Pearson on ranks (avoids a scipy dependency).
        ics = (
            sub.groupby("date")[[col, "fwd"]]
            .apply(lambda g, c=col: g[c].rank().corr(g["fwd"].rank()) if len(g) >= 20 else np.nan)
            .astype("float64")
            .dropna()
        )
        sub = sub.assign(q=sub.groupby("date")[col].transform(_quintile))
        quintiles = sub.groupby("q")["excess"].mean()
        by_year = ics.groupby(ics.index.year).mean()
        result[col] = {
            "mean_ic": float(ics.mean()) if len(ics) else None,
            "ic_t_stat": _t_stat(ics),
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


def format_study(market: str, study: dict, title: str = "signal study") -> str:
    if not study.get("dates"):
        return f"[{market}] not enough data"
    lines = [
        f"[{market}] {title}: {study['dates']} dates, {study['observations']:,} observations, "
        f"{study['horizon']}-session forward returns",
        f"  {'name':<20}{'mean IC':>9}{'t-stat':>8}{'IC>0':>7}{'Q5-Q1':>8}{'cover':>7}"
        "   quintile excess % (Q1..Q5)        IC by year",
    ]
    for score in study["columns"]:
        r = study[score]
        q = " ".join(f"{r['quintile_excess'].get(i, 0) * 100:+5.2f}" for i in range(1, 6))
        years = " ".join(f"{y}:{v:+.3f}" for y, v in r["ic_by_year"].items())
        lines.append(
            f"  {score:<20}{_f(r['mean_ic'], '+.3f'):>9}{_f(r['ic_t_stat'], '+.1f'):>8}"
            f"{_f(r['ic_positive_share'], '.0%'):>7}{_f(r['top_minus_bottom'], '+.2%'):>8}"
            f"{_f(r['coverage'], '.0%'):>7}   {q}   {years}"
        )
    return "\n".join(lines)


def _f(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)
