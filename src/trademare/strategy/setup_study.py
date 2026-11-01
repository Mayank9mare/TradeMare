"""Track record of each book setup on TradeMare's own price history, and today's matches.

Every signal becomes one simulated trade with the book's rules, filled from daily bars:

- entry: a buy-stop at the signal day's high, next session only; a gap above it fills at the open;
- exits, checked each day in this order: the open at or below the stop (fill at the open), the low
  touching the stop (fill at the stop), the target reached (at the open if it gapped past, else at
  the target), then after the close: the stop trails up to the setup's trail level, and the setup's
  exit condition or the holding limit exits at the next open;
- on the entry day only the stop is checked (a fill and a stop in one bar counts as a loss);
- one trade per stock at a time; India delivery costs on a ₹10,000 position plus slippage on
  stop and open exits.

Signals count only when the stock is liquid that day (20-day traded value at least the market's
minimum and, if set, among the ``universe_top_n`` most-traded), as the live strategy requires.
The universe is today's index list, so results carry some survivorship bias.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from trademare.backtest.costs import cost_model
from trademare.core.models import Market, OrderSide
from trademare.data.universe import load_universe
from trademare.strategy import setups as setups_module
from trademare.strategy.setups import SETUPS, Setup, SetupContext, evaluate

NOTIONAL = 10_000.0  # position size for the cost estimate (the DP charge is a flat fee per sale)
SPLIT = date(2020, 1, 1)  # periods: before (in-sample) and from (the later check)
PERIODS = ("2010-2019", "2020-now")
STUDY_START = date(2010, 1, 1)

# Evidence bar (docs/plans/2026-11-01-book-setups-plan.md), fixed before any results.
MIN_TRADES = 100
MIN_PROFIT_FACTOR_LATER = 1.1


@dataclass
class Trade:
    symbol: str
    signal_date: str
    entry_date: str
    entry: float
    stop: float
    exit_date: str
    exit: float
    reason: str  # stop | target | rule | time | open (still open at the end of the data)
    days: int
    ret: float  # net of costs, fraction of the entry value
    r: float  # gross gain per share / initial risk per share
    bench: float | None = None  # the benchmark's return over the same days (signal close to exit)


@dataclass
class PeriodStats:
    trades: int = 0
    win_rate: float | None = None
    avg_ret: float | None = None
    median_ret: float | None = None
    avg_r: float | None = None
    avg_bench: float | None = None  # the benchmark over the same days, averaged
    profit_factor: float | None = None
    median_days: float | None = None
    best: float | None = None
    worst: float | None = None


@dataclass
class Record:
    setup: str
    periods: dict[str, PeriodStats]
    by_year: dict[str, int]
    passes: bool
    verdict: str
    recent: list[dict]  # the latest trades, newest first


def _round_trip_cost(market: Market, entry: float, exit_: float) -> float:
    model = cost_model(market)
    qty = max(1, int(NOTIONAL // entry)) if entry > 0 else 1
    total = model.cost(OrderSide.BUY, entry, qty) + model.cost(OrderSide.SELL, exit_, qty)
    return total / (entry * qty)


def simulate(
    setup: Setup,
    symbol: str,
    bars: pd.DataFrame,
    frame: pd.DataFrame,
    eligible: pd.Series,
    market: Market,
    slippage_bps: float = 5.0,
    start: date = STUDY_START,
    benchmark: pd.Series | None = None,
) -> list[Trade]:
    o, h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    signal = (frame["signal"] & eligible.reindex(frame.index, fill_value=False)).to_numpy()
    entry_lvl, stop0 = frame["entry"].to_numpy(), frame["stop"].to_numpy()
    target0, trail, rule_exit = (
        frame["target"].to_numpy(),
        frame["trail"].to_numpy(),
        frame["exit"].to_numpy(),
    )
    days = bars.index
    bench = None if benchmark is None else benchmark.reindex(days).ffill().to_numpy(dtype=float)
    first = int(days.searchsorted(pd.Timestamp(start)))
    slip = slippage_bps / 10_000
    trades: list[Trade] = []
    m = len(c)
    i = max(first, 0)
    while i < m - 1:
        if not signal[i]:
            i += 1
            continue
        j = i + 1
        level = entry_lvl[i]
        if h[j] < level:  # the buy-stop never triggered
            i += 1
            continue
        fill = max(o[j], level)
        stop, target = stop0[i], target0[i]
        if not (stop < fill):  # gapped through the whole risk: no sane trade
            i += 1
            continue
        risk = fill - stop
        exit_price, reason, k = None, "open", m - 1
        if lo[j] <= stop:
            exit_price, reason, k = stop * (1 - slip), "stop", j
        else:
            if not math.isnan(trail[j]):
                stop = max(stop, trail[j])
            pending_open_exit = None
            if rule_exit[j]:
                pending_open_exit = "rule"
            for k in range(j + 1, m):
                if pending_open_exit:
                    exit_price, reason = o[k] * (1 - slip), pending_open_exit
                    break
                if o[k] <= stop:
                    exit_price, reason = o[k] * (1 - slip), "stop"
                    break
                if lo[k] <= stop:
                    exit_price, reason = stop * (1 - slip), "stop"
                    break
                if not math.isnan(target) and h[k] >= target:
                    exit_price, reason = (o[k] if o[k] >= target else target), "target"
                    break
                if not math.isnan(trail[k]):
                    stop = max(stop, trail[k])
                if rule_exit[k]:
                    pending_open_exit = "rule"
                elif k - j >= setup.max_hold:
                    pending_open_exit = "time"
            else:
                k = m - 1
        if exit_price is None:  # still open at the end of the data: mark at the last close
            exit_price, reason, k = c[m - 1], "open", m - 1
        gross = exit_price / fill - 1
        ret = gross - _round_trip_cost(market, fill, exit_price)
        trades.append(
            Trade(
                symbol=symbol,
                signal_date=days[i].date().isoformat(),
                entry_date=days[j].date().isoformat(),
                entry=round(float(fill), 2),
                stop=round(float(stop0[i]), 2),
                exit_date=days[k].date().isoformat(),
                exit=round(float(exit_price), 2),
                reason=reason,
                days=int(k - j),
                ret=round(float(ret), 5),
                r=round(float((exit_price - fill) / risk), 3),
                bench=None if bench is None else _ratio(bench[k], bench[i]),
            )
        )
        i = k + 1  # one trade at a time in this stock
    return trades


def _ratio(now: float, then: float) -> float | None:
    return None if not (then > 0) or math.isnan(now) else round(float(now / then - 1), 5)


def _stats(trades: list[Trade]) -> PeriodStats:
    closed = [t for t in trades if t.reason != "open"]
    if not closed:
        return PeriodStats()
    rets = np.array([t.ret for t in closed])
    gains, losses = rets[rets > 0].sum(), -rets[rets < 0].sum()
    return PeriodStats(
        trades=len(closed),
        win_rate=round(float((rets > 0).mean()), 4),
        avg_ret=round(float(rets.mean()), 5),
        median_ret=round(float(np.median(rets)), 5),
        avg_r=round(float(np.mean([t.r for t in closed])), 3),
        avg_bench=_mean([t.bench for t in closed if t.bench is not None]),
        profit_factor=round(float(gains / losses), 3) if losses > 0 else None,
        median_days=float(np.median([t.days for t in closed])),
        best=round(float(rets.max()), 4),
        worst=round(float(rets.min()), 4),
    )


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 5) if values else None


def judge(periods: dict[str, PeriodStats]) -> tuple[bool, str]:
    early, later = periods[PERIODS[0]], periods[PERIODS[1]]
    problems = []
    for name, p in ((PERIODS[0], early), (PERIODS[1], later)):
        if p.trades < MIN_TRADES:
            problems.append(f"only {p.trades} trades in {name} (needs {MIN_TRADES})")
        elif p.avg_ret is None or p.avg_ret <= 0:
            problems.append(
                f"loses on average after costs in {name} ({(p.avg_ret or 0) * 100:+.2f}% a trade)"
            )
    pf = later.profit_factor
    if later.trades >= MIN_TRADES and (pf is None or pf < MIN_PROFIT_FACTOR_LATER):
        problems.append(f"profit factor {pf or 0:.2f} in {PERIODS[1]} (needs {MIN_PROFIT_FACTOR_LATER})")
    if problems:
        return False, "Fails the evidence bar: " + "; ".join(problems) + "."
    return True, (
        f"Passes the evidence bar: profitable after costs in both periods "
        f"({early.avg_ret * 100:+.2f}% and {later.avg_ret * 100:+.2f}% a trade)."
    )


def summarize(setup_id: str, trades: list[Trade]) -> Record:
    split = SPLIT.isoformat()
    periods = {
        PERIODS[0]: _stats([t for t in trades if t.signal_date < split]),
        PERIODS[1]: _stats([t for t in trades if t.signal_date >= split]),
    }
    by_year: dict[str, int] = {}
    for t in trades:
        by_year[t.signal_date[:4]] = by_year.get(t.signal_date[:4], 0) + 1
    passes, verdict = judge(periods)
    recent = [asdict(t) for t in sorted(trades, key=lambda t: t.signal_date, reverse=True)[:20]]
    return Record(setup_id, periods, dict(sorted(by_year.items())), passes, verdict, recent)


# ---------------------------------------------------------------- universe-wide
class Universe:
    """Bars, liquidity and the setup context for one market, loaded once."""

    def __init__(self, service, market: Market) -> None:
        self.market = market
        self.cfg = service.config.markets[market]
        u = load_universe(self.cfg.universe)
        self.names = dict(zip(u["symbol"], u["name"], strict=True))
        self.sectors = dict(zip(u["symbol"], u["sector"], strict=True))
        store = service.store
        self.bars = {
            s: b for s in u["symbol"] if (b := store.read_bars(market, s)) is not None and len(b) >= 60
        }
        benchmark = store.read_bars(market, self.cfg.benchmark) if self.cfg.benchmark else None
        self.ctx = SetupContext(self.bars, self.sectors, benchmark)
        self.benchmark = None if benchmark is None else benchmark["close"]
        value20 = pd.DataFrame(
            {s: (b["close"] * b["volume"]).rolling(20).mean() for s, b in self.bars.items()}
        )
        liquid = value20 >= self.cfg.min_avg_traded_value
        if self.cfg.universe_top_n:
            value126 = pd.DataFrame(
                {
                    s: (b["close"] * b["volume"]).rolling(126, min_periods=60).mean()
                    for s, b in self.bars.items()
                }
            )
            liquid &= value126.rank(axis=1, ascending=False, method="min") <= self.cfg.universe_top_n
        self.liquid = liquid
        self.data_through = max(b.index[-1] for b in self.bars.values()).date() if self.bars else None

    def eligible(self, symbol: str) -> pd.Series:
        return self.liquid[symbol] if symbol in self.liquid else pd.Series(dtype=bool)


def rules_version() -> str:
    """Changes whenever the setup rules or this simulator change, so a stale record is redone."""
    source = inspect.getsource(setups_module) + inspect.getsource(simulate) + inspect.getsource(judge)
    return hashlib.sha1(source.encode()).hexdigest()[:12]


def record_path(service, market: Market) -> Path:
    return Path(service.config.data.cache_dir) / "setups" / f"{market.value}-record.json"


def run_study(
    service, market: Market, setup_ids: list[str] | None = None, universe: Universe | None = None
) -> dict:
    u = universe or Universe(service, market)
    ids = setup_ids or list(SETUPS)
    records, matches = {}, []
    for sid in ids:
        setup = SETUPS[sid]
        trades: list[Trade] = []
        for symbol, bars in u.bars.items():
            frame = evaluate(setup, bars, u.ctx, symbol)
            if (m := _match(u, sid, symbol, bars, frame)) is not None:
                matches.append(asdict(m))
            trades += simulate(
                setup, symbol, bars, frame, u.eligible(symbol), market, u.cfg.slippage_bps,
                benchmark=u.benchmark,
            )  # fmt: skip
        records[sid] = asdict(summarize(sid, trades))
    return {
        "market": market.value,
        "data_through": u.data_through.isoformat() if u.data_through else None,
        "rules_version": rules_version(),
        "records": records,
        "matches": matches,
    }


def load_record(service, market: Market) -> dict | None:
    path = record_path(service, market)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_record(service, market: Market, result: dict) -> Path:
    path = record_path(service, market)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
    return path


def record_is_current(result: dict | None, data_through: date | None) -> bool:
    return bool(
        result
        and result.get("rules_version") == rules_version()
        and data_through is not None
        and result.get("data_through") == data_through.isoformat()
        and len(result.get("records", {})) == len(SETUPS)
        and "matches" in result
    )


@dataclass
class Match:
    setup: str
    symbol: str
    name: str
    sector: str
    signal_date: str
    close: float
    entry: float
    stop: float
    target: float | None
    risk_pct: float
    strength: float
    liquid: bool


def _match(u: Universe, sid: str, symbol: str, bars: pd.DataFrame, frame: pd.DataFrame) -> Match | None:
    """A Match when the latest session is a liquid signal day for this setup."""
    if bars.index[-1].date() != u.data_through or not frame["signal"].iloc[-1]:
        return None
    elig = u.eligible(symbol)
    if not (len(elig) and bool(elig.iloc[-1])):
        return None
    row = frame.iloc[-1]
    entry, stop = float(row["entry"]), float(row["stop"])
    return Match(
        setup=sid,
        symbol=symbol,
        name=u.names.get(symbol, symbol),
        sector=u.sectors.get(symbol, ""),
        signal_date=bars.index[-1].date().isoformat(),
        close=round(float(bars["close"].iloc[-1]), 2),
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=None if pd.isna(row["target"]) else round(float(row["target"]), 2),
        risk_pct=round((entry - stop) / entry, 4),
        strength=float(row["strength"]) if pd.notna(row["strength"]) else 0.0,
        liquid=True,
    )


def todays_matches(universe: Universe, setup_ids: list[str]) -> list[Match]:
    """Stocks whose latest bar is a signal day for any of the setups (entries valid next session)."""
    out: list[Match] = []
    for sid in setup_ids:
        for symbol, bars in universe.bars.items():
            frame = evaluate(SETUPS[sid], bars, universe.ctx, symbol)
            if (m := _match(universe, sid, symbol, bars, frame)) is not None:
                out.append(m)
    return out


def current_record(service, market: Market, universe: Universe | None = None, refresh: bool = False) -> dict:
    """The saved track record, recomputed when the rules or the data have moved on (about 30 s)."""
    u = universe or Universe(service, market)
    saved = None if refresh else load_record(service, market)
    if record_is_current(saved, u.data_through):
        return saved  # type: ignore[return-value]
    result = run_study(service, market, universe=u)
    save_record(service, market, result)
    return result
