"""Trade journal: every entry signal you decided on, followed through to its outcome.

Approved signals are traced through the order to the position and its result. Rejected and expired
signals show what *would* have happened had you taken them (signal price to the latest close,
ignoring the stop), so your own decisions can be judged too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from trademare.core.models import Direction, Market
from trademare.data.store import ParquetStore
from trademare.storage.db import Database, OrderRow, PositionRow, SignalRow


@dataclass
class JournalEntry:
    signal: SignalRow
    stage: str  # awaiting fill | not filled | open | closed | rejected | expired
    order_status: str | None = None
    entry_date: date | None = None
    entry_price: float | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    last_price: float | None = None
    pnl: float | None = None  # after costs, for taken trades
    return_pct: float | None = None
    r_multiple: float | None = None
    hypothetical: bool = False  # True for rejected/expired: "if you had taken it"

    @property
    def market(self) -> Market:
        return Market(self.signal.market)


@dataclass
class JournalSummary:
    taken: int
    closed: int
    wins: int
    realised: dict[str, float]  # per market currency
    skipped: int
    skipped_avg_return: float | None  # what the signals you passed on did

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.closed if self.closed else None


def _last_close(store: ParquetStore, market: Market, symbol: str) -> float | None:
    bars = store.read_bars(market, symbol)
    return None if bars is None or bars.empty else float(bars["close"].iloc[-1])


def build_journal(
    db: Database, store: ParquetStore, limit: int = 200
) -> tuple[list[JournalEntry], JournalSummary]:
    with db.session() as s:
        signals = list(
            s.scalars(
                select(SignalRow)
                .where(SignalRow.action == "ENTRY", SignalRow.status.in_(("APPROVED", "REJECTED", "EXPIRED")))
                .order_by(SignalRow.created_at.desc())
                .limit(limit)
            )
        )
        ids = [sig.id for sig in signals]
        orders = {o.signal_id: o for o in s.scalars(select(OrderRow).where(OrderRow.signal_id.in_(ids)))}
        positions = {
            p.entry_signal_id: p
            for p in s.scalars(select(PositionRow).where(PositionRow.entry_signal_id.in_(ids)))
        }
        s.expunge_all()

    entries: list[JournalEntry] = []
    closes: dict[tuple[str, str], float | None] = {}
    for sig in signals:
        market = Market(sig.market)
        sign = 1 if sig.direction == Direction.LONG.value else -1
        key = (sig.market, sig.symbol)
        if key not in closes:
            closes[key] = _last_close(store, market, sig.symbol)
        last = closes[key]
        risk = abs(sig.entry_price - sig.stop_price) if sig.stop_price else None
        if sig.status == "APPROVED":
            pos, order = positions.get(sig.id), orders.get(sig.id)
            if pos is None:
                stage = "awaiting fill" if order is not None and order.status == "OPEN" else "not filled"
                entries.append(JournalEntry(sig, stage, order.status if order else None, last_price=last))
                continue
            closed = pos.status == "closed"
            px = pos.exit_price if closed else (pos.last_price or last)
            gross = sign * ((px or pos.entry_price) - pos.entry_price) * pos.quantity
            pnl = (
                pos.realized_pnl
                if closed and pos.realized_pnl is not None
                else gross - (pos.entry_costs or 0)
            )
            per_share_risk = pos.initial_risk or risk
            entries.append(
                JournalEntry(
                    sig,
                    "closed" if closed else "open",
                    order.status if order else None,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=pos.exit_date,
                    exit_price=pos.exit_price,
                    exit_reason=pos.exit_reason,
                    last_price=px,
                    pnl=pnl,
                    return_pct=sign * ((px or pos.entry_price) / pos.entry_price - 1),
                    r_multiple=(pnl / (per_share_risk * pos.quantity)) if per_share_risk else None,
                )
            )
        else:
            ret = sign * (last / sig.entry_price - 1) if last else None
            entries.append(
                JournalEntry(
                    sig,
                    sig.status.lower(),
                    last_price=last,
                    return_pct=ret,
                    r_multiple=(sign * (last - sig.entry_price) / risk) if last and risk else None,
                    hypothetical=True,
                )
            )

    taken = [e for e in entries if e.stage in ("open", "closed")]
    closed = [e for e in taken if e.stage == "closed"]
    realised: dict[str, float] = {}
    for e in closed:
        realised[e.signal.market] = realised.get(e.signal.market, 0.0) + (e.pnl or 0.0)
    skipped = [e for e in entries if e.hypothetical and e.return_pct is not None]
    summary = JournalSummary(
        taken=len(taken),
        closed=len(closed),
        wins=sum(1 for e in closed if (e.pnl or 0) > 0),
        realised=realised,
        skipped=len([e for e in entries if e.hypothetical]),
        skipped_avg_return=sum(e.return_pct for e in skipped) / len(skipped) if skipped else None,
    )
    return entries, summary
