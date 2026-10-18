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
    assert set(jobs) == {"daily-IN", "daily-US", "backup", "health"}
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
