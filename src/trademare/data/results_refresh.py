"""Refresh fundamentals right after companies publish results (India), instead of weekly.

Each daily run asks NSE's market-wide results feed which companies filed since the last check,
then, for those in the universe or that matter to the owner (open positions, pending signals,
stocks held outside TradeMare), re-downloads their NSE filings and figures, Yahoo fundamentals
and corporate events. A stock whose known results date has passed since its last fundamentals
refresh is included too, in case the feed missed it. Never raises: problems are returned.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trademare.core.models import Market

log = logging.getLogger(__name__)

MAX_SYMBOLS = 80  # per run; results season can bring hundreds of filings a day
FIRST_LOOKBACK = timedelta(days=3)


def _state_path(service) -> Path:
    return Path(service.config.data.cache_dir) / "nse_results" / "_last_check.json"


def refresh_after_results(service, market: Market, interest: set[str], now: datetime | None = None) -> dict:
    if market is not Market.IN:
        return {"skipped": "India only"}
    now = now or datetime.now()
    today = now.date()
    nse = service.nse_results()
    state = _state_path(service)
    since = now - FIRST_LOOKBACK
    if state.exists():
        with contextlib.suppress(ValueError, KeyError):
            since = datetime.fromisoformat(json.loads(state.read_text())["checked"])
    out: dict = {"since": since.isoformat(timespec="minutes"), "errors": []}
    try:
        filers = nse.recent_filers(since)
    except Exception as e:  # NSE blocking or format change: fall back to known results dates
        filers = set()
        out["errors"].append(f"results feed: {type(e).__name__}: {e}")
    universe = set(service.universe_symbols(market))
    due = set()
    events = service.events()
    store = service.store
    source = service.market(market).financials.source.source
    for symbol in interest:
        ev = events.cached(market, symbol)
        if ev is None or ev.next_results is None or ev.next_results > today:
            continue
        updated = store.financials_updated_at(market, source, symbol)
        published = datetime.combine(ev.next_results, datetime.min.time(), UTC)
        if updated is None or updated < published:
            due.add(symbol)
    wanted = (filers & (universe | interest)) | due
    targets = sorted(wanted, key=lambda s: (s not in interest, s))[:MAX_SYMBOLS]  # yours first
    out["filers"] = len(filers)
    out["refreshed"] = targets
    for symbol in targets:
        res = nse.get(symbol, refresh=True)
        if res.error:
            out["errors"].append(f"{symbol}: {res.error}")
    if targets:
        report = service.update_fundamentals(market, targets, force=True)
        out["fundamentals"] = report.summary()
        for symbol in set(targets) & interest:
            events.get(market, symbol, today=today, refresh=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"checked": now.isoformat(timespec="seconds")}))
    log.info("%s results refresh: %d filers, %d refreshed", market, len(filers), len(targets))
    return out


def owner_symbols(config, db, market: Market) -> set[str]:
    """Open positions, pending signals and (India) stocks held outside TradeMare."""
    from sqlalchemy import select

    from trademare.storage.db import PositionRow, SignalRow

    symbols: set[str] = set()
    m = market.value
    with db.session() as s:
        open_q = select(PositionRow.symbol).where(PositionRow.market == m, PositionRow.status == "open")
        pending_q = select(SignalRow.symbol).where(SignalRow.market == m, SignalRow.status == "PENDING")
        symbols |= set(s.scalars(open_q)) | set(s.scalars(pending_q))
    if market is Market.IN:
        from trademare.execution.outside_holdings import load

        snap = load(config)
        if snap:
            symbols |= {r["symbol"] for r in snap.get("holdings", []) if r.get("symbol")}
    return symbols
