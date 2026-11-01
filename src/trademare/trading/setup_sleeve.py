"""Book setups as a separate paper-trading sleeve (docs/plans/2026-11-01-book-setups-plan.md).

Runs after the core strategy each day. Setup positions carry ``strategy = "setup:<id>"``; the core
strategy neither sells them nor counts them against its slots. Here they get:

- exit signals when their setup's own exit condition holds, or after the holding limit;
- new entry signals for the selected setups' matches on that session, inside the sleeve's share of
  the budget and position limit (plus the account's usual sizing, sector and cash limits);
- trailing stops for setups that trail (``trail_level``, used by the desk's daily stop update).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date

import pandas as pd
from sqlalchemy import select

from trademare.core.models import Direction, SignalAction, SignalStatus
from trademare.risk.manager import Exposure
from trademare.storage.db import SignalRow
from trademare.strategy.evidence import event_warnings, risk_evidence
from trademare.strategy.setup_study import PERIODS, Universe, current_record
from trademare.strategy.setups import SETUPS, Setup, SetupContext, evaluate

log = logging.getLogger(__name__)

PREFIX = "setup:"
OPEN = "OPEN"


def setup_of(strategy: str | None) -> Setup | None:
    if strategy and strategy.startswith(PREFIX):
        return SETUPS.get(strategy.removeprefix(PREFIX))
    return None


def trail_level(setup: Setup, bars: pd.DataFrame) -> float | None:
    """Where the setup's trailing stop sits after the last bar; None if it does not trail."""
    frame = evaluate(setup, bars, SetupContext())
    value = frame["trail"].iloc[-1] if len(frame) else float("nan")
    return None if pd.isna(value) else float(value)


def record_line(record: dict, setup_id: str) -> str:
    r = record["records"].get(setup_id)
    if not r:
        return "No track record yet."
    parts = []
    for p in PERIODS:
        st = r["periods"][p]
        if st["trades"]:
            parts.append(
                f"{p}: {st['trades']} trades, {st['win_rate'] * 100:.0f}% won, "
                f"{st['avg_ret'] * 100:+.2f}% a trade"
            )
    return (
        "Track record on this market: "
        + "; ".join(parts)
        + ("" if r["passes"] else " (fails the evidence bar)")
    )


def run(desk, s, day: date, report) -> None:
    """Exits and new entries for the setups sleeve after the core strategy's run for ``day``."""
    cfg = desk.cfg.setups
    market = desk.market.value
    open_setups = [p for p in desk.db.open_positions(s, market) if setup_of(p.strategy)]
    if not cfg.enabled and not open_setups:
        return
    done = s.scalars(
        select(SignalRow.id)
        .where(SignalRow.market == market, SignalRow.scan_date == day, SignalRow.strategy.like(f"{PREFIX}%"))
        .limit(1)
    ).first()
    if done:
        return
    universe = Universe(desk.service, desk.market)
    if universe.data_through != day:
        report.notes.append(f"book setups: prices for {day} not loaded yet; setups wait for the next run")
        return
    try:
        record = current_record(desk.service, desk.market, universe)
    except Exception as e:  # a failed study must not stop the core strategy's run
        log.exception("setup study failed")
        report.notes.append(f"WARNING book setups skipped: track record could not be computed ({e})")
        return
    expires = desk.clock.session_close(desk.clock.next_session(day)).astimezone(UTC)
    _exits(desk, s, day, report, universe, open_setups, expires)
    if cfg.enabled:
        _entries(desk, s, day, report, record, open_setups, expires)


def _exits(desk, s, day, report, universe, open_setups, expires) -> None:
    exiting = {o.position_id for o in desk._orders(s, purpose="exit", status=OPEN)}
    for pos in open_setups:
        setup = setup_of(pos.strategy)
        bars = universe.bars.get(pos.symbol)
        if pos.id in exiting or setup is None or bars is None:
            continue
        frame = evaluate(setup, bars.loc[: pd.Timestamp(day)], universe.ctx, pos.symbol)
        held = len(desk.clock.sessions_between(pos.entry_date, day))
        if bool(frame["exit"].iloc[-1]):
            reason, why = "setup rule", f"{setup.name}: {setup.exit_text}"
        elif held >= setup.max_hold:
            reason, why = "time", f"{setup.name}: held {held} sessions (limit {setup.max_hold})"
        else:
            continue
        sig = desk._exit_signal(s, pos, day, expires, reason, None, None)
        sig.strategy = pos.strategy
        sig.reasons = {**sig.reasons, "exit": [why]}
        report.signals.append(sig)


