from datetime import UTC, date, datetime

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.core.models import Market
from trademare.notify.email import render_digest, send_digest
from trademare.scheduler import build_scheduler
from trademare.storage.db import SignalRow
from trademare.trading.desk import DailyReport


def test_scheduler_has_one_job_per_market_in_local_time():
    scheduler = build_scheduler(load_config(REPO_CONFIG))
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == {"daily-IN", "daily-US", "backup", "health", "intraday-IN", "intraday-US", "ipo"}
    assert str(jobs["ipo"].trigger.timezone) == "Asia/Kolkata"
    assert str(jobs["daily-IN"].trigger.timezone) == "Asia/Kolkata"
    assert "hour='17'" in str(jobs["daily-IN"].trigger) and "day_of_week='mon-fri'" in str(
        jobs["daily-IN"].trigger
    )
    assert str(jobs["daily-US"].trigger.timezone) == "America/New_York"


def report(**kw) -> DailyReport:
    sig = SignalRow(
        id="x",
        market="IN",
        symbol="M&M",
        direction="LONG",
        action="ENTRY",
        quantity=3,
        entry_price=3000.0,
        stop_price=2800.0,
        score=0.9,
        confidence=1.0,
        reasons={"factor": ["momentum <strong>"]},
        status="PENDING",
        scan_date=date(2025, 6, 2),
        expires_at=datetime(2025, 6, 3, tzinfo=UTC),
    )
    return DailyReport(
        market=Market.IN,
        session=date(2025, 6, 2),
        signals=[sig],
        equity=100_000.0,
        previous_equity=99_000.0,
        cash=50_000.0,
        **kw,
    )


def test_digest_renders_and_escapes():
    subject, html = render_digest([report(kill_switch=True, kill_reason="drawdown -12%")], "http://x")
    assert subject == "TradeMare IN: 1 new signal — KILL SWITCH"
    assert "M&amp;M" in html and "&lt;strong&gt;" in html and "Kill switch active" in html
    assert "http://x/signals" in html


def test_digest_saved_to_outbox_without_smtp(tmp_path):
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "data" / "cache")
    where = send_digest(config, Secrets(_env_file=None), [report()])
    assert where.startswith("saved to") and list((tmp_path / "data" / "outbox").glob("*.html"))
    empty = DailyReport(market=Market.IN, session=date(2025, 6, 2))
    assert send_digest(config, Secrets(_env_file=None), [empty]).startswith("skipped")


def test_manual_run_only_once_per_trading_day(tmp_path, monkeypatch):
    from trademare import scheduler
    from trademare.core.clock import MarketClock
    from trademare.storage.db import Database, MarketStateRow

    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.database_url = f"sqlite:///{tmp_path / 'run.db'}"
    db = Database(config.database_url)
    due = MarketClock(Market.IN).last_completed_session()
    with db.session() as s:
        s.add(MarketStateRow(market="IN", cash=1.0, peak_equity=1.0, last_processed=due))
    assert "already up to date" in scheduler.run_block(Market.IN, config)
    with db.session() as s:
        s.get(MarketStateRow, "IN").last_processed = MarketClock(Market.IN).previous_session(due)
    assert scheduler.run_block(Market.IN, config) is None

    started = []
    monkeypatch.setattr(scheduler, "load_config", lambda path=None: config)
    monkeypatch.setattr(
        scheduler, "run_daily_pipeline", lambda *a, **k: started.append(a) or DailyReport(Market.IN, due)
    )
    monkeypatch.setattr(
        scheduler.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: None})()
    )
    scheduler.manual_runs.clear()
    assert scheduler.start_run(Market.IN) is None
    assert scheduler.start_run(Market.IN) == "a run is already in progress"  # second click while running
    scheduler.manual_runs.clear()
