"""JSON endpoints for the React app at /app (frontend/). Read-only; they reuse the classic
dashboard's data helpers so both show the same numbers."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI
from sqlalchemy import select

from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.storage.db import Database, EquityRow
from trademare.strategy.scanner import load_latest_scan
from trademare.web.overview_data import (
    account_summary,
    ipo_cards_cached,
    pending_signals,
    today_tiles,
    working_entries,
    zerodha_summary,
)

NAMES = {Market.IN: "India", Market.US: "United States"}
INDEX = {Market.IN: "Nifty 50", Market.US: "S&P 500"}
CURRENCY = {Market.IN: "INR", Market.US: "USD"}


def _points(series: pd.Series) -> list[dict]:
    s = series.dropna()
    return [{"t": pd.Timestamp(i).strftime("%Y-%m-%d"), "v": round(float(v), 2)} for i, v in s.items()]


def register_api_routes(app: FastAPI, config: AppConfig, service: DataService, db: Database) -> None:
    @app.get("/api/overview")
    def overview_api() -> dict:
        pending = pending_signals(db)
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        zerodha = zerodha_summary(config)
        tiles, upcoming = today_tiles(
            config, db, pending, service.events(), zerodha, ipo_cards_cached(config, today), today
        )
        markets = []
        for market, cfg in config.enabled_markets().items():
            account = account_summary(db, market, cfg.budget)
            with db.session() as s:
                rows = list(
                    s.scalars(
                        select(EquityRow).where(EquityRow.market == market.value).order_by(EquityRow.day)
                    )
                )
            equity = pd.Series([r.equity for r in rows], index=pd.to_datetime([r.day for r in rows]))
            index_line: list[dict] = []
            if cfg.benchmark and (bench := service.store.read_bars(market, cfg.benchmark)) is not None:
                index_line = _points(bench["close"].iloc[-126:])
            try:
                scan = load_latest_scan(service, market)
            except Exception:
                scan = None
            regime = scan.regime.label.value if scan else None
            markets.append(
                {
                    "code": market.value,
                    "name": NAMES[market],
                    "currency": CURRENCY[market],
                    "equity": account.equity,
                    "previous_equity": account.previous_equity,
                    "cash": account.cash,
                    "peak": account.peak,
                    "positions": account.positions,
                    "max_positions": cfg.max_open_positions,
                    "kill_switch": account.kill_switch,
                    "kill_reason": account.kill_reason,
                    "last_processed": account.last_processed.isoformat() if account.last_processed else None,
                    "pending": sum(1 for p in pending if p.market == market.value),
                    "mood": regime,
                    "index_name": INDEX[market],
                    "equity_line": _points(equity),
                    "index_line": index_line,
                    "scan_date": scan.asof.isoformat() if scan else None,
                    "ideas": [
                        {
                            "symbol": i.symbol,
                            "sector": i.sector,
                            "entry": i.levels.entry,
                            "stop": i.levels.stop,
                            "score": i.composite,
                        }
                        for i in (scan.ideas[:5] if scan else [])
                    ],
                }
            )
        orders = [
            {
                "status": w["status"],
                "text": w["text"],
                "symbol": w["order"].symbol,
                "market": w["order"].market,
            }
            for w in working_entries(db, service.store, days=7)
        ]
        return {
            "today": today.isoformat(),
            "pending_total": len(pending),
            "tiles": [asdict(t) for t in tiles],
            "upcoming": [{**asdict(u), "day": u.day.isoformat()} for u in upcoming],
            "zerodha": None
            if zerodha is None
            else {
                **{k: v for k, v in zerodha.items() if k != "as_of"},
                "as_of": zerodha["as_of"].isoformat(),
            },
            "markets": markets,
            "orders": orders,
        }
