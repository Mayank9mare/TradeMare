"""Runs the daily pipeline for each enabled market at its configured local time.

Used by `trademare serve` (dashboard + scheduler in one process). Weekend runs
are skipped by the cron trigger; market holidays are harmless because the desk
always processes the last completed session and is idempotent. On start-up,
a market whose last processed session is behind is caught up immediately.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from trademare.config import Secrets, load_config
from trademare.config.schema import AppConfig
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.storage.db import MarketStateRow
from trademare.trading.pipeline import database, run_daily_pipeline

log = logging.getLogger(__name__)


def daily_job(market: Market, config_path: str | None = None) -> None:
    config = load_config(config_path)  # re-read so settings edits apply without a restart
    try:
        report = run_daily_pipeline(config, market, Secrets())
        log.info(
            "%s daily run done: %d signal(s), equity %.2f", market, len(report.signals), report.equity or 0
        )
    except Exception as e:
        log.exception("%s daily run failed", market)
        from trademare.notify.telegram import send_failure

        send_failure(config, Secrets(), market.value, f"{type(e).__name__}: {e}")


# Manual runs started from the dashboard: market -> (started, finished or None, outcome text)
manual_runs: dict[Market, tuple[datetime, datetime | None, str]] = {}
_starting = threading.Lock()


def is_running(market: Market) -> bool:
    from trademare.trading.pipeline import _locks

    return _locks[market].locked()


def run_block(market: Market, config: AppConfig) -> str | None:
    """Why a manual run is not allowed now, or None. A trading day is processed at most once:
    once the latest finished session is done, the button waits for the next one."""
    if is_running(market):
        return "a run is already in progress"
    due = MarketClock(market).last_completed_session()
    with database(config).session() as s:
        state = s.get(MarketStateRow, market.value)
        done = state.last_processed if state else None
    if done is not None and done >= due:
        return f"already up to date for {done:%a %d %b}; the next trading day is processed after it closes"
    return None


def start_run(market: Market, config_path: str | None = None) -> str | None:
    """Start the daily run for one market in the background. Returns why it was refused, or None."""
    config = load_config(config_path)
    with _starting:  # check and start as one step, so two quick clicks cannot both start a run
        current = manual_runs.get(market)
        if current is not None and current[1] is None:
            return "a run is already in progress"
        if (block := run_block(market, config)) is not None:
            return block
        manual_runs[market] = (datetime.now(UTC), None, "running")

    def work() -> None:
        config = load_config(config_path)
        try:
            report = run_daily_pipeline(config, market, Secrets())
            n = len(report.signals)
            if report.processed:
                outcome = f"processed {report.processed[-1]:%a %d %b}: {n} new signal{'s' if n != 1 else ''}"
            else:
                outcome = "nothing new: that day's prices are not published yet, try again later"
        except Exception as e:  # shown on the dashboard; the scheduler's job logs failures the same way
            log.exception("%s manual run failed", market)
            outcome = f"failed: {type(e).__name__}: {e}"
        manual_runs[market] = (manual_runs[market][0], datetime.now(UTC), outcome)

    threading.Thread(target=work, daemon=True, name=f"manual-run-{market.value}").start()
    return None


def backup_job(config_path: str | None = None) -> None:
    from trademare.ops import backup_database

    config = load_config(config_path)
    try:
        backup_database(config)
    except Exception as e:
        log.exception("database backup failed")
        from trademare.notify.telegram import send_text

        send_text(config, Secrets(), f"TradeMare database backup failed: {type(e).__name__}: {e}")


def health_job(config_path: str | None = None) -> None:
    from trademare.ops import alert_health, check_health

    config = load_config(config_path)
    try:
        report = check_health(config)
        if not report.ok:
            log.warning("health check: %s -> %s", report.problems, alert_health(config, Secrets(), report))
    except Exception:
        log.exception("health check failed")


def intraday_job(market: Market, config_path: str | None = None) -> None:
    from trademare.data.service import DataService
    from trademare.notify.telegram import send_text
    from trademare.trading.desk import TradingDesk
    from trademare.trading.pipeline import _locks

    config = load_config(config_path)
    if not _locks[market].acquire(blocking=False):
        return  # the daily run is busy with this market
    try:
        secrets = Secrets()
        desk = TradingDesk(DataService(config, secrets), database(config), market, secrets)
        alerts = desk.intraday_check()
        if alerts:
            log.warning("%s intraday: %s", market, alerts)
            send_text(config, secrets, f"{market.value} intraday stop check\n" + "\n".join(alerts))
    except Exception:
        log.exception("%s intraday check failed", market)
    finally:
        _locks[market].release()


def ipo_job(config_path: str | None = None) -> list[str]:
    """Refresh the Indian IPO calendar and send new alerts (opening soon; closing today)."""
    from sqlalchemy import select

    from trademare.analysis.ipo import build_cards, ipo_alerts
    from trademare.analysis.ipo_study import load_study
    from trademare.data.ipo import IpoService, ist_now
    from trademare.notify.email import send_text_email
    from trademare.notify.telegram import send_text
    from trademare.storage.db import EventRow

    config = load_config(config_path)
    sent: list[str] = []
    try:
        now = ist_now()
        today = now.date()
        service = IpoService(config.data.cache_dir)
        cards = build_cards(service.india(today), today, load_study(config.data.cache_dir))
        db, secrets = database(config), Secrets()
        with db.session() as s:
            done = {
                ev.data.get("key") for ev in s.scalars(select(EventRow).where(EventRow.kind == "ipo_alert"))
            }
            for key, text in ipo_alerts(cards, today, now.hour):
                if key in done:
                    continue
                send_text(config, secrets, text)
                send_text_email(config, secrets, "TradeMare IPO alert", text)
                db.log(s, "ipo_alert", text, Market.IN.value, key=key)
                sent.append(text)
    except Exception:
        log.exception("IPO check failed")
    return sent


def build_scheduler(config: AppConfig, config_path: str | None = None) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    for market, cfg in config.enabled_markets().items():
        hour, minute = (int(x) for x in cfg.daily_run_time.split(":"))
        scheduler.add_job(
            daily_job,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=MarketClock(market).tz),
            args=[market, config_path],
            id=f"daily-{market.value}",
            misfire_grace_time=6 * 3600,  # still run if the Mac woke up within 6 hours
            coalesce=True,
            max_instances=1,
        )
    ops = config.ops
    if ops.intraday_check_minutes:
        for market in config.enabled_markets():
            scheduler.add_job(
                intraday_job,
                CronTrigger(
                    day_of_week="mon-fri", hour="9-16", minute=f"*/{ops.intraday_check_minutes}",
                    timezone=MarketClock(market).tz,
                ),  # the desk itself skips times outside the session (and holidays)
                args=[market, config_path], id=f"intraday-{market.value}",
                misfire_grace_time=300, coalesce=True, max_instances=1,
            )  # fmt: skip
    if ops.ipo_alerts and Market.IN in config.enabled_markets():
        scheduler.add_job(
            ipo_job,
            CronTrigger(day_of_week="mon-fri", hour="10,14", minute=30, timezone=MarketClock(Market.IN).tz),
            args=[config_path], id="ipo", misfire_grace_time=3600, coalesce=True, max_instances=1,
        )  # fmt: skip
    if ops.backup_enabled:
        hour, minute = (int(x) for x in ops.backup_time.split(":"))
        scheduler.add_job(
            backup_job, CronTrigger(hour=hour, minute=minute), args=[config_path], id="backup",
            misfire_grace_time=12 * 3600, coalesce=True, max_instances=1,
        )  # fmt: skip
    if ops.health_check:
        scheduler.add_job(
            health_job, CronTrigger(minute=17), args=[config_path], id="health",
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )  # fmt: skip
    return scheduler


def catch_up(config: AppConfig, config_path: str | None = None) -> list[Market]:
    """Start background runs for markets whose last processed session is behind."""
    db = database(config)
    started = []
    for market in config.enabled_markets():
        with db.session() as s:
            state = s.get(MarketStateRow, market.value)
            last = state.last_processed if state else None
        due = MarketClock(market).last_completed_session()
        if last is not None and last < due:
            threading.Thread(target=daily_job, args=(market, config_path), daemon=True).start()
            started.append(market)
    return started
