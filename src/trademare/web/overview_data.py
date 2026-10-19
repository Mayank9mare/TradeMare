"""Data shaping for the dashboard's overview and chrome (status bar, badges, sparklines)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select

from trademare.config.schema import MarketConfig
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.storage.db import Database, EquityRow, MarketStateRow, OrderRow, PositionRow, SignalRow

EXCHANGE = {Market.IN: "NSE", Market.US: "NYSE"}
TZ_LABEL = {Market.IN: "IST", Market.US: "New York"}


@dataclass(frozen=True)
class MarketStatus:
    market: Market
    exchange: str
    is_open: bool
    next_run: datetime | None  # local market time of the next daily job
    tz_label: str
    prices_through: date | None = None  # last session with prices in the cache
    behind: int = 0  # completed sessions (past the daily run time) still missing prices


def price_freshness(
    market: Market, cfg: MarketConfig, store, now: datetime | None = None
) -> tuple[date | None, int]:
    """(last session with index prices, sessions missing since). A session counts as missing only
    once its daily run time has passed, since prices are fetched then."""
    if not cfg.benchmark:
        return None, 0
    bars = store.read_bars(market, cfg.benchmark)
    if bars is None or bars.empty:
        return None, 0
    through = bars.index.max().date()
    clock = MarketClock(market)
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(clock.tz))
    hour, minute = (int(x) for x in cfg.daily_run_time.split(":"))
    try:
        due = [
            d for d in clock.sessions_between(through, local.date())
            if d < local.date() or local.time() >= time(hour, minute)
        ]  # fmt: skip
    except ValueError:
        return through, 0
    return through, len(due)


def market_status(market: Market, cfg: MarketConfig, now: datetime | None = None, store=None) -> MarketStatus:
    clock = MarketClock(market)
    now = now or datetime.now(UTC)
    tz = ZoneInfo(clock.tz)
    local = now.astimezone(tz)
    hour, minute = (int(x) for x in cfg.daily_run_time.split(":"))
    run_at = time(hour, minute)
    try:
        is_open = clock.is_open(now)
        day = local.date()
        if not (clock.is_session(day) and local.time() < run_at):
            day = clock.next_session(day)
        next_run = datetime.combine(day, run_at, tz)
    except ValueError:  # beyond the calendar's range
        is_open, next_run = False, None
    through, behind = price_freshness(market, cfg, store, now) if store is not None else (None, 0)
    return MarketStatus(market, EXCHANGE[market], is_open, next_run, TZ_LABEL[market], through, behind)


@dataclass(frozen=True)
class Sparkline:
    path: str  # SVG path in a 0..width x 0..height box
    last_x: float
    last_y: float
    first: float
    last: float
    change: float  # last / first - 1
    low: float
    high: float
    start: date
    end: date


def sparkline(series: pd.Series, width: float = 240, height: float = 48, pad: float = 4) -> Sparkline | None:
    """Scale a dated series into a polyline path; None when there is nothing to draw."""
    s = series.dropna()
    if len(s) < 2:
        return None
    lo, hi = float(s.min()), float(s.max())
    span = (hi - lo) or 1.0
    n = len(s) - 1
    xs = [pad + (width - 2 * pad) * i / n for i in range(len(s))]
    ys = [pad + (height - 2 * pad) * (1 - (float(v) - lo) / span) for v in s]
    path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in zip(xs, ys, strict=True))
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    return Sparkline(
        path=path,
        last_x=xs[-1],
        last_y=ys[-1],
        first=first,
        last=last,
        change=last / first - 1 if first else 0.0,
        low=lo,
        high=hi,
        start=pd.Timestamp(s.index[0]).date(),
        end=pd.Timestamp(s.index[-1]).date(),
    )


@dataclass(frozen=True)
class Account:
    equity: float
    previous_equity: float | None
    cash: float
    positions: int
    peak: float
    kill_switch: bool
    kill_reason: str | None
    last_processed: date | None
    equity_line: Sparkline | None


def account_summary(db: Database, market: Market, budget: float) -> Account:
    with db.session() as s:
        state = s.get(MarketStateRow, market.value)
        query = select(EquityRow).where(EquityRow.market == market.value).order_by(EquityRow.day)
        rows = list(s.scalars(query))
        positions = s.scalar(
            select(func.count())
            .select_from(PositionRow)
            .where(PositionRow.market == market.value, PositionRow.status == "open")
        )
    equity = rows[-1].equity if rows else (state.cash if state else budget)
    series = pd.Series([r.equity for r in rows], index=pd.to_datetime([r.day for r in rows]), dtype="float64")
    return Account(
        equity=equity,
        previous_equity=rows[-2].equity if len(rows) > 1 else None,
        cash=state.cash if state else budget,
        positions=positions or 0,
        peak=state.peak_equity if state else budget,
        kill_switch=bool(state and state.kill_switch),
        kill_reason=state.kill_reason if state else None,
        last_processed=state.last_processed if state else None,
        equity_line=sparkline(series),
    )


def pending_signals(db: Database) -> list[SignalRow]:
    now = datetime.now(UTC)
    with db.session() as s:
        rows = list(s.scalars(select(SignalRow).where(SignalRow.status == "PENDING")))
    return [r for r in rows if r.expires_at is None or r.expires_at.replace(tzinfo=UTC) > now]


def working_entries(db: Database, store=None, days: int = 5) -> list[dict]:
    """Approved buy/sell orders still waiting, plus those that ended in the last ``days`` days
    (filled, or not), each with one sentence saying what happened. Protective stops are excluded."""
    from datetime import UTC, datetime, timedelta

    from trademare.trading.outcomes import describe

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    with db.session() as s:
        rows = list(
            s.scalars(
                select(OrderRow)
                .where(
                    OrderRow.purpose.in_(("entry", "exit")),
                    (OrderRow.status == "OPEN") | (OrderRow.updated_at >= since),
                )
                .order_by(OrderRow.status != "OPEN", OrderRow.updated_at.desc())
            )
        )
        s.expunge_all()
    out = []
    for o in rows:
        status, text = describe(o, store)
        out.append({"order": o, "status": status, "text": text})
    return out


def ideas_vs_signals(db: Database, config, market: Market, scan_date: date | None) -> str:
    """One sentence on why the ideas list and the signals list differ for ``scan_date``."""
    from trademare.config.schema import Rebalance, StrategyMode

    cfg = config.markets[market]
    with db.session() as s:
        n = (
            s.scalar(
                select(func.count())
                .select_from(SignalRow)
                .where(
                    SignalRow.market == market.value,
                    SignalRow.scan_date == scan_date,
                    SignalRow.action == "ENTRY",
                )
            )
            or 0
        )
        state = s.get(MarketStateRow, market.value)
    made = (
        f"{n} buy signal{'s' if n != 1 else ''} came from this scan"
        if n
        else "No buy signals came from this scan"
    )
    if n:
        return made + ": the top ideas that fit your risk limits (open slots, position and sector caps)."
    if state is not None and state.kill_switch:
        return made + ": the kill switch is tripped, so new buys are blocked until you re-arm it."
    freq = config.strategy.factor_rebalance
    if cfg.strategy_mode is StrategyMode.FACTOR and freq is not Rebalance.DAILY and scan_date:
        clock = MarketClock(market)
        try:
            day = scan_date
            while True:  # first session of the next period
                day = clock.next_session(day)
                prev = clock.previous_session(day)
                if freq is Rebalance.WEEKLY and prev.isocalendar()[:2] != day.isocalendar()[:2]:
                    break
                if freq is Rebalance.MONTHLY and (prev.year, prev.month) != (day.year, day.month):
                    break
            when = f"; the next is {day:%a %d %b}"
        except ValueError:
            when = ""
        period = "month" if freq is Rebalance.MONTHLY else "week"
        return (
            f"{made}: this strategy only buys on the first trading day of each {period} "
            f"(its tested rebalance){when}. Between those days the ideas are for information."
        )
    return made + ": no idea passed the risk limits (open slots, position and sector caps, regime)."
