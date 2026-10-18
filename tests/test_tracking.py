from datetime import UTC, datetime

from tests.test_desk import DAY0, SESSIONS, desk, entry_signals  # noqa: F401  (fixture)
from trademare.core.models import Market
from trademare.storage.db import PositionRow
from trademare.trading.tracking import compare_with_backtest


def test_too_short_before_a_week(desk):  # noqa: F811
    assert compare_with_backtest(desk.service, desk.db, Market.IN).status == "no_history"
    desk.run_daily(DAY0)
    t = compare_with_backtest(desk.service, desk.db, Market.IN)
    assert t.status == "too_short" and t.sessions == 1


def run_week(desk, decide):  # noqa: F811
    desk.run_daily(DAY0)
    for sig in entry_signals(desk):
        decide(sig)
    start = SESSIONS.index(DAY0)
    for day in SESSIONS[start + 1 : start + 8]:
        desk.run_daily(day)
    return compare_with_backtest(desk.service, desk.db, Market.IN)


def test_paper_matches_backtest_when_every_signal_is_approved(desk):  # noqa: F811
    t = run_week(desk, lambda sig: desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC)))
    assert t.status == "ok" and t.sessions == 8
    with desk.db.session() as s:
        held = {p.symbol for p in s.query(PositionRow)}
    assert held and {m.symbol for m in t.matched} == held  # same fills, including unfilled limits
    assert all(m.paper_date == m.backtest_date and abs(m.price_gap) < 1e-9 for m in t.matched)
    assert t.decisions == [] and t.unexplained == []
    assert abs(t.gap) < 1e-5  # rounding only


def test_rejected_signals_are_explained_as_your_decisions(desk):  # noqa: F811
    t = run_week(desk, lambda sig: desk.reject(sig.id))
    assert t.matched == [] and t.unexplained == []
    assert t.decisions and all(m.note == "you rejected the signal" for m in t.decisions)
    assert t.backtest_return != t.paper_return
