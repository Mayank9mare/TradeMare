from datetime import UTC, datetime, timedelta

from tests.test_desk import DAY0, SESSIONS, desk, entry_signals  # noqa: F401  (fixture)
from trademare.config.schema import EventAction
from trademare.data.events import Announcement, CorporateEvents, EventsService
from trademare.storage.db import EventRow


def stub_events(monkeypatch, results_in_sessions=None, flag=None, symbols=None):
    """Every (or the given) symbol reports results N sessions after the asked day, plus an optional filing."""

    def get(self, market, symbol, today=None, refresh=False):
        ev = CorporateEvents(market.value, symbol, datetime.now(UTC))
        if symbols is not None and symbol not in symbols:
            ev.announcements = [Announcement(today, "Press Release", "routine", "NSE")]
            return ev
        if results_in_sessions is not None:
            day = today
            for _ in range(results_in_sessions):
                day = SESSIONS[SESSIONS.index(day) + 1]
            ev.next_results, ev.results_source = day, "NSE board meeting"
        ev.announcements = [flag(today)] if flag else [Announcement(today, "Press Release", "routine", "NSE")]
        return ev

    monkeypatch.setattr(EventsService, "get", get)


def test_results_soon_warns_by_default(desk, monkeypatch):  # noqa: F811
    stub_events(monkeypatch, results_in_sessions=3)
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    assert sig.evidence["events"]["results_soon"] and sig.evidence["events"]["sessions_to_results"] == 3
    assert sig.reasons["events"][0].startswith("Results due")


def test_results_far_away_is_quiet(desk, monkeypatch):  # noqa: F811
    stub_events(monkeypatch, results_in_sessions=20)
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    assert not sig.evidence["events"]["results_soon"] and "events" not in sig.reasons


def test_skip_settings_drop_entries(desk, monkeypatch):  # noqa: F811
    stub_events(monkeypatch, results_in_sessions=2)
    desk.config.events.results_action = EventAction.SKIP
    report = desk.run_daily(DAY0)
    assert entry_signals(desk) == []
    assert any("entry skipped (results due" in n for n in report.notes)
    with desk.db.session() as s:
        assert s.query(EventRow).filter_by(kind="event_skip").count() >= 1


def test_red_flag_skip(desk, monkeypatch):  # noqa: F811
    stub_events(
        monkeypatch,
        flag=lambda d: Announcement(d, "Fraud/Default/Arrest", "arrest of MD", "NSE"),
        symbols={"S0"},
    )
    desk.config.events.red_flag_action = EventAction.SKIP
    desk.run_daily(DAY0)
    symbols = {s.symbol for s in entry_signals(desk)}
    assert "S0" not in symbols and symbols  # only the flagged name is dropped


def test_new_filing_on_a_holding_alerts_once(desk, monkeypatch):  # noqa: F811
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    i = SESSIONS.index(DAY0)
    day1, day2, day3 = SESSIONS[i + 1 : i + 4]
    from tests.test_desk import set_bar

    px = sig.entry_price
    for d in (day1, day2, day3):
        set_bar(desk, sig.symbol, d, open=px * 0.999, high=px * 1.01, low=px * 0.99, close=px)
    desk.run_daily(day1)
    stub_events(
        monkeypatch,
        results_in_sessions=1,
        flag=lambda d: Announcement(day2, "Credit Rating", "rating downgraded", "NSE"),
    )
    report = desk.run_daily(day2)
    assert any(n.startswith(f"WARNING {sig.symbol} (held): Credit rating downgrade") for n in report.notes)
    assert any(n.startswith(f"HEADS-UP {sig.symbol} (held): results due") for n in report.notes)
    later = desk.run_daily(day3)  # the same filing is not reported again
    assert not any("Credit rating downgrade" in n for n in later.notes)
    assert day3 - day2 < timedelta(days=5)
