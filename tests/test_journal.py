from datetime import UTC, datetime

import pytest

from tests.test_desk import DAY0, SESSIONS, desk, entry_signals, set_bar  # noqa: F401  (fixture)
from trademare.trading.journal import build_journal


def test_journal_follows_decisions_to_outcomes(desk):  # noqa: F811
    desk.run_daily(DAY0)
    taken, passed = entry_signals(desk)[:2]
    desk.approve(taken.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    desk.reject(passed.id)
    entries, summary = build_journal(desk.db, desk.service.store)
    assert {e.signal.symbol: e.stage for e in entries} == {
        taken.symbol: "awaiting fill",
        passed.symbol: "rejected",
    }

    i = SESSIONS.index(DAY0)
    px = taken.entry_price
    set_bar(
        desk, taken.symbol, SESSIONS[i + 1], open=px * 0.999, high=px * 1.02, low=px * 0.998, close=px * 1.015
    )
    desk.run_daily(SESSIONS[i + 1])
    entries, summary = build_journal(desk.db, desk.service.store)
    by = {e.signal.symbol: e for e in entries}
    held = by[taken.symbol]
    assert held.stage == "open" and held.entry_date == SESSIONS[i + 1] and not held.hypothetical
    assert held.return_pct == pytest.approx(held.last_price / held.entry_price - 1)
    assert held.r_multiple is not None and summary.taken == 1 and summary.closed == 0
    skipped = by[passed.symbol]
    assert skipped.hypothetical and skipped.return_pct is not None and summary.skipped == 1


def test_unfilled_order_shows_as_not_filled(desk):  # noqa: F811
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    i = SESSIONS.index(DAY0)
    px = sig.entry_price
    set_bar(desk, sig.symbol, SESSIONS[i + 1], open=px * 1.05, high=px * 1.06, low=px * 1.04, close=px * 1.05)
    desk.run_daily(SESSIONS[i + 1])
    desk.run_daily(SESSIONS[i + 2])
    [entry] = [e for e in build_journal(desk.db, desk.service.store)[0] if e.signal.id == sig.id]
    assert entry.stage == "not filled" and entry.order_status == "EXPIRED"
