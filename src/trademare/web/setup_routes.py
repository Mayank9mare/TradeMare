"""Setups page: pick chart setups from "51 Trading Strategies", see their record and today's matches."""

from __future__ import annotations

import logging
import secrets as pysecrets
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from trademare.config import load_config
from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.strategy.setup_study import PERIODS, current_record, load_record, rules_version
from trademare.strategy.setups import FAMILIES, SETUPS
from trademare.web.settings_form import save_setups

log = logging.getLogger(__name__)


def register_setup_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    config: AppConfig,
    service: DataService,
    config_path: str | Path | None = None,
) -> None:
    running: dict[Market, threading.Thread] = {}
    lock = threading.Lock()

    def data_date(market: Market) -> date | None:
        cfg = config.markets[market]
        bars = service.store.read_bars(market, cfg.benchmark) if cfg.benchmark else None
        return None if bars is None or bars.empty else bars.index[-1].date()

    def refresh_in_background(market: Market) -> None:
        """Start recomputing the record unless a run for this market is already going."""
        with lock:
            thread = running.get(market)
            if thread is not None and thread.is_alive():
                return

            def work() -> None:
                try:
                    current_record(service, market)
                except Exception:
                    log.exception("setup study for %s failed", market.value)

            thread = threading.Thread(target=work, name=f"setup-study-{market.value}", daemon=True)
            running[market] = thread
            thread.start()

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
    def setups_page(
        request: Request,
        code: str,
        s: list[str] = Query(default=[]),  # noqa: B008
        msg: str | None = None,
        err: str | None = None,
    ):
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
        traded = load_config(config_path).markets[market].setups  # fresh: it is saved from this page
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
                "msg": msg,
                "err": err,
            },
        )

    @app.post("/setups/{code}/refresh")
    def setups_refresh(code: str):
        market = market_or_404(code)
        refresh_in_background(market)
        return RedirectResponse(f"/setups/{market.value}", status_code=303)

    @app.post("/setups/{code}/trading")
    async def setups_trading(request: Request, code: str):
        market = market_or_404(code)
        form = await request.form()
        if not pysecrets.compare_digest(str(form.get("csrf_token", "")), templates.env.globals["csrf_token"]):
            raise HTTPException(403, "invalid form token; reload the page and try again")
        values: dict = {k: form.get(k) for k in ("enabled", "budget_pct", "max_positions", "require_record")}
        values["selected"] = [x for x in form.getlist("selected") if x in SETUPS]
        block, errors = save_setups(market.value, values, config_path)
        if errors:
            return RedirectResponse(f"/setups/{market.value}?err={quote('; '.join(errors))}#trading", 303)
        if block["enabled"]:
            names = ", ".join(SETUPS[x].name for x in block["selected"])
            text = f"Saved. From the next daily run these setups can propose paper trades: {names}."
        else:
            text = "Saved. Setups are analysis only on this market."
        return RedirectResponse(f"/setups/{market.value}?msg={quote(text)}#trading", 303)
