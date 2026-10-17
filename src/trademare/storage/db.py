"""SQLite persistence for live/paper trading state (SQLAlchemy 2.0 ORM).

Tables: signals, orders, positions, equity snapshots, per-market state
(paper cash, kill switch) and an append-only audit log.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    market: Mapped[str] = mapped_column(String(4), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))  # ENTRY | EXIT
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)  # limit for entries; reference price for exits
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), index=True)  # see SignalStatus
    scan_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(4), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    purpose: Mapped[str] = mapped_column(String(8))  # entry | exit | stop
    quantity: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(12))
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(18), index=True)
    broker: Mapped[str] = mapped_column(String(16))
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    placed_on: Mapped[date] = mapped_column(Date)  # session date the order was placed after
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)  # None = good till cancelled
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    costs: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(4), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_risk: Mapped[float] = mapped_column(Float)  # per share at entry
    entry_score: Mapped[float] = mapped_column(Float, default=0.0)
    entry_costs: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(8), index=True)  # open | closed
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exit_costs: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EquityRow(Base):
    __tablename__ = "equity"

    market: Mapped[str] = mapped_column(String(4), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    cash: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float)  # signed (shorts negative)
    equity: Mapped[float] = mapped_column(Float)
    gross_exposure: Mapped[float] = mapped_column(Float)


class MarketStateRow(Base):
    __tablename__ = "market_state"

    market: Mapped[str] = mapped_column(String(4), primary_key=True)
    cash: Mapped[float] = mapped_column(Float)  # paper-account cash
    peak_equity: Mapped[float] = mapped_column(Float)
    kill_switch: Mapped[bool] = mapped_column(default=False)
    kill_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    kill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_processed: Mapped[date | None] = mapped_column(Date, nullable=True)  # last session fully processed


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    market: Mapped[str | None] = mapped_column(String(4), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        if url in ("sqlite://", "sqlite:///:memory:"):
            # One shared connection, or every new connection would see a fresh empty database.
            self.engine: Engine = create_engine(
                url, future=True, poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
        else:
            self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as s, s.begin():
            yield s

    def log(self, session: Session, kind: str, message: str, market: str | None = None, **data: Any) -> None:
        session.add(EventRow(kind=kind, message=message, market=market, data=_jsonable(data)))

    def market_state(self, session: Session, market: str, starting_cash: float) -> MarketStateRow:
        state = session.get(MarketStateRow, market)
        if state is None:
            state = MarketStateRow(market=market, cash=starting_cash, peak_equity=starting_cash)
            session.add(state)
            session.flush()
        return state

    def open_positions(self, session: Session, market: str) -> list[PositionRow]:
        return list(
            session.scalars(
                select(PositionRow).where(PositionRow.market == market, PositionRow.status == "open")
            )
        )


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        out[k] = v.isoformat() if isinstance(v, date | datetime) else v
    return out
