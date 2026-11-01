"""Setups page: pick chart setups from "51 Trading Strategies", see their record and today's matches."""

from __future__ import annotations

import logging
import threading
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.strategy.setup_study import PERIODS, current_record, load_record, rules_version
from trademare.strategy.setups import FAMILIES, SETUPS

log = logging.getLogger(__name__)


def register_setup_routes(
    app: FastAPI, templates: Jinja2Templates, config: AppConfig, service: DataService
) -> None:
    running: dict[Market, threading.Thread] = {}
    lock = threading.Lock()

    def data_date(market: Market) -> date | None:
        cfg = config.markets[market]
        bars = service.store.read_bars(market, cfg.benchmark) if cfg.benchmark else None
        return None if bars is None or bars.empty else bars.index[-1].date()

    def refresh_in_background(market: Market) -> bool:
        """Start recomputing the record unless a run is already going; True if one is running."""
        with lock:
            thread = running.get(market)
            if thread is not None and thread.is_alive():
                return True

            def work() -> None:
                try:
                    current_record(service, market)
                except Exception:
                    log.exception("setup study for %s failed", market.value)

            thread = threading.Thread(target=work, name=f"setup-study-{market.value}", daemon=True)
            running[market] = thread
            thread.start()
            return True

    def is_running(market: Market) -> bool:
        thread = running.get(market)
        return thread is not None and thread.is_alive()

    def market_or_404(code: str) -> Market:
        try:
            market = Market(code.upper())
        except ValueError:
            raise HTTPException(404) from None
        if market not in config.markets:
            raise HTTPException(404)
        return market

    @app.get("/setups")
    def setups_home():
        return RedirectResponse(f"/setups/{next(iter(config.markets)).value}", status_code=307)

    @app.get("/setups/{code}", response_class=HTMLResponse)
    def setups_page(request: Request, code: str, s: list[str] = Query(default=[])):  # noqa: B008
        market = market_or_404(code)
        record = load_record(service, market)
        through = data_date(market)
        current = bool(
            record
            and record.get("rules_version") == rules_version()
            and record.get("data_through") == (through.isoformat() if through else None)
            and "matches" in record
        )
        if not current:
            refresh_in_background(market)
        records = (record or {}).get("records", {})
        traded = config.markets[market].setups
        chosen = [x for x in s if x in SETUPS]
        if not chosen:
            chosen = [x for x in traded.selected if x in SETUPS] or [
                x for x, r in records.items() if r.get("passes")
            ]
        matches = [m for m in (record or {}).get("matches", []) if m["setup"] in chosen]
        by_symbol: dict[str, dict] = {}
        for m in sorted(matches, key=lambda m: -m["strength"]):
            row = by_symbol.setdefault(m["symbol"], {**m, "setups": []})
            row["setups"].append(m["setup"])
        groups = [(f, [x for x in SETUPS.values() if x.family == f]) for f in FAMILIES]
        return templates.TemplateResponse(
            request,
            "setups.html",
            {
                "market": market,
                "groups": groups,
                "setups": SETUPS,
                "records": records,
                "periods": PERIODS,
                "chosen": chosen,
                "matches": sorted(by_symbol.values(), key=lambda r: (-len(r["setups"]), r["symbol"])),
                "record": record,
                "current": current,
                "computing": is_running(market),
                "trading": traded,
            },
        )

    @app.post("/setups/{code}/refresh")
    def setups_refresh(code: str):
        market = market_or_404(code)
        refresh_in_background(market)
        return RedirectResponse(f"/setups/{market.value}", status_code=303)
