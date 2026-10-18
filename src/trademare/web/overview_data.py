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


def market_status(market: Market, cfg: MarketConfig, now: datetime | None = None) -> MarketStatus:
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
    return MarketStatus(market, EXCHANGE[market], is_open, next_run, TZ_LABEL[market])


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


def working_entries(db: Database) -> list[OrderRow]:
    """Approved buy/sell orders not filled yet (protective stops are excluded)."""
    with db.session() as s:
        rows = list(
            s.scalars(
                select(OrderRow)
                .where(OrderRow.status == "OPEN", OrderRow.purpose.in_(("entry", "exit")))
                .order_by(OrderRow.placed_on)
            )
        )
        s.expunge_all()
    return rows
