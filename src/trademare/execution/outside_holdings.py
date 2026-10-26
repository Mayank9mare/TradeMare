"""Stocks you hold at your broker that TradeMare did not buy (read-only).

TradeMare never trades these: no stops, no exits, no orders. It only shows them next to its own
analysis (rank, trend, upcoming results) so you can see your whole portfolio in one place. The
snapshot is taken with the broker's read-only holdings call whenever a broker session is active
(the daily run tries; `trademare broker holdings` forces it) and kept in the cache directory.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.storage.db import Database, PositionRow

log = logging.getLogger(__name__)


def snapshot_path(config: AppConfig, market: Market = Market.IN) -> Path:
    return Path(config.data.cache_dir) / "broker" / f"{market.value}-outside-holdings.json"


def own_quantities(db: Database, market: Market) -> dict[str, int]:
    """Shares of each stock held by TradeMare's own (live) positions."""
    with db.session() as s:
        rows = s.scalars(
            select(PositionRow).where(PositionRow.market == market.value, PositionRow.status == "open")
        )
        out: dict[str, int] = {}
        for p in rows:
            out[p.symbol] = out.get(p.symbol, 0) + p.quantity
        return out


def sync(config: AppConfig, secrets, db: Database, market: Market = Market.IN) -> dict[str, Any]:
    """Fetch holdings from Zerodha and save the ones TradeMare does not manage. Raises on failure."""
    from trademare.execution.brokers.zerodha import ZerodhaBroker

    broker = ZerodhaBroker(market, config.markets[market], secrets)
    holdings = broker.get_holdings_detail()
    mine = own_quantities(db, market) if config.markets[market].broker == "zerodha" else {}
    outside = []
    for h in holdings:
        qty = h["quantity"] - mine.get(h["symbol"], 0)  # TradeMare's own shares are shown with its positions
        if qty > 0:
            outside.append(h | {"quantity": qty})
    snap = {"broker": "zerodha", "fetched_at": datetime.now(UTC).isoformat(), "holdings": outside}
    path = snapshot_path(config, market)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1))
    return snap


def try_sync(config: AppConfig, secrets, db: Database, market: Market = Market.IN) -> str:
    """Sync when a Zerodha session is available; never raises (used by the daily run)."""
    if not (secrets.zerodha_api_key and secrets.zerodha_api_secret):
        return "no Zerodha keys"
    try:
        snap = sync(config, secrets, db, market)
        return f"{len(snap['holdings'])} outside holding(s) synced"
    except Exception as e:  # expired login, network: keep the previous snapshot
        log.info("outside holdings not synced: %s", e)
        return f"not synced: {e}"


def load(config: AppConfig, market: Market = Market.IN) -> dict[str, Any] | None:
    try:
        return json.loads(snapshot_path(config, market).read_text())
    except (OSError, ValueError):
        return None


def view(config: AppConfig, service, market: Market = Market.IN) -> dict[str, Any] | None:
    """Rows for the dashboard and the brief: P&L plus TradeMare's view of each stock."""
    snap = load(config, market)
    if not snap:
        return None
    from trademare.strategy.scanner import load_latest_scan

    try:
        scan = load_latest_scan(service, market)
    except Exception:
        scan = None
    ranked = sorted(
        (s for s in (scan.scores if scan else []) if s.composite is not None), key=lambda s: -s.composite
    )
    rank = {s.symbol: (i + 1, s.composite) for i, s in enumerate(ranked)}
    events = service.events()
    fetched = datetime.fromisoformat(snap["fetched_at"])
    rows = []
    for h in snap["holdings"]:
        sym = h["symbol"]
        price, price_day = h["last_price"], fetched.date()
        bars = service.store.read_bars(market, sym)
        trend = None
        if bars is not None and len(bars):
            last_day = bars.index[-1].date()
            if last_day >= price_day:  # the daily cache is newer than the broker snapshot
                price, price_day = float(bars["close"].iloc[-1]), last_day
            if len(bars) >= 200:
                trend = bool(bars["close"].iloc[-1] > bars["close"].rolling(200).mean().iloc[-1])
        cached = events.cached(market, sym)
        nxt = cached.next_results if cached else None
        invested = h["quantity"] * h["average_price"]
        value = h["quantity"] * price
        r = rank.get(sym)
        rows.append({
            "symbol": sym,
            "quantity": h["quantity"],
            "average_price": h["average_price"],
            "price": price,
            "price_day": price_day,
            "invested": invested,
            "value": value,
            "pnl": value - invested,
            "return": value / invested - 1 if invested else None,
            "rank": r[0] if r else None,
            "ranked_of": len(ranked) or None,
            "score": r[1] if r else None,
            "above_200d": trend,
            "next_results": nxt,
            "results_soon": bool(nxt and 0 <= (pd.Timestamp(nxt).date() - date.today()).days <= 7),
        })  # fmt: skip
    rows.sort(key=lambda r: -r["value"])
    invested = sum(r["invested"] for r in rows)
    value = sum(r["value"] for r in rows)
    return {
        "broker": snap["broker"],
        "fetched_at": fetched,
        "rows": rows,
        "invested": invested,
        "value": value,
        "pnl": value - invested,
        "return": value / invested - 1 if invested else None,
    }
