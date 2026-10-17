"""End-to-end tests for the trading desk on a paper account (synthetic prices, real NSE calendar)."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from tests.conftest import REPO_CONFIG
from tests.helpers import make_bars
from trademare.config import Secrets, load_config
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.data.fields import normalize_financials
from trademare.data.service import DataService
from trademare.execution.brokers import base as broker_base
from trademare.storage.db import Database, EquityRow, MarketStateRow, OrderRow, PositionRow, SignalRow
from trademare.trading.desk import DeskError, TradingDesk

CLOCK = MarketClock(Market.IN)
# First session of June 2025 is a monthly rebalance day.
DAY0 = CLOCK.next_session(date(2025, 5, 31))
SESSIONS = [
    ts.date()
    for ts in CLOCK.calendar.sessions_in_range(pd.Timestamp("2023-01-02"), pd.Timestamp("2025-07-31"))
]


def on_sessions(bars: pd.DataFrame) -> pd.DataFrame:
    bars = bars.iloc[-len(SESSIONS) :].copy()
    bars.index = pd.DatetimeIndex([pd.Timestamp(d) for d in SESSIONS], name="date")
    return bars


@pytest.fixture
def desk(tmp_path, monkeypatch):
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True)
    symbols = [f"S{i}" for i in range(10)]
    (universes / "nifty500.csv").write_text(
        "symbol,name,sector,cik\n"
        + "\n".join(f"{s},{s} Ltd,Sector{i % 5}," for i, s in enumerate(symbols))
        + "\n"
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "data" / "cache")
    india = config.markets[Market.IN]
    india.universe, india.factors, india.universe_top_n, india.min_avg_traded_value = (
        "nifty500",
        ["mom_12_1"],
        None,
        0,
    )
    india.budget, india.slippage_bps = 1_000_000, 0
    service = DataService(config, Secrets(_env_file=None))
    n = len(SESSIONS)
    service.store.write_bars(Market.IN, "^NSEI", on_sessions(make_bars(n, drift=0.0005, seed=99)))
    for i, s in enumerate(symbols):
        bars = on_sessions(make_bars(n, drift=0.003 - 0.0006 * i, vol=0.004, seed=i))
        service.store.write_bars(Market.IN, s, bars)
        service.store.write_financials(Market.IN, "yfinance", s, normalize_financials(pd.DataFrame()))
    db = Database(f"sqlite:///{tmp_path / 'trade.db'}")
    return TradingDesk(service, db, Market.IN, Secrets(_env_file=None))


def set_bar(desk: TradingDesk, symbol: str, day: date, **ohlc: float) -> None:
    bars = desk.service.store.read_bars(Market.IN, symbol)
    for k, v in ohlc.items():
        bars.loc[pd.Timestamp(day), k] = v
    desk.service.store.write_bars(Market.IN, symbol, bars)


def entry_signals(desk: TradingDesk) -> list[SignalRow]:
    with desk.db.session() as s:
        return list(s.query(SignalRow).filter_by(action="ENTRY").order_by(SignalRow.score.desc()))


def test_daily_creates_sized_entry_signals(desk):
    report = desk.run_daily(DAY0)
    assert report.rebalance_day and report.processed == [DAY0]
    sigs = entry_signals(desk)
    assert sigs and sigs[0].symbol == "S0"
    assert all(sig.quantity > 0 and sig.stop_price < sig.entry_price for sig in sigs)
    # Idempotent: a second run for the same day creates nothing new.
    desk.run_daily(DAY0)
    assert len(entry_signals(desk)) == len(sigs)


def test_approve_fill_then_stop_out(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    order = desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    assert order.status == "OPEN" and order.order_type == "LIMIT"

    day1, day2 = CLOCK.next_session(DAY0), CLOCK.next_session(CLOCK.next_session(DAY0))
    limit = sig.entry_price
    # Opens just under the limit and stays well above the stop.
    set_bar(desk, "S0", day1, open=limit * 0.999, high=limit * 1.002, low=limit * 0.998, close=limit * 1.001)
    report = desk.run_daily(day1, generate=False)
    assert any("S0: LONG" in f for f in report.fills)
    with desk.db.session() as s:
        pos = s.query(PositionRow).one()
        assert pos.entry_price == pytest.approx(limit * 0.999) and pos.status == "open"
        stops = s.query(OrderRow).filter_by(position_id=pos.id, purpose="stop", status="OPEN").all()
        assert len(stops) == 1
        cash = s.get(MarketStateRow, "IN").cash
        assert cash < 1_000_000 - pos.quantity * pos.entry_price + 1  # paid for shares + costs

    stop = sig.stop_price
    set_bar(desk, "S0", day2, open=stop * 1.01, high=stop * 1.02, low=stop * 0.95, close=stop * 0.97)
    report = desk.run_daily(day2, generate=False)
    with desk.db.session() as s:
        pos = s.query(PositionRow).one()
        assert pos.status == "closed" and pos.exit_reason == "stop" and pos.exit_price == pytest.approx(stop)
        assert pos.realized_pnl < 0
        assert s.query(OrderRow).filter_by(position_id=pos.id, status="OPEN").count() == 0
        equity = s.get(EquityRow, ("IN", day2)).equity
    assert equity == pytest.approx(1_000_000 + pos.realized_pnl)


def test_unfilled_entry_expires(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    day1 = CLOCK.next_session(DAY0)
    set_bar(
        desk,
        "S0",
        day1,
        open=sig.entry_price * 1.05,
        high=sig.entry_price * 1.06,
        low=sig.entry_price * 1.04,
        close=sig.entry_price * 1.05,
    )  # gaps up and never returns
    report = desk.run_daily(day1, generate=False)
    assert any("expired" in n for n in report.notes)
    with desk.db.session() as s:
        assert s.query(PositionRow).count() == 0


def test_catch_up_processes_missed_sessions(desk):
    desk.run_daily(DAY0)
    later = SESSIONS[SESSIONS.index(DAY0) + 4]
    report = desk.run_daily(later, generate=False)
    assert report.processed == SESSIONS[SESSIONS.index(DAY0) + 1 : SESSIONS.index(DAY0) + 5]


def test_expired_signal_cannot_be_approved(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    with pytest.raises(DeskError, match="expired"):
        desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC) + timedelta(days=10))


def test_kill_switch_blocks_entries_until_rearmed(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    with desk.db.session() as s:
        s.get(MarketStateRow, "IN").peak_equity = 5_000_000  # equity is now 80% below peak
    # "now" before the signal's expiry (close of the next session)
    report = desk.run_daily(CLOCK.next_session(DAY0), generate=False, now=datetime(2025, 6, 3, 5, tzinfo=UTC))
    assert report.kill_switch and "drawdown" in report.kill_reason
    with pytest.raises(DeskError, match="kill switch"):
        desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))
    desk.rearm()
    with desk.db.session() as s:
        assert not s.get(MarketStateRow, "IN").kill_switch


def test_live_orders_blocked_when_unvalidated(desk, monkeypatch):
    class FakeLive(broker_base.BrokerAdapter):
        name = "dhan"
        markets = frozenset({Market.IN})

        def connect(self): ...
        def place_order(self, order):
            raise AssertionError("must not place a live order")

        def cancel_order(self, broker_order_id): ...
        def get_order(self, broker_order_id): ...
        def get_positions(self):
            return []

        def get_funds(self): ...
        def get_ltp(self, symbols):
            return {}

    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    monkeypatch.setitem(broker_base._REGISTRY, "dhan", FakeLive)
    cfg = desk.cfg.model_copy(update={"broker": "dhan", "validated": False})
    desk.broker = FakeLive(Market.IN, cfg, Secrets(_env_file=None))
    with pytest.raises(DeskError, match="not validated"):
        desk.approve(sig.id, now=datetime(2025, 6, 2, tzinfo=UTC))


def test_reject(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    desk.reject(sig.id, "not today")
    with desk.db.session() as s:
        assert s.get(SignalRow, sig.id).status == "REJECTED"
    with pytest.raises(DeskError):
        desk.reject(sig.id)


def test_first_run_builds_portfolio_off_rebalance_day(desk):
    mid_month = CLOCK.next_session(date(2025, 6, 15))
    report = desk.run_daily(mid_month)
    assert report.rebalance_day and entry_signals(desk)
    # Later mid-month days are not rebalance days.
    report = desk.run_daily(CLOCK.next_session(mid_month))
    assert not report.rebalance_day


def test_signal_expiry_is_stored_in_utc(desk):
    desk.run_daily(DAY0)
    sig = entry_signals(desk)[0]
    close = CLOCK.session_close(CLOCK.next_session(DAY0)).astimezone(UTC)  # 15:30 IST = 10:00 UTC
    assert sig.expires_at.replace(tzinfo=UTC) == close
    assert sig.expires_at.hour == 10
