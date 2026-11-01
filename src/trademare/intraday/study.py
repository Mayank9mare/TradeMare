"""Track record of each intraday strategy on the cached candles, and the evidence bar."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

from trademare.intraday import features as feat
from trademare.intraday.sim import Rules, Trade, simulate
from trademare.intraday.store import CandleStore
from trademare.intraday.strategies import STRATEGIES, evaluate

SPLIT = "2022-01-01"
PERIODS = ("2018-2021", "2022-now")
MIN_TRADES = 300
MIN_PF_LATER = 1.1


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    rets = np.array([t.ret for t in trades])
    gains, losses = rets[rets > 0].sum(), -rets[rets < 0].sum()
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {
        "trades": len(trades),
        "win_rate": round(float((rets > 0).mean()), 4),
        "avg_ret": round(float(rets.mean()), 6),
        "median_ret": round(float(np.median(rets)), 6),
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "avg_r": round(float(np.mean([t.r for t in trades])), 3),
        "avg_minutes": round(float(np.mean([t.candles for t in trades])) * 5, 1),
        "squareoff_share": round(reasons.get("squareoff", 0) / len(trades), 3),
        "exits": reasons,
    }


def judge(early: dict, later: dict) -> tuple[bool, str]:
    problems = []
    for name, p in zip(PERIODS, (early, later), strict=True):
        if p["trades"] < MIN_TRADES:
            problems.append(f"only {p['trades']} trades in {name} (needs {MIN_TRADES})")
        elif p["avg_ret"] <= 0:
            problems.append(f"loses {p['avg_ret'] * 100:.3f}% a trade after costs in {name}")
    pf = later.get("profit_factor")
    if later["trades"] >= MIN_TRADES and (pf is None or pf < MIN_PF_LATER):
        problems.append(f"profit factor {pf or 0:.2f} in {PERIODS[1]} (needs {MIN_PF_LATER})")
    if problems:
        return False, "Fails the evidence bar: " + "; ".join(problems) + "."
    return True, "Passes the evidence bar."


def summarize(trades: list[Trade]) -> dict:
    by = {p: [t for t in trades if (t.day < SPLIT) == (p == PERIODS[0])] for p in PERIODS}
    periods = {p: stats(ts) for p, ts in by.items()}
    sides = {
        p: {"long": stats([t for t in ts if t.side > 0]), "short": stats([t for t in ts if t.side < 0])}
        for p, ts in by.items()
    }
    passes, verdict = judge(periods[PERIODS[0]], periods[PERIODS[1]])
    return {"periods": periods, "sides": sides, "passes": passes, "verdict": verdict}


def run(cfg, strategy_ids: list[str] | None = None, progress=print) -> dict:
    store = CandleStore(cfg.root, cfg.interval)
    rules = Rules.from_config(cfg)
    ids = strategy_ids or list(STRATEGIES)
    trades: dict[str, list[Trade]] = {s: [] for s in ids}
    symbols = store.symbols()
    if not symbols:
        raise FileNotFoundError("no candles cached: run `trademare-intraday data update` first")
    first, last = None, None
    for n, symbol in enumerate(symbols, 1):
        candles = store.read(symbol)
        if candles is None or len(candles) < 500:
            continue
        candles = candles[candles.index >= str(cfg.history_start)]
        first = min(first or candles.index[0], candles.index[0])
        last = max(last or candles.index[-1], candles.index[-1])
        f = feat.build(candles, cfg.minutes)
        for sid in ids:
            trades[sid] += simulate(sid, symbol, f, evaluate(STRATEGIES[sid], f), rules)
        progress(f"[{n}/{len(symbols)}] {symbol}")
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "data_from": str(first.date()) if first is not None else None,
        "data_through": str(last.date()) if last is not None else None,
        "symbols": len(symbols),
        "records": {sid: summarize(ts) for sid, ts in trades.items()},
    }


def path(cfg) -> Path:
    return cfg.root / "study.json"


def save(cfg, result: dict) -> Path:
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=1, default=str))
    return p


def load(cfg) -> dict | None:
    p = path(cfg)
    return json.loads(p.read_text()) if p.exists() else None


def sample_trades(trades: list[Trade], n: int = 20) -> list[dict]:
    return [asdict(t) for t in sorted(trades, key=lambda t: t.entry_time, reverse=True)[:n]]
