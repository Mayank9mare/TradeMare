"""TradeMare dashboard (FastAPI + Jinja). Local-only for now: bind to 127.0.0.1."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trademare import __version__
from trademare.analysis.ratios import key_ratios
from trademare.config import Secrets, load_config
from trademare.config.schema import AppConfig
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.data.fields import FIELDS
from trademare.data.service import DataService
from trademare.data.universe import load_universe
from trademare.web import formatting
from trademare.web.snapshot import SnapshotCache

HERE = Path(__file__).parent
STATEMENT_ROWS = [f for f in FIELDS if f != "shares_outstanding"] + ["shares_outstanding"]


def create_app(config: AppConfig | None = None, service: DataService | None = None) -> FastAPI:
    config = config or load_config()
    service = service or DataService(config, Secrets())
    snapshots = SnapshotCache(service)

    app = FastAPI(title="TradeMare", version=__version__, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters.update(money=formatting.money, pct=formatting.pct, num=formatting.num, tone=formatting.tone)
    env.globals.update(version=__version__, markets=list(config.markets))

    def market_or_404(code: str) -> Market:
        try:
            market = Market(code.upper())
        except ValueError:
            raise HTTPException(404, f"unknown market {code}") from None
        if market not in config.markets:
            raise HTTPException(404, f"market {market} not configured")
        return market

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        cards = []
        for market, cfg in config.markets.items():
            universe = load_universe(cfg.universe)
            source = service.market(market).financials.source.source
            bar_symbols = set(service.store.bar_symbols(market))
            with_fin = sum(
                1 for s in universe["symbol"] if service.store.financials_updated_at(market, source, s)
            )
            rows = snapshots.get(market)
            dated = [r for r in rows if r.last_date]
            advancers = sum(1 for r in rows if (r.returns["chg_1d"] or 0) > 0)
            clock = MarketClock(market)
            cards.append(
                dict(
                    market=market,
                    cfg=cfg,
                    universe_size=len(universe),
                    bars=len(bar_symbols & set(universe["symbol"])),
                    fundamentals=with_fin,
                    source=source,
                    last_date=max((r.last_date for r in dated), default=None),
                    advancers=advancers,
                    decliners=sum(1 for r in rows if (r.returns["chg_1d"] or 0) < 0),
                    next_session=_safe(lambda c=clock: c.next_session(date.today())),
                    top=sorted(dated, key=lambda r: r.returns["chg_1d"] or 0, reverse=True)[:5],
                    bottom=sorted(dated, key=lambda r: r.returns["chg_1d"] or 0)[:5],
                )
            )
        return templates.TemplateResponse(request, "overview.html", {"cards": cards, "config": config})

    @app.get("/m/{code}", response_class=HTMLResponse)
    def market_page(request: Request, code: str):
        market = market_or_404(code)
        rows = snapshots.get(market)
        sectors = sorted({r.sector for r in rows if isinstance(r.sector, str)})
        return templates.TemplateResponse(
            request,
            "market.html",
            {"market": market, "rows": rows, "sectors": sectors, "cfg": config.markets[market]},
        )

    @app.get("/m/{code}/{symbol}", response_class=HTMLResponse)
    def symbol_page(request: Request, code: str, symbol: str):
        market = market_or_404(code)
        universe = load_universe(config.markets[market].universe).set_index("symbol")
        bars = service.store.read_bars(market, symbol)
        if bars is None or bars.empty:
            raise HTTPException(404, f"no cached data for {market}:{symbol}")
        financials = service.market(market).financials.get_financials(market, symbol)
        last = bars.index[-1].date()
        close = float(bars["close"].iloc[-1])
        info = universe.loc[symbol] if symbol in universe.index else None
        statements = {
            label: _statement_table(financials, kind, limit)
            for label, kind, limit in (("Quarterly", "Q", 8), ("Annual", "A", 6))
        }
        return templates.TemplateResponse(
            request,
            "symbol.html",
            {
                "market": market,
                "symbol": symbol,
                "name": info["name"] if info is not None else symbol,
                "sector": info["sector"] if info is not None else "",
                "last": last,
                "close": close,
                "change": float(close / bars["close"].iloc[-2] - 1) if len(bars) > 1 else None,
                "ratios": key_ratios(financials, last, close),
                "statements": statements,
                "rows": STATEMENT_ROWS,
                "source": service.market(market).financials.source.source,
            },
        )

    @app.get("/api/bars/{code}/{symbol}")
    def bars_api(code: str, symbol: str, years: float = 3):
        market = market_or_404(code)
        bars = service.store.read_bars(market, symbol)
        if bars is None or bars.empty:
            raise HTTPException(404, f"no cached data for {market}:{symbol}")
        close = bars["close"]
        sma50, sma200 = close.rolling(50).mean(), close.rolling(200).mean()
        start = bars.index[-1] - pd.DateOffset(days=round(365.25 * years))
        window = bars.index >= start
        b = bars[window]
        times = [d.strftime("%Y-%m-%d") for d in b.index]
        return JSONResponse(
            {
                "candles": [
                    {"time": t, "open": o, "high": h, "low": lo, "close": c}
                    for t, o, h, lo, c in zip(times, b["open"], b["high"], b["low"], b["close"], strict=True)
                ],
                "volume": [
                    {"time": t, "value": v, "up": c >= o}
                    for t, v, o, c in zip(times, b["volume"], b["open"], b["close"], strict=True)
                ],
                "sma50": _line(times, sma50[window]),
                "sma200": _line(times, sma200[window]),
            }
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        dumped = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False)
        return templates.TemplateResponse(request, "settings.html", {"config_yaml": dumped})

    return app


def _line(times: list[str], values: pd.Series) -> list[dict]:
    return [{"time": t, "value": v} for t, v in zip(times, values, strict=True) if pd.notna(v)]


def _statement_table(financials: pd.DataFrame, kind: str, limit: int) -> dict:
    rows = financials[financials["period_type"] == kind].sort_values("period_end").tail(limit)
    return {
        "periods": [p.date() for p in rows["period_end"]],
        "available": [a.date() for a in rows["available_from"]],
        "values": {f: [None if pd.isna(v) else float(v) for v in rows[f]] for f in FIELDS},
    }


def _safe(fn):
    try:
        return fn()
    except ValueError:
        return None
