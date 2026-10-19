"""TradeMare dashboard (FastAPI + Jinja). Local-only for now: bind to 127.0.0.1."""

from __future__ import annotations

import secrets as pysecrets
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import pandas as pd
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trademare import __version__
from trademare.analysis.ratios import key_ratios
from trademare.backtest.report import list_backtests, load_backtest
from trademare.config import Secrets, load_config
from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.data.fields import FIELDS
from trademare.data.service import DataService
from trademare.data.universe import load_universe
from trademare.storage.db import Database
from trademare.strategy.scanner import ScanResult, load_latest_scan, scan_dir
from trademare.web import formatting
from trademare.web.glossary import tip
from trademare.web.ipo_routes import register_ipo_routes
from trademare.web.overview_data import (
    account_summary,
    market_status,
    pending_signals,
    sparkline,
    working_entries,
)
from trademare.web.settings_form import GLOBAL_FIELDS, MARKET_FIELDS, apply_form, current_values
from trademare.web.snapshot import SnapshotCache
from trademare.web.trading_routes import register_trading_routes

HERE = Path(__file__).parent
STATEMENT_ROWS = [f for f in FIELDS if f != "shares_outstanding"] + ["shares_outstanding"]


def create_app(
    config: AppConfig | None = None,
    service: DataService | None = None,
    db: Database | None = None,
    secrets: Secrets | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    config = config or load_config(config_path)
    secrets = secrets or Secrets()
    service = service or DataService(config, secrets)
    db = db or Database(config.database_url)
    snapshots = SnapshotCache(service)

    app = FastAPI(
        title="TradeMare",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(basic_auth(secrets))],
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters.update(
        money=formatting.money,
        pct=formatting.pct,
        num=formatting.num,
        tone=formatting.tone,
        metric=formatting.metric,
        local_time=formatting.local_time,
        run_label=formatting.run_label,
    )
    # Cache-busting: static URLs carry the files' modification time, so browsers fetch new versions.
    asset_v = max(int(f.stat().st_mtime) for f in (HERE / "static").iterdir())
    env.globals.update(version=__version__, markets=list(config.markets), asset_v=asset_v, tip=tip)

    register_trading_routes(app, templates, config, service, db, secrets)
    register_ipo_routes(app, templates, config, db, secrets)
    scan_cache: dict[Market, tuple[float, ScanResult | None]] = {}

    def latest_scan(market: Market) -> ScanResult | None:
        """Most recent saved scan, re-read only when a newer one is written."""
        folder = scan_dir(service, market)
        version = folder.stat().st_mtime if folder.exists() else 0.0
        cached = scan_cache.get(market)
        if cached is None or cached[0] != version:
            scan_cache[market] = (version, load_latest_scan(service, market))
        return scan_cache[market][1]

    def market_or_404(code: str) -> Market:
        try:
            market = Market(code.upper())
        except ValueError:
            raise HTTPException(404, f"unknown market {code}") from None
        if market not in config.markets:
            raise HTTPException(404, f"market {market} not configured")
        return market

    def chrome() -> dict:
        """Context every page needs: market status bar and the pending-signal badge."""
        return {
            "statuses": [market_status(m, c) for m, c in config.markets.items() if c.enabled],
            "pending_total": len(pending_signals(db)),
        }

    templates.env.globals["chrome"] = chrome

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        pending = pending_signals(db)
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
            scan = latest_scan(market)
            index_line = None
            if cfg.benchmark and (bench := service.store.read_bars(market, cfg.benchmark)) is not None:
                index_line = sparkline(bench["close"].iloc[-126:])
            account = account_summary(db, market, cfg.budget)
            cards.append(
                dict(
                    market=market,
                    cfg=cfg,
                    account=account,
                    status=market_status(market, cfg),
                    index_line=index_line,
                    universe_size=len(universe),
                    bars=len(bar_symbols & set(universe["symbol"])),
                    fundamentals=with_fin,
                    source=source,
                    last_date=max((r.last_date for r in dated), default=None),
                    advancers=sum(1 for r in rows if (r.returns["chg_1d"] or 0) > 0),
                    decliners=sum(1 for r in rows if (r.returns["chg_1d"] or 0) < 0),
                    top=sorted(dated, key=lambda r: r.returns["chg_1d"] or 0, reverse=True)[:5],
                    bottom=sorted(dated, key=lambda r: r.returns["chg_1d"] or 0)[:5],
                    scan=scan,
                    ideas=(scan.ideas[:5] if scan else []),
                    pending=[p for p in pending if p.market == market.value],
                )
            )
        first_expiry = min((p for p in pending if p.expires_at), key=lambda p: p.expires_at, default=None)
        return templates.TemplateResponse(
            request,
            "overview.html",
            {
                "cards": cards,
                "config": config,
                "pending": pending,
                "first_expiry": first_expiry,
                "working": working_entries(db, service.store),
            },
        )

    @app.get("/m/{code}", response_class=HTMLResponse)
    def market_page(request: Request, code: str):
        market = market_or_404(code)
        rows = snapshots.get(market)
        sectors = sorted({r.sector for r in rows if isinstance(r.sector, str)})
        scan = latest_scan(market)
        scores = {s.symbol: s.composite for s in scan.scores} if scan else {}
        ideas = {i.symbol: i.direction.value for i in scan.ideas} if scan else {}
        return templates.TemplateResponse(
            request,
            "market.html",
            {
                "market": market,
                "rows": rows,
                "sectors": sectors,
                "cfg": config.markets[market],
                "scores": scores,
                "ideas": ideas,
                "scan": scan,
            },
        )

    # Registered before /m/{code}/{symbol} so "ideas" is not taken as a symbol.
    @app.get("/m/{code}/ideas", response_class=HTMLResponse)
    def ideas_page(request: Request, code: str):
        market = market_or_404(code)
        return templates.TemplateResponse(
            request,
            "ideas.html",
            {
                "market": market,
                "scan": latest_scan(market),
                "cfg": config.markets[market],
                "config_min": config.strategy.min_composite_score,
                "penalty": config.strategy.regime_penalty,
            },
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
        scan = latest_scan(market)
        score = next((x for x in scan.scores if x.symbol == symbol), None) if scan else None
        idea = next((x for x in scan.ideas if x.symbol == symbol), None) if scan else None
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
                "scan": scan,
                "score": score,
                "idea": idea,
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

    @app.get("/backtests", response_class=HTMLResponse)
    def backtests_page(request: Request):
        return templates.TemplateResponse(request, "backtests.html", {"runs": list_backtests(config)})

    @app.get("/backtests/{run_id}", response_class=HTMLResponse)
    def backtest_page(request: Request, run_id: str):
        loaded = load_backtest(config, run_id)
        if loaded is None:
            raise HTTPException(404, f"no backtest {run_id}")
        meta, _, trades = loaded
        trades = trades.sort_values("exit_date", ascending=False) if len(trades) else trades
        return templates.TemplateResponse(
            request,
            "backtest.html",
            {"run": meta, "market": Market(meta["market"]), "trades": trades.to_dict("records")},
        )

    @app.get("/api/backtests/{run_id}")
    def backtest_api(run_id: str):
        loaded = load_backtest(config, run_id)
        if loaded is None:
            raise HTTPException(404, f"no backtest {run_id}")
        _, curves, _ = loaded
        times = [d.strftime("%Y-%m-%d") for d in curves.index]
        equity = curves["equity"]
        drawdown = equity / equity.cummax() - 1
        payload = {
            "equity": _line(times, equity),
            "drawdown": _line(times, drawdown * 100),
        }
        if "benchmark" in curves:
            payload["benchmark"] = _line(times, curves["benchmark"])
        return JSONResponse(payload)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: str | None = None):
        on_disk = load_config(config_path)  # show what is on disk, including unapplied edits
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "config_yaml": yaml.safe_dump(on_disk.model_dump(mode="json"), sort_keys=False),
                "values": current_values(on_disk),
                "market_fields": MARKET_FIELDS,
                "global_fields": GLOBAL_FIELDS,
                "market_codes": list(on_disk.markets),
                "on_disk": on_disk,
                "saved": saved,
                "errors": [],
            },
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def save_settings(request: Request):
        form = dict((await request.form()).items())
        if not pysecrets.compare_digest(str(form.get("csrf_token", "")), templates.env.globals["csrf_token"]):
            raise HTTPException(403, "invalid form token; reload the page and try again")
        changes, errors = apply_form(form, config_path)
        if errors:
            on_disk = load_config(config_path)
            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "config_yaml": yaml.safe_dump(on_disk.model_dump(mode="json"), sort_keys=False),
                    "values": {**current_values(on_disk), **form},
                    "market_fields": MARKET_FIELDS,
                    "global_fields": GLOBAL_FIELDS,
                    "market_codes": list(on_disk.markets),
                    "on_disk": on_disk,
                    "saved": None,
                    "errors": errors,
                },
                status_code=422,
            )
        summary = "no changes" if not changes else ", ".join(_flatten(changes))
        return RedirectResponse(f"/settings?saved={quote(summary)}", status_code=303)

    return app


# Module level: annotations are resolved from module globals (from __future__ import annotations).
_basic = HTTPBasic(auto_error=False)


def _flatten(data: dict, prefix: str = "") -> list[str]:
    out = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        out += _flatten(value, path + ".") if isinstance(value, dict) else [f"{path} = {value}"]
    return out


def basic_auth(secrets: Secrets):
    """HTTP Basic auth (any username) when DASHBOARD_PASSWORD is set; open otherwise (localhost use)."""
    password = secrets.dashboard_password.get_secret_value() if secrets.dashboard_password else None

    def check(credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)]) -> None:
        if password is None:
            return
        if credentials is None or not pysecrets.compare_digest(credentials.password, password):
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Basic"})

    return check


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
