"""Walk-forward selection: does choosing a variant on trailing results work out of sample?

All variants are backtested once over the full span. At each calendar-year start the variant with
the best Sharpe over the trailing ``lookback_years`` (at least ``min_history_days`` sessions) is
held for that year, and the yearly segments are chained into one equity curve. Each choice uses
only data before the year it is applied to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from trademare.backtest.metrics import TRADING_DAYS, curve_metrics


@dataclass(frozen=True)
class YearChoice:
    year: int
    variant: str
    trailing_sharpe: float
    year_return: float
    benchmark_return: float | None


@dataclass(frozen=True)
class WalkForward:
    equity: pd.Series
    benchmark: pd.Series | None
    choices: list[YearChoice]

    def metrics(self) -> dict:
        bench = self.benchmark
        return {
            "strategy": curve_metrics(self.equity),
            "benchmark": curve_metrics(bench) if bench is not None else {},
        }


def _sharpe(returns: pd.Series) -> float | None:
    returns = returns.dropna()
    vol = returns.std()
    if len(returns) < 2 or not vol or math.isnan(vol):
        return None
    return float(returns.mean() / vol * math.sqrt(TRADING_DAYS))


def walk_forward(
    curves: dict[str, pd.Series],
    benchmark: pd.Series | None = None,
    lookback_years: int = 3,
    min_history_days: int = 252,
) -> WalkForward:
    if not curves:
        raise ValueError("no variant curves")
    returns = pd.DataFrame({name: eq.pct_change() for name, eq in curves.items()}).sort_index()
    years = sorted({ts.year for ts in returns.index})
    pieces: list[pd.Series] = []
    choices: list[YearChoice] = []
    bench_returns = benchmark.reindex(returns.index).ffill().pct_change() if benchmark is not None else None
    for year in years:
        start = pd.Timestamp(year=year, month=1, day=1)
        past = returns[
            (returns.index < start) & (returns.index >= start - pd.DateOffset(years=lookback_years))
        ]
        if len(past.dropna(how="all")) < min_history_days:
            continue
        scored = {name: _sharpe(past[name]) for name in returns.columns}
        scored = {k: v for k, v in scored.items() if v is not None}
        if not scored:
            continue
        best = max(scored, key=lambda k: scored[k])  # dict order breaks ties: earlier variant wins
        segment = returns.loc[returns.index.year == year, best].fillna(0.0)
        pieces.append(segment)
        bench_year = None
        if bench_returns is not None:
            bench_year = float((1 + bench_returns[bench_returns.index.year == year].fillna(0.0)).prod() - 1)
        choices.append(YearChoice(year, best, scored[best], float((1 + segment).prod() - 1), bench_year))
    if not pieces:
        raise ValueError("not enough history for any walk-forward year")
    chained = pd.concat(pieces)
    equity = (1 + chained).cumprod()
    bench = None
    if bench_returns is not None:  # the index over exactly the same sessions, compounded the same way
        bench = (1 + bench_returns.reindex(chained.index).fillna(0.0)).cumprod()
    return WalkForward(equity=equity, benchmark=bench, choices=choices)


def format_walk_forward(wf: WalkForward) -> str:
    lines = ["year  variant  trailing Sharpe   year return   index return"]
    for c in wf.choices:
        b = "" if c.benchmark_return is None else f"{c.benchmark_return:+.1%}"
        lines.append(
            f"{c.year}  {c.variant:<7}  {c.trailing_sharpe:>15.2f}   {c.year_return:>+11.1%}   {b:>12}"
        )
    m = wf.metrics()
    for label in ("strategy", "benchmark"):
        x = m[label]
        if x:
            lines.append(
                f"{label:<9}: CAGR {x['cagr']:+.1%}  Sharpe {x['sharpe']:.2f}  max DD {x['max_drawdown']:.1%}"
            )
    return "\n".join(lines)
