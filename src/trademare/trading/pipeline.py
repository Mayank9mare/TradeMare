"""The daily pipeline for one market: refresh data, run the desk, send the digest.

Shared by `trademare daily`, the scheduler in `trademare serve`, and the dashboard.
"""

from __future__ import annotations

import logging
import threading

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.notify.email import send_digest
from trademare.notify.phone import send_alerts
from trademare.storage.db import Database
from trademare.trading.desk import DailyReport, TradingDesk

log = logging.getLogger(__name__)

# One run per market at a time (scheduler, CLI and dashboard may overlap).
_locks = {m: threading.Lock() for m in Market}


def database(config: AppConfig) -> Database:
    return Database(config.database_url)


def run_daily_pipeline(
    config: AppConfig,
    market: Market,
    secrets: Secrets | None = None,
    update_data: bool = True,
    notify: bool = True,
    db: Database | None = None,
) -> DailyReport:
    secrets = secrets or Secrets()
    lock = _locks[market]
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"daily run for {market} is already in progress")
    try:
        service = DataService(config, secrets)
        if update_data:
            symbols = service.universe_symbols(market)
            report = service.update_prices(market, symbols)
            log.info("%s prices: %s", market, report.summary())
            report = service.update_fundamentals(market, symbols)
            log.info("%s fundamentals: %s", market, report.summary())
        desk = TradingDesk(service, db or database(config), market, secrets)
        result = desk.run_daily()
        if market is Market.IN:  # your other stocks at Zerodha, shown read-only on the dashboard
            from trademare.execution.outside_holdings import try_sync

            log.info("IN outside holdings: %s", try_sync(config, secrets, db or database(config)))
        if notify:
            where = send_digest(config, secrets, [result], config.notifications.dashboard_url)
            log.info("%s digest: %s", market, where)
            log.info("%s %s", market, send_alerts(config, secrets, [result]))
        return result
    finally:
        lock.release()
