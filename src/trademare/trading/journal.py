"""Trade journal: every entry signal you decided on, followed through to its outcome.

Approved signals are traced through the order to the position and its result. Rejected and expired
signals show what *would* have happened had you taken them (signal price to the latest close,
ignoring the stop), so your own decisions can be judged too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from trademare.core.models import Direction, Market
from trademare.data.store import ParquetStore
from trademare.risk.tax import long_term_from
from trademare.storage.db import Database, OrderRow, PositionRow, SignalRow
from trademare.trading.outcomes import describe as describe_order


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
    quantity: int | None = None
    invested: float | None = None  # entry price x quantity
    current_value: float | None = None  # last price x quantity (exit value once closed)
    price_date: date | None = None  # date of the price used for open trades
    stop: float | None = None
    costs: float | None = None  # brokerage, taxes and fees paid so far
    long_term_on: date | None = None  # open longs: first day a sale counts as a long-term gain
    outcome: str | None = None  # approved but not (yet) filled: what happened to the order, in a sentence

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
    open_totals: dict[str, dict[str, float]] = field(default_factory=dict)  # per market: invested, value, pnl

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.closed if self.closed else None


def _last_close(store: ParquetStore, market: Market, symbol: str) -> tuple[float | None, date | None]:
    bars = store.read_bars(market, symbol)
    if bars is None or bars.empty:
        return None, None
    return float(bars["close"].iloc[-1]), bars.index[-1].date()


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
    closes: dict[tuple[str, str], tuple[float | None, date | None]] = {}
    for sig in signals:
        market = Market(sig.market)
        sign = 1 if sig.direction == Direction.LONG.value else -1
        key = (sig.market, sig.symbol)
        if key not in closes:
            closes[key] = _last_close(store, market, sig.symbol)
        last, last_day = closes[key]
        risk = abs(sig.entry_price - sig.stop_price) if sig.stop_price else None
        if sig.status == "APPROVED":
            pos, order = positions.get(sig.id), orders.get(sig.id)
            if pos is None:
                stage = "awaiting fill" if order is not None and order.status == "OPEN" else "not filled"
                entries.append(
                    JournalEntry(
                        sig, stage, order.status if order else None, last_price=last, price_date=last_day,
                        quantity=order.quantity if order else sig.quantity, stop=sig.stop_price,
                        outcome=describe_order(order, store)[1] if order else "No order was placed.",
                    )
                )  # fmt: skip
                continue
            closed = pos.status == "closed"
            if closed:
                px, px_day = pos.exit_price, pos.exit_date
            elif last is not None:  # latest cached close: at least as fresh as the last daily run
                px, px_day = last, last_day
            else:
                px, px_day = pos.last_price or pos.entry_price, None
            invested = pos.entry_price * pos.quantity
            costs = (pos.entry_costs or 0.0) + ((pos.exit_costs or 0.0) if closed else 0.0)
            if closed and pos.realized_pnl is not None:
                pnl = pos.realized_pnl
            else:
                pnl = sign * (px - pos.entry_price) * pos.quantity - (pos.entry_costs or 0.0)
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
                    return_pct=pnl / invested if invested else None,
                    r_multiple=(pnl / (per_share_risk * pos.quantity)) if per_share_risk else None,
                    quantity=pos.quantity,
                    invested=invested,
                    current_value=px * pos.quantity,
                    price_date=px_day,
                    stop=pos.stop_price,
                    costs=costs,
                    long_term_on=long_term_from(pos.entry_date) if not closed and sign > 0 else None,
                )
            )
        else:
            ret = sign * (last / sig.entry_price - 1) if last else None
            entries.append(
                JournalEntry(
                    sig,
                    sig.status.lower(),
                    last_price=last,
                    price_date=last_day,
                    return_pct=ret,
                    r_multiple=(sign * (last - sig.entry_price) / risk) if last and risk else None,
                    hypothetical=True,
                    quantity=sig.quantity,
                    stop=sig.stop_price,
                )
            )

    taken = [e for e in entries if e.stage in ("open", "closed")]
    closed = [e for e in taken if e.stage == "closed"]
    realised: dict[str, float] = {}
    for e in closed:
        realised[e.signal.market] = realised.get(e.signal.market, 0.0) + (e.pnl or 0.0)
    open_totals: dict[str, dict[str, float]] = {}
    for e in taken:
        if e.stage == "open":
            t = open_totals.setdefault(
                e.signal.market, {"invested": 0.0, "value": 0.0, "pnl": 0.0, "count": 0}
            )
            t["invested"] += e.invested or 0.0
            t["value"] += e.current_value or 0.0
            t["pnl"] += e.pnl or 0.0
            t["count"] += 1
    for t in open_totals.values():
        t["return"] = t["pnl"] / t["invested"] if t["invested"] else 0.0
    skipped = [e for e in entries if e.hypothetical and e.return_pct is not None]
    summary = JournalSummary(
        taken=len(taken),
        closed=len(closed),
        wins=sum(1 for e in closed if (e.pnl or 0) > 0),
        realised=realised,
        skipped=len([e for e in entries if e.hypothetical]),
        skipped_avg_return=sum(e.return_pct for e in skipped) / len(skipped) if skipped else None,
        open_totals=open_totals,
    )
    return entries, summary
