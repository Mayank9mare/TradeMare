"""Trading desk: turns scans into signals, approvals into orders, and fills
into positions — with every step persisted and audited.

Daily process for one market (``run_daily``), after the session closes:
1. For each session not yet processed (catch-up if the Mac was asleep):
   fill paper orders against that session's bar, ratchet trailing stops,
   mark positions to market, check the kill switch.
2. Expire signals that were never approved.
3. Scan and create new exit/entry signals (entries respect the rebalance
   schedule, slots, sizing and the kill switch).

Approvals (``approve``) place orders for the next session:
- entry -> LIMIT order at the signal price, valid for one session; once
  filled, a protective STOP (and a TARGET limit if configured) are placed
  and cancel each other (OCO);
- exit  -> MARKET order at the next open.
Live (non-simulated) brokers additionally require ``validated: true``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trademare.analysis.indicators import atr
from trademare.analysis.llm import create_llm_client
from trademare.analysis.sentiment import SentimentAnalyst
from trademare.backtest.costs import cost_model
from trademare.config.schema import Rebalance, StrategyMode, TrailMethod
from trademare.config.secrets import Secrets
from trademare.core.clock import MarketClock
from trademare.core.models import (
    Direction,
    Market,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    SignalAction,
    SignalStatus,
)
from trademare.data.service import DataService
from trademare.data.universe import load_universe
from trademare.execution.brokers import BrokerError, LoginRequired, OrderUpdate, create_broker
from trademare.execution.fills import Bar, gapped_past_limit, limit_fill, slipped, stop_fill, target_fill
from trademare.risk.manager import Exposure, RiskManager
from trademare.storage.db import Database, EquityRow, OrderRow, PositionRow, SignalRow
from trademare.strategy.evidence import build_evidence, risk_evidence
from trademare.strategy.scanner import ScanContext, ScanResult, is_rebalance_day, save_scan

log = logging.getLogger(__name__)

OPEN = OrderStatus.OPEN.value


class DeskError(Exception):
    """A request the desk refuses (bad state, limits, unvalidated live market)."""


@dataclass
class DailyReport:
    market: Market
    session: date
    processed: list[date] = field(default_factory=list)
    fills: list[str] = field(default_factory=list)
    signals: list[SignalRow] = field(default_factory=list)
    expired: int = 0
    equity: float | None = None
    previous_equity: float | None = None
    cash: float | None = None
    open_positions: int = 0
    kill_switch: bool = False
    kill_reason: str | None = None
    regime: str | None = None
    rebalance_day: bool = True
    notes: list[str] = field(default_factory=list)


def _side(direction: str, opening: bool) -> OrderSide:
    long = direction == Direction.LONG.value
    return OrderSide.BUY if opening == long else OrderSide.SELL


class TradingDesk:
    def __init__(
        self, service: DataService, db: Database, market: Market, secrets: Secrets | None = None
    ) -> None:
        self.service = service
        self.db = db
        self.market = market
        self.config = service.config
        self.cfg = self.config.markets[market]
        self.risk = RiskManager(self.cfg, self.config.strategy)
        self.secrets = secrets or Secrets()
        self.broker = create_broker(market, self.cfg, self.secrets)
        self.clock = MarketClock(market)
        self.costs = cost_model(market)
        self._sectors: dict[str, str] | None = None
        self._names: dict[str, str] | None = None
        self.sentiment: SentimentAnalyst | None = None  # built on first use when llm.enabled

    # ------------------------------------------------------------------ helpers
    def _bar(self, symbol: str, day: date) -> Bar | None:
        bars = self.service.store.read_bars(self.market, symbol)
        ts = pd.Timestamp(day)
        if bars is None or ts not in bars.index:
            return None
        row = bars.loc[ts]
        return Bar(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

    def _last_close(self, symbol: str, day: date) -> float | None:
        bars = self.service.store.read_bars(self.market, symbol)
        if bars is None:
            return None
        upto = bars.loc[: pd.Timestamp(day)]
        return float(upto["close"].iloc[-1]) if len(upto) else None

    def account_state(self, s: Session):
        return self.db.market_state(s, self.market.value, starting_cash=self.cfg.budget)

    def _orders(self, s: Session, **filters) -> list[OrderRow]:
        q = select(OrderRow).where(OrderRow.market == self.market.value)
        for key, value in filters.items():
            q = q.where(getattr(OrderRow, key) == value)
        return list(s.scalars(q))

    def _exposure(self, s: Session) -> Exposure:
        by_sector: dict[str, float] = {}
        gross = 0.0
        count = 0
        for p in self.db.open_positions(s, self.market.value):
            value = p.quantity * (p.last_price or p.entry_price)
            gross += value
            by_sector[p.sector or ""] = by_sector.get(p.sector or "", 0.0) + value
            count += 1
        for o in self._orders(s, purpose="entry", status=OPEN):
            value = o.quantity * (o.limit_price or 0.0)
            gross += value
            sector = self.sector(o.symbol)
            by_sector[sector] = by_sector.get(sector, 0.0) + value
            count += 1
        return Exposure(gross=gross, by_sector=by_sector, positions=count)

    def sector(self, symbol: str) -> str:
        if self._sectors is None:
            u = load_universe(self.cfg.universe)
            self._sectors = dict(zip(u["symbol"], u["sector"], strict=True))
            self._names = dict(zip(u["symbol"], u["name"], strict=True))
        return self._sectors.get(symbol, "")

    def company(self, symbol: str) -> str:
        self.sector(symbol)
        return (self._names or {}).get(symbol, symbol)

    def _sentiment_analyst(self) -> SentimentAnalyst | None:
        if not self.config.llm.enabled:
            return None
        if self.sentiment is None:
            cache = Path(self.config.data.cache_dir).parent / "sentiment"
            self.sentiment = SentimentAnalyst(
                self.config.llm, create_llm_client(self.config.llm, self.secrets), cache
            )
        return self.sentiment

    def equity(self, s: Session) -> tuple[float, float, float]:
        """(equity, cash, gross exposure) at the latest marked prices."""
        state = self.account_state(s)
        value = gross = 0.0
        for p in self.db.open_positions(s, self.market.value):
            px = p.last_price or p.entry_price
            sign = 1 if p.direction == Direction.LONG.value else -1
            value += sign * p.quantity * px
            gross += p.quantity * px
        return state.cash + value, state.cash, gross

    # ------------------------------------------------------------------ approvals
    def approve(
        self,
        signal_id: str,
        quantity: int | None = None,
        limit: float | None = None,
        stop: float | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> OrderRow:
        now = now or datetime.now(UTC)
        with self.db.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None or sig.market != self.market.value:
                raise DeskError(f"no signal {signal_id} for {self.market}")
            if sig.status != SignalStatus.PENDING.value:
                raise DeskError(f"signal is {sig.status}, not PENDING")
            if sig.expires_at and _aware(sig.expires_at) < now:
                raise DeskError("signal has expired")  # marked EXPIRED by the next daily run
            if not self.broker.simulated and not self.cfg.validated:
                raise DeskError(
                    f"{self.market} is not validated: live orders are blocked (set validated: true only "
                    "after the strategy passes its out-of-sample test)"
                )
            state = self.account_state(s)
            valid_until = self.clock.next_session(sig.scan_date)

            if sig.action == SignalAction.ENTRY.value:
                if state.kill_switch:
                    raise DeskError(f"kill switch is active ({state.kill_reason}); re-arm before new entries")
                qty = quantity or sig.quantity
                price = limit or sig.entry_price
                stop_price = stop or sig.stop_price
                sign = 1 if sig.direction == Direction.LONG.value else -1
                if qty <= 0 or stop_price is None or sign * (price - stop_price) <= 0:
                    raise DeskError("quantity must be positive and the stop on the losing side of the entry")
                order = self._new_order(
                    s,
                    sig,
                    "entry",
                    _side(sig.direction, True),
                    qty,
                    OrderType.LIMIT,
                    price,
                    None,
                    valid_until,
                )
                sig.quantity, sig.entry_price, sig.stop_price = qty, price, stop_price
            else:
                pos = s.get(PositionRow, sig.position_id) if sig.position_id else None
                if pos is None or pos.status != "open":
                    raise DeskError("position for this exit is no longer open")
                if any(o.position_id == pos.id for o in self._orders(s, purpose="exit", status=OPEN)):
                    raise DeskError("an exit order is already open for this position")
                order = self._new_order(
                    s,
                    sig,
                    "exit",
                    _side(pos.direction, False),
                    pos.quantity,
                    OrderType.MARKET,
                    None,
                    None,
                    valid_until,
                    position_id=pos.id,
                )
            sig.status = SignalStatus.APPROVED.value
            sig.decided_at = now
            sig.note = note
            self.db.log(
                s,
                "approve",
                f"approved {sig.action} {sig.symbol} x{order.quantity}",
                self.market.value,
                signal=sig.id,
                order=order.id,
            )
            return order

    def reject(self, signal_id: str, note: str | None = None) -> None:
        with self.db.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None or sig.status != SignalStatus.PENDING.value:
                raise DeskError("only pending signals can be rejected")
            sig.status = SignalStatus.REJECTED.value
            sig.decided_at = datetime.now(UTC)
            sig.note = note
            self.db.log(s, "reject", f"rejected {sig.action} {sig.symbol}", self.market.value, signal=sig.id)

    def rearm(self) -> None:
        with self.db.session() as s:
            state = self.account_state(s)
            equity, _, _ = self.equity(s)
            state.kill_switch, state.kill_reason, state.kill_at = False, None, None
            state.peak_equity = equity  # otherwise an ongoing drawdown would re-trip immediately
            self.db.log(s, "rearm", f"kill switch re-armed at equity {equity:,.2f}", self.market.value)

    def _new_order(
        self,
        s: Session,
        sig: SignalRow | None,
        purpose: str,
        side: OrderSide,
        qty: int,
        order_type: OrderType,
        limit: float | None,
        trigger: float | None,
        valid_until: date | None,
        position_id: int | None = None,
        placed_on: date | None = None,
    ) -> OrderRow:
        model = Order(
            market=self.market,
            symbol=sig.symbol if sig else "",
            side=side,
            quantity=qty,
            order_type=order_type,
            limit_price=limit,
            trigger_price=trigger,
            signal_id=sig.id if sig else None,
        )
        try:
            broker_id = self.broker.place_order(model)
        except BrokerError as e:
            raise DeskError(f"broker rejected order: {e}") from e
        row = OrderRow(
            id=model.id,
            signal_id=model.signal_id,
            position_id=position_id,
            market=self.market.value,
            symbol=model.symbol,
            side=side.value,
            purpose=purpose,
            quantity=qty,
            order_type=order_type.value,
            limit_price=limit,
            trigger_price=trigger,
            status=OPEN,
            broker=self.broker.name,
            broker_order_id=broker_id,
            placed_on=placed_on or (sig.scan_date if sig else date.today()),
            valid_until=valid_until,
        )
        s.add(row)
        s.flush()
        return row

    # ------------------------------------------------------------------ session processing (paper fills)
    def process_session(self, s: Session, day: date, report: DailyReport) -> None:
        if not self.broker.simulated:
            try:
                self._sync_live(s, day, report)
            except LoginRequired as e:
                report.notes.append(f"broker login required — orders not synced: {e}")
            except BrokerError as e:
                report.notes.append(f"broker error — orders not fully synced: {e}")
            return
        state = self.account_state(s)
        fresh_entries: dict[int, float] = {}

        def eligible(o: OrderRow) -> bool:
            return o.placed_on < day

        # 1. Market exits at the open.
        for o in self._orders(s, purpose="exit", status=OPEN):
            bar = self._bar(o.symbol, day)
            if not eligible(o) or bar is None:
                continue
            pos = s.get(PositionRow, o.position_id)
            price = slipped(bar.open, OrderSide(o.side), self.cfg.slippage_bps)
            self._fill(o, price, day)
            self._close(s, state, pos, price, day, o.costs, o.reason or "signal", report)

        # 2. Limit entries.
        for o in self._orders(s, purpose="entry", status=OPEN):
            bar = self._bar(o.symbol, day)
            if not eligible(o) or bar is None:
                continue
            side = OrderSide(o.side)
            price = limit_fill(side, o.limit_price, bar)
            if price is None:
                continue  # expires below if this was its last session
            sig = s.get(SignalRow, o.signal_id)
            long = side is OrderSide.BUY
            if (long and price <= sig.stop_price) or (not long and price >= sig.stop_price):
                o.status = OrderStatus.CANCELLED.value
                report.fills.append(f"{o.symbol}: entry skipped — opened beyond the stop")
                continue
            self._fill(o, price, day)
            state.cash -= (price * o.quantity if long else -price * o.quantity) + o.costs
            pos = PositionRow(
                market=self.market.value,
                symbol=o.symbol,
                sector=self.sector(o.symbol),
                direction=sig.direction,
                quantity=o.quantity,
                entry_price=price,
                entry_date=day,
                stop_price=sig.stop_price,
                target_price=sig.target_price,
                initial_risk=abs(price - sig.stop_price),
                entry_score=sig.score,
                entry_costs=o.costs,
                status="open",
                last_price=price,
                entry_signal_id=sig.id,
            )
            s.add(pos)
            s.flush()
            exit_side = _side(sig.direction, False)
            self._new_order(
                s,
                sig,
                "stop",
                exit_side,
                o.quantity,
                OrderType.STOP,
                None,
                sig.stop_price,
                None,
                position_id=pos.id,
                placed_on=day,
            )
            if sig.target_price:
                self._new_order(
                    s,
                    sig,
                    "target",
                    exit_side,
                    o.quantity,
                    OrderType.LIMIT,
                    sig.target_price,
                    None,
                    None,
                    position_id=pos.id,
                    placed_on=day,
                )
            fresh_entries[pos.id] = price
            report.fills.append(f"{o.symbol}: {sig.direction} {o.quantity} @ {price:,.2f}")
            self.db.log(s, "fill", f"entry {o.symbol} x{o.quantity} @ {price}", self.market.value, order=o.id)

        # 3. Protective stops, then targets (stop wins when both are touched).
        for pos in self.db.open_positions(s, self.market.value):
            bar = self._bar(pos.symbol, day)
            if bar is None:
                continue
            orders = {o.purpose: o for o in self._orders(s, position_id=pos.id, status=OPEN)}
            opened_at = fresh_entries.get(pos.id)
            stop = orders.get("stop")
            active = stop is not None and (opened_at is not None or stop.placed_on < day)
            if active and (hit := stop_fill(OrderSide(stop.side), stop.trigger_price, bar, opened_at)):
                buffer = self.cfg.stop_loss.limit_buffer_pct
                if hit[1] and gapped_past_limit(OrderSide(stop.side), stop.trigger_price, hit[0], buffer):
                    # Same as a live limit stop that the gap jumped over: exit at the next open.
                    stop.status = OrderStatus.CANCELLED.value
                    self._market_exit(s, pos, day, "stop (gapped past limit)", report)
                    continue
                price = slipped(hit[0], OrderSide(stop.side), self.cfg.slippage_bps)
                self._fill(stop, price, day)
                reason = "stop (gap)" if hit[1] else "stop"
                self._close(s, state, pos, price, day, stop.costs, reason, report)
                continue
            if (target := orders.get("target")) and (
                hit := target_fill(OrderSide(target.side), target.limit_price, bar, opened_at)
            ):
                self._fill(target, hit[0], day)
                self._close(s, state, pos, hit[0], day, target.costs, "target", report)

        # 4. Day orders whose last session has passed.
        for o in self._orders(s, status=OPEN):
            if o.valid_until is not None and o.valid_until <= day:
                o.status = "EXPIRED"
                self.broker.cancel_order(o.broker_order_id or "")
                report.notes.append(f"{o.symbol}: {o.purpose} order expired unfilled")

    # ------------------------------------------------------------------ live reconciliation
    def _sync_live(self, s: Session, day: date, report: DailyReport) -> None:
        """Reconcile open orders with the broker (fills happened at the exchange, not here)."""
        state = self.account_state(s)
        for o in self._orders(s, status=OPEN):
            if o.purpose in ("entry", "exit"):
                update = self.broker.get_order(o.broker_order_id)
                expired = o.valid_until is not None and o.valid_until <= day
                if update.status is OrderStatus.PARTIALLY_FILLED and expired and update.filled_quantity:
                    o.quantity = update.filled_quantity  # day order ended part-filled: keep what we got
                    update = OrderUpdate(OrderStatus.FILLED, update.filled_quantity, update.average_price)
                if update.is_filled and update.average_price:
                    self._fill(o, update.average_price, day)
                    if o.purpose == "entry":
                        self._open_live_position(s, state, o, day, report)
                    else:
                        pos = s.get(PositionRow, o.position_id)
                        self._cancel_protection(s, pos, report)
                        reason = o.reason or "signal"
                        self._close(s, state, pos, update.average_price, day, o.costs, reason, report)
                elif update.is_dead:
                    o.status = update.status.value
                    report.notes.append(
                        f"{o.symbol}: {o.purpose} order {update.status.value} ({update.message})"
                    )
                elif expired:
                    o.status = "EXPIRED"
                    report.notes.append(f"{o.symbol}: {o.purpose} order expired unfilled")
            elif o.purpose == "stop":
                update = self.broker.protection_status(o.broker_order_id)
                pos = s.get(PositionRow, o.position_id)
                if update.is_filled and update.average_price and pos is not None:
                    self._fill(o, update.average_price, day)
                    hit_target = pos.target_price is not None and abs(
                        update.average_price - pos.target_price
                    ) < abs(update.average_price - (pos.stop_price or 0))
                    self._close(
                        s,
                        state,
                        pos,
                        update.average_price,
                        day,
                        o.costs,
                        "target" if hit_target else "stop",
                        report,
                    )
                elif update.is_dead:
                    o.status = update.status.value
                    report.notes.append(
                        f"WARNING {o.symbol}: protective stop no longer active at the broker "
                        f"({update.message}); re-placing it"
                    )
                elif pos is not None and (bar := self._bar(pos.symbol, day)) is not None:
                    buffer = self.cfg.stop_loss.limit_buffer_pct
                    if gapped_past_limit(OrderSide(o.side), o.trigger_price, bar.close, buffer):
                        # The price is beyond the stop's limit leg, so it cannot fill: get out at the open.
                        try:
                            self.broker.cancel_protection(o.broker_order_id)
                        except BrokerError as e:
                            report.notes.append(f"WARNING {o.symbol}: could not cancel stuck stop: {e}")
                        o.status = OrderStatus.CANCELLED.value
                        self._market_exit(s, pos, day, "stop (gapped past limit)", report)

        # Every open live position must have a standing stop (or an exit on its way).
        for pos in self.db.open_positions(s, self.market.value):
            working = {o.purpose for o in self._orders(s, position_id=pos.id, status=OPEN)}
            if working & {"stop", "exit"} or pos.stop_price is None:
                continue
            exit_side = _side(pos.direction, False)
            try:
                pid = self.broker.place_protection(
                    pos.symbol,
                    exit_side,
                    pos.quantity,
                    pos.stop_price,
                    pos.target_price,
                    pos.last_price or pos.entry_price,
                )
            except BrokerError as e:
                report.notes.append(
                    f"WARNING {pos.symbol}: position has NO protective stop and re-placing failed: {e}"
                )
                continue
            s.add(
                OrderRow(
                    id=uuid.uuid4().hex,
                    position_id=pos.id,
                    market=self.market.value,
                    symbol=pos.symbol,
                    side=exit_side.value,
                    purpose="stop",
                    quantity=pos.quantity,
                    order_type=OrderType.STOP.value,
                    limit_price=pos.target_price,
                    trigger_price=pos.stop_price,
                    status=OPEN,
                    broker=self.broker.name,
                    broker_order_id=pid,
                    placed_on=day,
                    valid_until=None,
                )
            )
            report.notes.append(f"{pos.symbol}: protective stop re-placed at {pos.stop_price:,.2f}")

    def _open_live_position(self, s: Session, state, o: OrderRow, day: date, report: DailyReport) -> None:
        sig = s.get(SignalRow, o.signal_id)
        price = o.average_fill_price
        long = OrderSide(o.side) is OrderSide.BUY
        state.cash -= (price * o.quantity if long else -price * o.quantity) + o.costs
        pos = PositionRow(
            market=self.market.value,
            symbol=o.symbol,
            sector=self.sector(o.symbol),
            direction=sig.direction,
            quantity=o.quantity,
            entry_price=price,
            entry_date=day,
            stop_price=sig.stop_price,
            target_price=sig.target_price,
            initial_risk=abs(price - sig.stop_price),
            entry_score=sig.score,
            entry_costs=o.costs,
            status="open",
            last_price=price,
            entry_signal_id=sig.id,
        )
        s.add(pos)
        s.flush()
        exit_side = _side(sig.direction, False)
        try:
            protection_id = self.broker.place_protection(
                o.symbol, exit_side, o.quantity, sig.stop_price, sig.target_price, price
            )
        except BrokerError as e:
            report.notes.append(f"WARNING {o.symbol}: filled but the protective stop was NOT placed: {e}")
            protection_id = None
        if protection_id:
            s.add(
                OrderRow(
                    id=uuid.uuid4().hex,
                    signal_id=sig.id,
                    position_id=pos.id,
                    market=self.market.value,
                    symbol=o.symbol,
                    side=exit_side.value,
                    purpose="stop",
                    quantity=o.quantity,
                    order_type=OrderType.STOP.value,
                    limit_price=sig.target_price,
                    trigger_price=sig.stop_price,
                    status=OPEN,
                    broker=self.broker.name,
                    broker_order_id=protection_id,
                    placed_on=day,
                    valid_until=None,
                )
            )
        report.fills.append(f"{o.symbol}: {sig.direction} {o.quantity} @ {price:,.2f} (broker)")
        self.db.log(
            s, "fill", f"live entry {o.symbol} x{o.quantity} @ {price}", self.market.value, order=o.id
        )

    def _market_exit(self, s: Session, pos: PositionRow, day: date, reason: str, report: DailyReport) -> None:
        """Pre-approved protective exit: market order at the next session's open."""
        side = _side(pos.direction, False)
        model = Order(
            market=self.market,
            symbol=pos.symbol,
            side=side,
            quantity=pos.quantity,
            order_type=OrderType.MARKET,
        )
        try:
            broker_id = self.broker.place_order(model)
        except BrokerError as e:
            report.notes.append(
                f"WARNING {pos.symbol}: {reason} — market exit FAILED ({e}); exit it manually"
            )
            return
        s.add(
            OrderRow(
                id=model.id,
                position_id=pos.id,
                market=self.market.value,
                symbol=pos.symbol,
                side=side.value,
                purpose="exit",
                reason=reason,
                quantity=pos.quantity,
                order_type=OrderType.MARKET.value,
                status=OPEN,
                broker=self.broker.name,
                broker_order_id=broker_id,
                placed_on=day,
                valid_until=self.clock.next_session(day),
            )
        )
        report.notes.append(f"ALERT {pos.symbol}: {reason} — market exit placed for the next open")
        self.db.log(
            s, "stop_gap", f"{pos.symbol}: {reason}; market exit placed", self.market.value, position=pos.id
        )

    def _cancel_protection(self, s: Session, pos: PositionRow, report: DailyReport) -> None:
        for o in self._orders(s, position_id=pos.id, purpose="stop", status=OPEN):
            try:
                self.broker.cancel_protection(o.broker_order_id)
            except BrokerError as e:
                report.notes.append(
                    f"WARNING {pos.symbol}: could not cancel protective stop {o.broker_order_id}: {e}"
                )
            # Marked here so _close does not treat the GTT/Forever id as a regular order.
            o.status = OrderStatus.CANCELLED.value

    def _fill(self, o: OrderRow, price: float, day: date) -> None:
        o.status = OrderStatus.FILLED.value
        o.filled_quantity = o.quantity
        o.average_fill_price = price
        o.filled_on = day
        o.costs = self.costs.cost(OrderSide(o.side), price, o.quantity)

    def _close(
        self,
        s: Session,
        state,
        pos: PositionRow,
        price: float,
        day: date,
        exit_cost: float,
        reason: str,
        report: DailyReport,
    ) -> None:
        sign = 1 if pos.direction == Direction.LONG.value else -1
        state.cash += sign * pos.quantity * price - exit_cost
        pos.status, pos.exit_price, pos.exit_date, pos.exit_reason = "closed", price, day, reason
        pos.exit_costs = exit_cost
        pos.realized_pnl = sign * (price - pos.entry_price) * pos.quantity - pos.entry_costs - exit_cost
        pos.last_price = price
        for o in self._orders(s, position_id=pos.id, status=OPEN):  # OCO: cancel siblings
            o.status = OrderStatus.CANCELLED.value
            self.broker.cancel_order(o.broker_order_id or "")
        report.fills.append(f"{pos.symbol}: closed ({reason}) @ {price:,.2f}, P&L {pos.realized_pnl:,.2f}")
        self.db.log(
            s,
            "close",
            f"{pos.symbol} closed ({reason}) @ {price}",
            self.market.value,
            position=pos.id,
            pnl=pos.realized_pnl,
        )

    def _trail_and_mark(self, s: Session, day: date, report: DailyReport) -> None:
        sl = self.cfg.stop_loss
        for pos in self.db.open_positions(s, self.market.value):
            close = self._last_close(pos.symbol, day)
            if close is None:
                continue
            pos.last_price = close
            if not sl.trailing:
                continue
            if sl.trail_method is TrailMethod.ATR:
                bars = self.service.store.read_bars(self.market, pos.symbol).loc[: pd.Timestamp(day)]
                a = atr(bars, sl.atr_period).iloc[-1]
                distance = None if pd.isna(a) else sl.trail_value * float(a)
            else:
                distance = close * sl.trail_value / 100
            if not distance:
                continue
            sign = 1 if pos.direction == Direction.LONG.value else -1
            candidate = close - sign * distance
            if pos.stop_price is None or sign * (candidate - pos.stop_price) > 0:
                pos.stop_price = round(candidate, 2)
                for o in self._orders(s, position_id=pos.id, purpose="stop", status=OPEN):
                    o.trigger_price = pos.stop_price
                    if not self.broker.simulated:
                        try:
                            o.broker_order_id = self.broker.modify_protection(
                                o.broker_order_id,
                                pos.symbol,
                                OrderSide(o.side),
                                pos.quantity,
                                pos.stop_price,
                                pos.target_price,
                                close,
                            )
                        except BrokerError as e:
                            report.notes.append(f"{pos.symbol}: could not move stop at broker: {e}")

    def _snapshot(self, s: Session, day: date, report: DailyReport) -> None:
        state = self.account_state(s)
        equity, cash, gross = self.equity(s)
        prev = s.scalars(
            select(EquityRow)
            .where(EquityRow.market == self.market.value, EquityRow.day < day)
            .order_by(EquityRow.day.desc())
            .limit(1)
        ).first()
        row = s.get(EquityRow, (self.market.value, day)) or EquityRow(market=self.market.value, day=day)
        row.cash, row.positions_value, row.equity, row.gross_exposure = cash, equity - cash, equity, gross
        s.add(row)
        state.peak_equity = max(state.peak_equity, equity)
        check = self.risk.check_kill_switch(equity, prev.equity if prev else None, state.peak_equity)
        if check.tripped and not state.kill_switch:
            state.kill_switch, state.kill_reason, state.kill_at = True, check.reason, datetime.now(UTC)
            self.db.log(s, "kill_switch", f"kill switch tripped: {check.reason}", self.market.value)
        report.equity, report.cash = equity, cash
        report.previous_equity = prev.equity if prev else None

    # ------------------------------------------------------------------ signals
    def expire_signals(self, s: Session, now: datetime) -> int:
        n = 0
        q = select(SignalRow).where(SignalRow.market == self.market.value, SignalRow.status == "PENDING")
        for sig in s.scalars(q):
            if sig.expires_at and _aware(sig.expires_at) < now:
                sig.status = SignalStatus.EXPIRED.value
                n += 1
        return n

    def generate_signals(self, s: Session, day: date, report: DailyReport) -> ScanResult | None:
        exists = s.scalars(
            select(SignalRow)
            .where(SignalRow.market == self.market.value, SignalRow.scan_date == day)
            .limit(1)
        ).first()
        if exists:
            report.notes.append(f"signals for {day} already generated")
            return None
        factor_mode = self.cfg.strategy_mode is StrategyMode.FACTOR
        frequency = self.config.strategy.factor_rebalance if factor_mode else Rebalance.DAILY
        report.rebalance_day = is_rebalance_day(self.clock.previous_session(day), day, frequency)
        never_signalled = (
            s.scalars(select(SignalRow.id).where(SignalRow.market == self.market.value).limit(1)).first()
            is None
        )
        if not report.rebalance_day and never_signalled and not self.db.open_positions(s, self.market.value):
            report.rebalance_day = True  # first run of an empty account: build the initial portfolio now
            report.notes.append("initial portfolio build (first run of an empty account)")
        ctx = ScanContext(self.service, self.market)
        scan = ctx.evaluate(day)
        save_scan(self.service, scan)
        report.regime = scan.regime.label.value
        fund_scores: dict | None = None

        def evidence_for(symbol: str, risk: dict | None = None) -> dict:
            nonlocal fund_scores
            if fund_scores is None:  # universe-wide ranking, computed once per run
                fund_scores = ctx.fundamental_scores(day)
            return build_evidence(ctx, symbol, day, fund_scores, risk)

        if not report.rebalance_day:
            report.notes.append(f"not a rebalance day ({frequency}): no new signals; stops stay active")
            return scan

        state = self.account_state(s)
        # Store UTC: SQLite drops tz info and values are read back as UTC.
        expires = self.clock.session_close(self.clock.next_session(day)).astimezone(UTC)
        composites = {x.symbol: x.composite for x in scan.scores}
        positions = self.db.open_positions(s, self.market.value)
        exiting = {o.position_id for o in self._orders(s, purpose="exit", status=OPEN)}
        staying = []
        for pos in positions:
            if pos.id in exiting:
                continue
            c = composites.get(pos.symbol)
            long = pos.direction == Direction.LONG.value
            if c is not None and (
                (long and c <= scan.exit_long_below) or (not long and c >= scan.exit_short_above)
            ):
                report.signals.append(
                    self._exit_signal(s, pos, day, expires, "signal", c, evidence_for(pos.symbol))
                )
            else:
                staying.append(pos)
        max_positions = self.risk.max_positions(scan.regime.label, scan.vol_scale)
        excess = len(staying) - max_positions
        if excess > 0:
            sign = {p.id: 1 if p.direction == Direction.LONG.value else -1 for p in staying}
            weakest = sorted(staying, key=lambda p: sign[p.id] * (composites.get(p.symbol) or 0.0))
            for pos in weakest[:excess]:
                report.signals.append(
                    self._exit_signal(
                        s, pos, day, expires, "regime", composites.get(pos.symbol), evidence_for(pos.symbol)
                    )
                )
                staying.remove(pos)

        if state.kill_switch:
            report.notes.append("kill switch active: entry signals suppressed")
            return scan
        exposure = self._exposure(s)
        pending_entries = {o.symbol for o in self._orders(s, purpose="entry", status=OPEN)}
        held = {p.symbol for p in positions}
        slots = max_positions - len(staying) - len(pending_entries)
        equity, cash, _ = self.equity(s)
        sentiment = self._sentiment_analyst()
        for idea in scan.ideas:
            if slots <= 0:
                break
            if idea.symbol in held or idea.symbol in pending_entries:
                continue
            qty = self.risk.size(idea.levels.entry, idea.levels.stop, idea.sector, equity, cash, exposure)
            if qty <= 0:
                report.notes.append(f"{idea.symbol}: no size within budget/limits")
                continue
            reasons = dict(idea.reasons)
            if sentiment is not None:
                outcome = sentiment.assess(
                    self.market, idea.symbol, self.company(idea.symbol), idea.direction, day
                )
                if outcome.veto:
                    report.notes.append(f"{idea.symbol}: entry vetoed by news sentiment ({outcome.note})")
                    self.db.log(
                        s,
                        "sentiment_veto",
                        f"{idea.symbol} {idea.direction.value} vetoed: {outcome.note}",
                        self.market.value,
                        symbol=idea.symbol,
                        direction=idea.direction.value,
                        entry=idea.levels.entry,
                        composite=idea.composite,
                        sentiment=outcome.result.score if outcome.result else None,
                        reasons=outcome.result.reasons if outcome.result else [],
                    )
                    continue
                reasons["sentiment"] = outcome.result.reasons if outcome.result else [outcome.note]
            sig = SignalRow(
                id=uuid.uuid4().hex,
                market=self.market.value,
                symbol=idea.symbol,
                direction=idea.direction.value,
                action=SignalAction.ENTRY.value,
                quantity=qty,
                entry_price=idea.levels.entry,
                stop_price=idea.levels.stop,
                target_price=idea.levels.target,
                score=idea.composite,
                confidence=idea.confidence,
                reasons=reasons,
                evidence=evidence_for(
                    idea.symbol,
                    risk_evidence(
                        idea.levels.entry,
                        idea.levels.stop,
                        idea.levels.target,
                        qty,
                        self.risk.capital(equity),
                        self.cfg,
                    ),
                ),
                status=SignalStatus.PENDING.value,
                scan_date=day,
                expires_at=expires,
            )
            s.add(sig)
            report.signals.append(sig)
            value = qty * idea.levels.entry
            exposure = Exposure(
                gross=exposure.gross + value,
                by_sector={
                    **exposure.by_sector,
                    idea.sector: exposure.by_sector.get(idea.sector, 0.0) + value,
                },
                positions=exposure.positions + 1,
            )
            cash -= value
            slots -= 1
        return scan

    def _exit_signal(
        self,
        s: Session,
        pos: PositionRow,
        day: date,
        expires: datetime,
        reason: str,
        score: float | None,
        evidence: dict | None = None,
    ) -> SignalRow:
        sig = SignalRow(
            id=uuid.uuid4().hex,
            market=self.market.value,
            symbol=pos.symbol,
            direction=pos.direction,
            action=SignalAction.EXIT.value,
            quantity=pos.quantity,
            entry_price=pos.last_price or pos.entry_price,
            stop_price=None,
            target_price=None,
            score=score or 0.0,
            confidence=1.0,
            reasons={"exit": [f"{reason}: composite {score:+.2f}" if score is not None else reason]},
            exit_reason=reason,
            evidence=evidence,
            position_id=pos.id,
            status=SignalStatus.PENDING.value,
            scan_date=day,
            expires_at=expires,
        )
        s.add(sig)
        return sig

    def ensure_evidence(self) -> int:
        """Attach evidence to pending signals created before evidence was recorded."""
        with self.db.session() as s:
            q = select(SignalRow).where(SignalRow.market == self.market.value, SignalRow.status == "PENDING")
            # Checked in Python: older rows may hold JSON null rather than SQL NULL.
            missing = [sig for sig in s.scalars(q) if not sig.evidence]
            if not missing:
                return 0
            ctx = ScanContext(self.service, self.market)
            equity, _, _ = self.equity(s)
            capital = self.risk.capital(equity)
            scores_by_day: dict[date, dict] = {}
            for sig in missing:
                if sig.scan_date not in scores_by_day:
                    scores_by_day[sig.scan_date] = ctx.fundamental_scores(sig.scan_date)
                risk = None
                if sig.action == SignalAction.ENTRY.value:
                    risk = risk_evidence(
                        sig.entry_price, sig.stop_price, sig.target_price, sig.quantity, capital, self.cfg
                    )
                sig.evidence = build_evidence(
                    ctx, sig.symbol, sig.scan_date, scores_by_day[sig.scan_date], risk
                )
            return len(missing)

    # ------------------------------------------------------------------ daily
    def run_daily(
        self, asof: date | None = None, generate: bool = True, now: datetime | None = None
    ) -> DailyReport:
        day = asof or self.clock.last_completed_session()
        now = now or datetime.now(UTC)
        report = DailyReport(market=self.market, session=day)
        with self.db.session() as s:
            state = self.account_state(s)
            sessions = (
                self.clock.sessions_between(state.last_processed, day) if state.last_processed else [day]
            )
            for d in sessions:
                self.process_session(s, d, report)
                self._trail_and_mark(s, d, report)
                self._snapshot(s, d, report)
                state.last_processed = d
                report.processed.append(d)
            report.expired = self.expire_signals(s, now)
            if generate:
                self.generate_signals(s, day, report)
            report.kill_switch, report.kill_reason = state.kill_switch, state.kill_reason
            report.open_positions = len(self.db.open_positions(s, self.market.value))
            if report.equity is None:
                report.equity, report.cash, _ = self.equity(s)
            self.db.log(
                s,
                "daily",
                f"daily run {day}: {len(report.processed)} session(s), {len(report.signals)} signal(s)",
                self.market.value,
            )
        return report


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; they are stored in UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
