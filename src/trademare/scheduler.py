"""Runs the daily pipeline for each enabled market at its configured local time.

Used by `trademare serve` (dashboard + scheduler in one process). Weekend runs
are skipped by the cron trigger; market holidays are harmless because the desk
always processes the last completed session and is idempotent. On start-up,
a market whose last processed session is behind is caught up immediately.
"""

from __future__ import annotations

import logging
import threading

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
