"""Performance statistics for equity curves and trade lists."""

from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np
import pandas as pd

from trademare.backtest.engine import BacktestResult, Trade

TRADING_DAYS = 252


def curve_metrics(equity: pd.Series) -> dict[str, float | None]:
    """Return/risk statistics for a daily equity series."""
    equity = equity.dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {}
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None
    vol = rets.std() * math.sqrt(TRADING_DAYS) if len(rets) > 1 else None
    downside = rets[rets < 0].std() * math.sqrt(TRADING_DAYS) if (rets < 0).sum() > 1 else None
    mean_annual = rets.mean() * TRADING_DAYS
    peak = equity.cummax()
    drawdown = equity / peak - 1
    max_dd = float(drawdown.min())
    # Longest stretch below a previous peak, in calendar days.
    underwater = drawdown < 0
    longest = 0
    start = None
    for ts, under in underwater.items():
        if under and start is None:
            start = ts
        elif not under and start is not None:
            longest = max(longest, (ts - start).days)
            start = None
    if start is not None:
        longest = max(longest, (equity.index[-1] - start).days)
    return {
        "total_return": float(total),
        "cagr": None if cagr is None else float(cagr),
        "volatility": None if vol is None else float(vol),
        "sharpe": float(mean_annual / vol) if vol else None,
        "sortino": float(mean_annual / downside) if downside else None,
        "max_drawdown": max_dd,
        "calmar": float(cagr / -max_dd) if cagr is not None and max_dd < 0 else None,
        "longest_drawdown_days": float(longest),
    }


def trade_metrics(trades: list[Trade]) -> dict[str, float | None]:
    if not trades:
        return {"trades": 0.0}
    pnl = np.array([t.pnl for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "trades": float(len(trades)),
        "win_rate": float(len(wins) / len(trades)),
        "avg_win_pct": float(np.mean([t.return_pct for t in trades if t.pnl > 0])) if len(wins) else None,
        "avg_loss_pct": float(np.mean([t.return_pct for t in trades if t.pnl <= 0])) if len(losses) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "avg_r": float(np.mean([t.r_multiple for t in trades])),
        "expectancy": float(pnl.mean()),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        "total_costs": float(sum(t.costs for t in trades)),
    }


def summarize(result: BacktestResult, out_of_sample_fraction: float = 0.4) -> dict:
    """Full report: overall, benchmark, per-year and in/out-of-sample splits."""
    eq, bench = result.equity, result.benchmark
    summary: dict = {
        "strategy": curve_metrics(eq) | trade_metrics(result.trades),
        "benchmark": curve_metrics(bench) if bench is not None else {},
        "exposure_avg": float(result.exposure.mean()) if len(result.exposure) else None,
        "time_in_market": float((result.exposure > 0).mean()) if len(result.exposure) else None,
    }
    summary["kill_switch"] = {
        "trips": [{"date": d.isoformat(), "reason": r} for d, r in result.kill_switch_trips],
        "halted_sessions": result.halted_sessions,
    }
    exits = pd.Series([t.exit_reason for t in result.trades]).value_counts()
    summary["exit_reasons"] = {k: int(v) for k, v in exits.items()}

    years = {}
    for year in sorted({ts.year for ts in eq.index}):
        mask = eq.index.year == year
        # Include the prior close so each year's return starts from the previous year's end.
        prev = eq[eq.index.year < year]
        seg = pd.concat([prev.iloc[-1:], eq[mask]]) if len(prev) else eq[mask]
        row = {"strategy": curve_metrics(seg).get("total_return")}
        if bench is not None:
            bprev = bench[bench.index.year < year]
            bseg = (
                pd.concat([bprev.iloc[-1:], bench[bench.index.year == year]]) if len(bprev) else bench[mask]
            )
            row["benchmark"] = curve_metrics(bseg).get("total_return")
        row["trades"] = sum(1 for t in result.trades if t.exit_date.year == year)
        years[str(year)] = row
    summary["by_year"] = years

    if 0 < out_of_sample_fraction < 1 and len(eq) > 20:
        split = eq.index[int(len(eq) * (1 - out_of_sample_fraction))]
        summary["split_date"] = split.date().isoformat()
        cut = split.date()
        segments = {
            # Trades that straddle the split belong to neither side.
            "in_sample": (eq.index <= split, [t for t in result.trades if t.exit_date <= cut]),
            "out_of_sample": (eq.index >= split, [t for t in result.trades if t.entry_date >= cut]),
        }
        for label, (mask, seg_trades) in segments.items():
            summary[label] = {
                "strategy": curve_metrics(eq[mask]) | trade_metrics(seg_trades),
                "benchmark": curve_metrics(bench[mask]) if bench is not None else {},
            }
    return summary


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
