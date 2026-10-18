"""Paper (or live) trading vs the backtest over the same days.

If the backtester is an honest model of trading, replaying it over the days the desk has been
running should give similar trades and similar returns. Differences fall into three groups:
- your decisions: signals you rejected, let expire or have not acted on yet;
- execution: different fill prices or dates for the same trade;
- unexplained: trades on only one side with no matching decision, which point at a bug or at
  data that changed after the fact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import select

from trademare.backtest.engine import Backtester
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.storage.db import Database, EquityRow, PositionRow, SignalRow
from trademare.strategy.scanner import ScanContext

MIN_SESSIONS = 5

_DECISION = {
    "REJECTED": "you rejected the signal",
    "EXPIRED": "the signal expired unapproved",
    "PENDING": "the signal is awaiting your decision",
}


@dataclass(frozen=True)
class TradeMatch:
    symbol: str
    paper_date: date | None
    paper_price: float | None
    backtest_date: date | None
    backtest_price: float | None
    note: str

    @property
    def price_gap(self) -> float | None:
        if self.paper_price and self.backtest_price:
            return self.paper_price / self.backtest_price - 1
        return None


@dataclass
class Tracking:
    market: str
    start: date | None
    end: date | None
    sessions: int
    status: str  # ok | too_short | no_history
    paper_return: float | None = None
    backtest_return: float | None = None
    tracking_error: float | None = None  # annualised std of daily return differences
    curve: pd.DataFrame | None = None  # columns: paper, backtest (both start at 1.0)
    matched: list[TradeMatch] = field(default_factory=list)
    decisions: list[TradeMatch] = field(default_factory=list)
    unexplained: list[TradeMatch] = field(default_factory=list)

    @property
    def gap(self) -> float | None:
        if self.paper_return is None or self.backtest_return is None:
            return None
        return self.paper_return - self.backtest_return


def compare_with_backtest(service: DataService, db: Database, market: Market) -> Tracking:
    with db.session() as s:
        equity = list(
            s.scalars(select(EquityRow).where(EquityRow.market == market.value).order_by(EquityRow.day))
        )
        positions = list(s.scalars(select(PositionRow).where(PositionRow.market == market.value)))
        signals = list(
            s.scalars(select(SignalRow).where(SignalRow.market == market.value, SignalRow.action == "ENTRY"))
        )
        s.expunge_all()
    if not equity:
        return Tracking(market.value, None, None, 0, "no_history")
    start, end = equity[0].day, equity[-1].day
    if len(equity) < MIN_SESSIONS:
        return Tracking(market.value, start, end, len(equity), "too_short")

    result = Backtester(ScanContext(service, market), service.config).run(start, end)
    paper = pd.Series({pd.Timestamp(r.day): r.equity for r in equity}).sort_index()
    bt = result.equity.reindex(paper.index).ffill()
    curve = pd.DataFrame({"paper": paper / paper.iloc[0], "backtest": bt / bt.iloc[0]})
    diff = curve.pct_change().dropna()
    te = (diff["paper"] - diff["backtest"]).std() * math.sqrt(252) if len(diff) > 1 else None

    # Entries on each side, keyed by symbol (the first entry in the period).
    bt_entries: dict[str, tuple[date, float]] = {}
    for t in sorted(result.trades, key=lambda t: t.entry_date):
        bt_entries.setdefault(t.symbol, (t.entry_date, t.entry_price))
    for p in result.open_positions:
        bt_entries.setdefault(p.symbol, (p.entry_date, p.entry_price))
    paper_entries: dict[str, tuple[date, float]] = {}
    for p in sorted(positions, key=lambda p: p.entry_date):
        if p.entry_date >= start:
            paper_entries.setdefault(p.symbol, (p.entry_date, p.entry_price))
    decision_by_symbol = {sig.symbol: sig.status for sig in sorted(signals, key=lambda x: x.created_at)}

    tracking = Tracking(
        market.value,
        start,
        end,
        len(equity),
        "ok",
        paper_return=float(curve["paper"].iloc[-1] - 1),
        backtest_return=float(curve["backtest"].iloc[-1] - 1),
        tracking_error=None if te is None or math.isnan(te) else float(te),
        curve=curve,
    )
    for symbol in sorted(set(bt_entries) | set(paper_entries)):
        pd_, pp = paper_entries.get(symbol, (None, None))
        bd, bp = bt_entries.get(symbol, (None, None))
        if pd_ and bd:
            note = "same day" if pd_ == bd else f"entered {(pd_ - bd).days:+d} days vs backtest"
            tracking.matched.append(TradeMatch(symbol, pd_, pp, bd, bp, note))
        elif bd:
            status = decision_by_symbol.get(symbol)
            if status in _DECISION:
                tracking.decisions.append(TradeMatch(symbol, None, None, bd, bp, _DECISION[status]))
            else:
                tracking.unexplained.append(
                    TradeMatch(symbol, None, None, bd, bp, "backtest only: no signal on record")
                )
        else:
            tracking.unexplained.append(
                TradeMatch(symbol, pd_, pp, None, None, "paper only: backtest did not trade it")
            )
    return tracking