def _entries(desk, s, day, report, record, open_setups, expires) -> None:
    cfg = desk.cfg.setups
    state = desk.account_state(s)
    if state.kill_switch:
        report.notes.append("book setups: kill switch active, no new entries")
        return
    records = record["records"]
    allowed = [x for x in cfg.selected if not cfg.require_record or records.get(x, {}).get("passes")]
    if blocked := [x for x in cfg.selected if x not in allowed]:
        report.notes.append(
            "book setups not trading (fail the evidence bar): " + ", ".join(SETUPS[x].name for x in blocked)
        )
    groups: dict[str, list[dict]] = {}
    for m in record.get("matches", []):
        if m["setup"] in allowed and m["signal_date"] == day.isoformat():
            groups.setdefault(m["symbol"], []).append(m)
    if not groups:
        return
    ranked = sorted(groups.values(), key=lambda ms: (-len(ms), -max(m["strength"] for m in ms)))

    positions = desk.db.open_positions(s, desk.market.value)
    entry_orders = desk._orders(s, purpose="entry", status=OPEN)
    held = {p.symbol for p in positions} | {o.symbol for o in entry_orders}
    sleeve_orders = [o for o in entry_orders if setup_of(desk._order_strategy(s, o))]
    slots = cfg.max_positions - len(open_setups) - len(sleeve_orders)
    room = desk.cfg.budget * cfg.budget_pct / 100
    room -= sum(p.quantity * (p.last_price or p.entry_price) for p in open_setups)
    room -= sum((o.limit_price or 0) * o.quantity for o in sleeve_orders)
    equity, cash, _ = desk.equity(s)
    exposure = desk._exposure(s)
    capital = desk.risk.capital(equity)
    for ms in ranked:
        if slots <= 0:
            break
        best = max(ms, key=lambda m: m["strength"])
        symbol, entry, stop, target = best["symbol"], best["entry"], best["stop"], best["target"]
        if symbol in held:
            continue
        setup = SETUPS[best["setup"]]
        events = desk.events_for(symbol, day)
        if skip := desk._event_skip(events):
            report.notes.append(f"{symbol}: setup entry skipped ({skip})")
            desk.db.log(
                s, "event_skip", f"{symbol} setup entry skipped: {skip}", desk.market.value, symbol=symbol
            )
            continue
        qty = desk.risk.size(entry, stop, best["sector"], equity, cash, exposure)
        qty = min(qty, int(max(room, 0) // entry))
        if qty <= 0:
            report.notes.append(f"{symbol}: no size within the setups' budget/limits")
            continue
        reasons = {
            "setup": [
                f"{setup.name} (book {setup.book}): {setup.summary}",
                f"Buys only if the price trades above {entry:,.2f} next session (a buy-stop). "
                + setup.exit_text,
                record_line(record, setup.id),
            ]
        }
        if len(ms) > 1:
            reasons["setup"].append(
                "Also matches: " + ", ".join(SETUPS[m["setup"]].name for m in ms if m is not best)
            )
        if warnings := event_warnings(events):
            reasons["events"] = warnings
        sig = SignalRow(
            id=uuid.uuid4().hex,
            market=desk.market.value,
            symbol=symbol,
            direction=Direction.LONG.value,
            action=SignalAction.ENTRY.value,
            quantity=qty,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            score=0.0,
            confidence=1.0,
            reasons=reasons,
            evidence={"risk": risk_evidence(entry, stop, target, qty, capital, desk.cfg), "events": events},
            strategy=f"{PREFIX}{setup.id}",
            status=SignalStatus.PENDING.value,
            scan_date=day,
            expires_at=expires,
        )
        s.add(sig)
        report.signals.append(sig)
        value = qty * entry
        exposure = Exposure(
            gross=exposure.gross + value,
            by_sector={
                **exposure.by_sector,
                best["sector"]: exposure.by_sector.get(best["sector"], 0.0) + value,
            },
            positions=exposure.positions + 1,
        )
        cash -= value
        room -= value
        slots -= 1
        held.add(symbol)
